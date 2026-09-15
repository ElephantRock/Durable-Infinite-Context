from __future__ import annotations

import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Type

from simulator.retired_generation_reclamation import _sparse_copy
from storage.bidirectional_retirement_descriptor_primary import (
    BidirectionalRetirementDescriptorPrimaryStore,
)
from storage.fixed_page_primary import PAGE_SIZE
from storage.partial_tail_shrinking_retirement_descriptor_primary import (
    PartialTailShrinkingRetirementDescriptorPrimaryStore,
)
from storage.retirement_descriptor_pool import RETIREMENT_STATUS_FREE
from storage.segregated_retirement_descriptor_primary import (
    SegregatedRetirementDescriptorPrimaryStore,
)

ROOT = Path(__file__).resolve().parent.parent
WORKER = ROOT / "bidirectional_tail_unlink_worker.py"
SEGMENT_BUDGET = 2
SHRINK_FAILPOINTS = (
    "retirement_tail_unlink_staged",
    "retirement_tail_unlink_dependencies_synced",
    "committed",
    "retirement_tail_unlink_committed",
    "retirement_arena_truncated",
    "retirement_tail_unlink_synced",
)
PRECOMMIT_FAILPOINTS = {
    "retirement_tail_unlink_staged",
    "retirement_tail_unlink_dependencies_synced",
}
POSTCOMMIT_PRETRUNCATE_FAILPOINTS = {
    "committed",
    "retirement_tail_unlink_committed",
}

StoreType = Type[SegregatedRetirementDescriptorPrimaryStore]


def _worker(path: Path, *, failpoint: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(WORKER),
            "--file",
            str(path),
            "shrink",
            "--failpoint",
            failpoint,
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _require_sigkill(proc: subprocess.CompletedProcess[str], label: str) -> None:
    if proc.returncode != -signal.SIGKILL:
        raise AssertionError(f"{label} did not SIGKILL: {proc.returncode} {proc.stderr}")


def _copy_candidate_store(source: Path, destination: Path) -> None:
    _sparse_copy(source, destination)
    source_arena = BidirectionalRetirementDescriptorPrimaryStore.arena_path_for(source)
    destination_arena = BidirectionalRetirementDescriptorPrimaryStore.arena_path_for(destination)
    shutil.copyfile(source_arena, destination_arena)


def _semantic_state(
    store: BidirectionalRetirementDescriptorPrimaryStore,
    keys: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "state": store.committed_state(keys),
        "queue": store.retirement_queue_snapshot(),
    }


def _require_scan_free(recovery: dict[str, Any]) -> None:
    if int(recovery["logical_work"]) != 0:
        raise AssertionError("v0.39 recovery required logical redo")
    if int(recovery["generation_pages_scanned"]) != 0:
        raise AssertionError("v0.39 recovery scanned generation pages")
    if int(recovery["mapping_nodes_scanned"]) != 0:
        raise AssertionError("v0.39 recovery scanned radix nodes")
    if int(recovery.get("retirement_descriptors_scanned", 0)) != 0:
        raise AssertionError("v0.39 recovery scanned retirement descriptors")


def _recover_twice(
    store: BidirectionalRetirementDescriptorPrimaryStore,
) -> tuple[dict[str, Any], dict[str, Any]]:
    one = store.recover()
    two = store.recover()
    _require_scan_free(one)
    _require_scan_free(two)
    if int(two["physical_truncated_bytes"]) != 0:
        raise AssertionError("v0.39 second recovery changed primary physical length")
    if int(two["retirement_descriptor_arena_truncated_bytes"]) != 0:
        raise AssertionError("v0.39 second recovery changed descriptor arena length")
    return one, two


def _next_key(index: int) -> str:
    return f"k-{index:04d}"


def _insert_until_queue_count(
    store: SegregatedRetirementDescriptorPrimaryStore,
    *,
    index: int,
    target: int,
    cap: int = 4096,
) -> int:
    while int(store.retirement_queue_snapshot()["queue_count"]) < target:
        if index >= cap:
            raise AssertionError(f"v0.39 fixture did not reach queue count {target}")
        store.insert(_next_key(index))
        index += 1
    if int(store.retirement_queue_snapshot()["queue_count"]) != target:
        raise AssertionError("v0.39 fixture overshot requested queue count")
    return index


def _build_buried_tail_fixture(path: Path, store_cls: StoreType) -> dict[str, Any]:
    store = store_cls(str(path))
    store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=4096)
    index = _insert_until_queue_count(store, index=0, target=3)
    initial_peak = store.retirement_queue_snapshot()
    initial_pages = [int(row["descriptor_page"]) for row in initial_peak["descriptors"]]
    if initial_pages != [0, 2, 4] or int(initial_peak["descriptor_arena_pages"]) != 6:
        raise AssertionError(f"v0.39 initial descriptor layout drifted: {initial_pages}")

    while int(store.retirement_queue_snapshot()["queue_count"]) > 1:
        trace = store.reclaim_step(budget=SEGMENT_BUDGET)
        if int(trace.retirement_descriptors_scanned) != 0:
            raise AssertionError("v0.39 setup reclaim scanned descriptor history")
    one_live = store.retirement_queue_snapshot()
    if int(one_live["head_page"]) != 4 or int(one_live["descriptor_free_count"]) != 2:
        raise AssertionError("v0.39 fixture did not leave page 4 live with two reusable descriptors")

    index = _insert_until_queue_count(store, index=index, target=3)
    reused = store.retirement_queue_snapshot()
    reused_pages = [int(row["descriptor_page"]) for row in reused["descriptors"]]
    if reused_pages != [4, 2, 0] or int(reused["descriptor_free_count"]) != 0:
        raise AssertionError(f"v0.39 reuse order drifted: {reused_pages}")

    while int(store.retirement_queue_snapshot()["head_page"]) == 4:
        store.reclaim_step(budget=SEGMENT_BUDGET)
    after_tail_freed = store.retirement_queue_snapshot()
    if int(after_tail_freed["descriptor_free_head_page"]) != 4:
        raise AssertionError("v0.39 did not first free the physical tail as free-list head")

    while int(store.retirement_queue_snapshot()["head_page"]) == 2:
        store.reclaim_step(budget=SEGMENT_BUDGET)
    buried = store.retirement_queue_snapshot()
    if int(buried["queue_count"]) != 1:
        raise AssertionError("v0.39 buried-tail fixture must retain one live descriptor")
    if int(buried["descriptor_arena_pages"]) != 6:
        raise AssertionError("v0.39 buried-tail fixture arena frontier drifted")
    if int(buried["descriptor_free_count"]) != 2:
        raise AssertionError("v0.39 buried-tail fixture must contain two free descriptors")
    if int(buried["descriptor_free_head_page"]) != 2:
        raise AssertionError("v0.39 physical tail unexpectedly remained the free-list head")
    free_rows = buried["free_descriptors"]
    if [int(row["descriptor_page"]) for row in free_rows] != [2, 4]:
        raise AssertionError("v0.39 buried-tail free-list order drifted")

    tail_row = free_rows[1]
    tail_incarnation = int(tail_row["descriptor_incarnation"])
    if store_cls is BidirectionalRetirementDescriptorPrimaryStore:
        if int(tail_row.get("prev_descriptor_page", -1)) != 2:
            raise AssertionError("v0.39 physical tail lacks direct predecessor authority")
        if int(tail_row.get("prev_descriptor_incarnation", -1)) != int(
            free_rows[0]["descriptor_incarnation"]
        ):
            raise AssertionError("v0.39 physical tail predecessor tag drifted")

    return {
        "next_key_index": index,
        "keys": tuple(_next_key(i) for i in range(index)),
        "initial_peak": initial_peak,
        "one_live": one_live,
        "reused": reused,
        "after_tail_freed": after_tail_freed,
        "buried": buried,
        "tail_page": 4,
        "tail_incarnation": tail_incarnation,
    }


def _v038_control_case() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v039-v038-control-") as tmp:
        path = Path(tmp) / "primary.pages"
        fixture = _build_buried_tail_fixture(
            path, PartialTailShrinkingRetirementDescriptorPrimaryStore
        )
        store = PartialTailShrinkingRetirementDescriptorPrimaryStore(str(path))
        before = store.retirement_queue_snapshot()
        trace = store.shrink_retirement_arena_tail_step()
        after = store.retirement_queue_snapshot()
        if trace.released or before != after:
            raise AssertionError("v0.38 control unexpectedly reclaimed a buried physical tail")
        if int(trace.retirement_descriptor_preads) != 0:
            raise AssertionError("v0.38 control searched after detecting non-head free-list identity")
        return {
            "keys_inserted": int(fixture["next_key_index"]),
            "queue_before": before,
            "trace": trace.to_dict(),
            "queue_after": after,
            "retained_arena_pages": 6,
            "retained_arena_bytes": 6 * PAGE_SIZE,
        }


def _non_head_release_case() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v039-release-") as tmp:
        path = Path(tmp) / "primary.pages"
        fixture = _build_buried_tail_fixture(path, BidirectionalRetirementDescriptorPrimaryStore)
        store = BidirectionalRetirementDescriptorPrimaryStore(str(path))
        before = store.retirement_queue_snapshot()
        arena_before = store.descriptor_arena_diagnostic()
        trace = store.shrink_retirement_arena_tail_step()
        after = store.retirement_queue_snapshot()
        arena_after = store.descriptor_arena_diagnostic()

        if not trace.released or trace.tail_was_free_head:
            raise AssertionError("v0.39 did not release the non-head physical free tail")
        if int(trace.predecessor_page or -1) != 2:
            raise AssertionError("v0.39 tail unlink did not use direct predecessor page 2")
        if int(trace.retirement_descriptor_preads) != 4:
            raise AssertionError("v0.39 buried-tail unlink should read tail and predecessor only")
        if int(trace.retirement_descriptor_pwrites) != 1:
            raise AssertionError("v0.39 buried-tail unlink should rewrite one predecessor")
        if int(trace.retirement_descriptors_scanned) != 0 or int(trace.candidate_relocations) != 0:
            raise AssertionError("v0.39 candidate scanned history or relocated live state")
        if int(before["descriptor_arena_pages"]) != 6 or int(after["descriptor_arena_pages"]) != 4:
            raise AssertionError("v0.39 committed arena frontier did not shrink 6 -> 4")
        if int(arena_before["arena_file_bytes"]) != 6 * PAGE_SIZE:
            raise AssertionError("v0.39 pre-shrink physical arena length drifted")
        if int(arena_after["arena_file_bytes"]) != 4 * PAGE_SIZE:
            raise AssertionError("v0.39 post-shrink physical arena length drifted")
        if int(after["queue_count"]) != 1 or int(after["descriptor_free_count"]) != 1:
            raise AssertionError("v0.39 tail unlink changed live backlog/free count incorrectly")
        if int(after["descriptor_free_head_page"]) != 2:
            raise AssertionError("v0.39 tail unlink changed the surviving free-list head")

        return {
            "keys_inserted": int(fixture["next_key_index"]),
            "queue_before": before,
            "arena_before": arena_before,
            "trace": trace.to_dict(),
            "queue_after": after,
            "arena_after": arena_after,
            "released_arena_pages": 2,
            "released_arena_bytes": 2 * PAGE_SIZE,
            "candidate_descriptor_history_walks": 0,
            "candidate_relocations": 0,
        }


def _reuse_head_predecessor_repair_case() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v039-reuse-repair-") as tmp:
        path = Path(tmp) / "primary.pages"
        fixture = _build_buried_tail_fixture(path, BidirectionalRetirementDescriptorPrimaryStore)
        store = BidirectionalRetirementDescriptorPrimaryStore(str(path))
        index = int(fixture["next_key_index"])
        trigger = None
        trace_row = None
        while index < 4096:
            trace = store.insert(_next_key(index))
            if int(trace.retirement_descriptors_enqueued) > 0:
                trigger = index
                trace_row = trace.to_dict()
                break
            index += 1
        if trigger is None or trace_row is None:
            raise AssertionError("v0.39 reuse-repair fixture never reused free-list head")
        after = store.retirement_queue_snapshot()
        if int(after["descriptor_free_count"]) != 1 or int(after["descriptor_free_head_page"]) != 4:
            raise AssertionError("v0.39 reuse did not advance free head from page 2 to page 4")
        free_row = after["free_descriptors"][0]
        if "prev_descriptor_page" in free_row or "prev_descriptor_incarnation" in free_row:
            raise AssertionError("v0.39 new free-list head retained predecessor after head reuse")
        return {
            "reuse_trigger_key_index": trigger,
            "insert_trace": trace_row,
            "queue_after": after,
            "new_head_predecessor_cleared": True,
        }


def _identity_after_non_head_shrink_case() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v039-identity-") as tmp:
        path = Path(tmp) / "primary.pages"
        fixture = _build_buried_tail_fixture(path, BidirectionalRetirementDescriptorPrimaryStore)
        store = BidirectionalRetirementDescriptorPrimaryStore(str(path))
        stale_page = int(fixture["tail_page"])
        stale_incarnation = int(fixture["tail_incarnation"])
        store.shrink_retirement_arena_tail_step()

        stale_rejected_after_shrink = False
        try:
            store.retirement_descriptor_reference(
                stale_page,
                stale_incarnation,
                expected_status=RETIREMENT_STATUS_FREE,
            )
        except RuntimeError:
            stale_rejected_after_shrink = True
        if not stale_rejected_after_shrink:
            raise AssertionError("v0.39 committed frontier exposed truncated stale tail")

        index = int(fixture["next_key_index"])
        reused = None
        trigger = None
        while index < 8192:
            store.insert(_next_key(index))
            snapshot = store.retirement_queue_snapshot()
            rows = [row for row in snapshot["descriptors"] if int(row["descriptor_page"]) == stale_page]
            if rows:
                reused = snapshot
                trigger = index
                break
            index += 1
        if reused is None or trigger is None:
            raise AssertionError("v0.39 identity fixture did not re-extend physical tail page")
        current_row = next(
            row for row in reused["descriptors"] if int(row["descriptor_page"]) == stale_page
        )
        current_incarnation = int(current_row["descriptor_incarnation"])
        if current_incarnation <= stale_incarnation:
            raise AssertionError("v0.39 physical tail reuse did not advance global incarnation")

        stale_rejected_after_reuse = False
        try:
            store.retirement_descriptor_reference(stale_page, stale_incarnation)
        except RuntimeError:
            stale_rejected_after_reuse = True
        if not stale_rejected_after_reuse:
            raise AssertionError("v0.39 stale tail identity aliased later address reuse")
        current = store.retirement_descriptor_reference(stale_page, current_incarnation)
        return {
            "stale_page": stale_page,
            "stale_incarnation": stale_incarnation,
            "stale_rejected_after_shrink": stale_rejected_after_shrink,
            "reuse_trigger_key_index": trigger,
            "current_incarnation": current_incarnation,
            "incarnation_advanced": current_incarnation > stale_incarnation,
            "stale_rejected_after_reuse": stale_rejected_after_reuse,
            "current_identity_generation": int(current["generation"]),
            "arena_pages_after_reuse": int(reused["descriptor_arena_pages"]),
        }


def _crash_matrix() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v039-crash-") as tmp:
        directory = Path(tmp)
        base_path = directory / "base.pages"
        fixture = _build_buried_tail_fixture(
            base_path, BidirectionalRetirementDescriptorPrimaryStore
        )
        keys = tuple(fixture["keys"])
        base = BidirectionalRetirementDescriptorPrimaryStore(str(base_path))
        pre = _semantic_state(base, keys)

        clean_path = directory / "clean.pages"
        _copy_candidate_store(base_path, clean_path)
        clean = BidirectionalRetirementDescriptorPrimaryStore(str(clean_path))
        clean_trace = clean.shrink_retirement_arena_tail_step()
        if not clean_trace.released:
            raise AssertionError("v0.39 clean crash oracle did not release buried tail")
        post = _semantic_state(clean, keys)

        cases: list[dict[str, Any]] = []
        for failpoint in SHRINK_FAILPOINTS:
            crash_path = directory / f"crash-{failpoint}.pages"
            _copy_candidate_store(base_path, crash_path)
            proc = _worker(crash_path, failpoint=failpoint)
            _require_sigkill(proc, failpoint)

            crashed = BidirectionalRetirementDescriptorPrimaryStore(str(crash_path))
            before_recovery = crashed.descriptor_arena_diagnostic()
            one, two = _recover_twice(crashed)
            recovered = _semantic_state(crashed, keys)
            expected = pre if failpoint in PRECOMMIT_FAILPOINTS else post
            if recovered != expected:
                raise AssertionError(f"v0.39 {failpoint} recovered wrong committed state")

            expected_truncated = (
                2 * PAGE_SIZE if failpoint in POSTCOMMIT_PRETRUNCATE_FAILPOINTS else 0
            )
            observed_truncated = int(one["retirement_descriptor_arena_truncated_bytes"])
            if observed_truncated != expected_truncated:
                raise AssertionError(
                    f"v0.39 {failpoint} recovery truncated {observed_truncated}, "
                    f"expected {expected_truncated}"
                )
            cases.append(
                {
                    "failpoint": failpoint,
                    "expected_state": "pre" if failpoint in PRECOMMIT_FAILPOINTS else "post",
                    "arena_before_recovery": before_recovery,
                    "first_recovery": one,
                    "second_recovery": two,
                    "exact_committed_state_match": recovered == expected,
                    "recovery_scan_free": True,
                    "second_recovery_idempotent": int(
                        two["retirement_descriptor_arena_truncated_bytes"]
                    ) == 0,
                }
            )

        return {
            "case_count": len(cases),
            "failpoints": list(SHRINK_FAILPOINTS),
            "pre_state": pre,
            "clean_post_state": post,
            "clean_trace": clean_trace.to_dict(),
            "cases": cases,
            "all_exact_committed_state_match": all(
                bool(row["exact_committed_state_match"]) for row in cases
            ),
            "all_recovery_scan_free": all(bool(row["recovery_scan_free"]) for row in cases),
            "all_second_recovery_idempotent": all(
                bool(row["second_recovery_idempotent"]) for row in cases
            ),
        }


def run_bidirectional_tail_unlink_experiment() -> dict[str, Any]:
    control = _v038_control_case()
    release = _non_head_release_case()
    reuse_repair = _reuse_head_predecessor_repair_case()
    identity = _identity_after_non_head_shrink_case()
    crashes = _crash_matrix()
    return {
        "experiment": "v0.39-bidirectional-free-tail-unlink",
        "survived": True,
        "v038_control": control,
        "non_head_release": release,
        "reuse_head_predecessor_repair": reuse_repair,
        "identity_after_non_head_shrink": identity,
        "crash_matrix": crashes,
        "candidate_descriptor_history_walks": 0,
        "candidate_relocations": 0,
        "claim": (
            "A directly addressed physical tail descriptor that is FREE but buried behind "
            "another free-list node can be unlinked with bounded current-topology work: "
            "the tested non-head case uses two dual-copy reads and one neighbor rewrite, "
            "then publishes a two-page-shorter authoritative arena frontier before physical "
            "truncate. Recovery remains scan-free and stale tagged identities remain rejected."
        ),
        "nonclaims": [
            "No free-list/history traversal is counted as candidate work; diagnostic snapshots may traverse chains.",
            "The experiment establishes bounded current-topology maintenance, not zero metadata writes on free/reuse.",
            "The tested shrink releases one physical descriptor pair per maintenance step.",
            "File-length truncation is not a claim about filesystem allocated-block reclamation.",
            "Process SIGKILL evidence is not a hardware power-loss or torn-write proof.",
            "Descriptor incarnation remains finite uint64 authority.",
        ],
    }
