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
from storage.live_tail_evacuation_retirement_descriptor_primary import (
    LiveTailEvacuationRetirementDescriptorPrimaryStore,
)
from storage.retirement_descriptor_pool import RETIREMENT_STATUS_QUEUED
from storage.segregated_retirement_descriptor_primary import (
    SegregatedRetirementDescriptorPrimaryStore,
)

ROOT = Path(__file__).resolve().parent.parent
WORKER = ROOT / "live_tail_evacuation_worker.py"
SEGMENT_BUDGET = 2
SETUP_RECLAIM_BUDGET = 1_000_000
TARGET_DESCRIPTOR_COUNTS = (3, 4, 5, 6)
EVACUATION_FAILPOINTS = (
    "retirement_live_tail_destination_staged",
    "retirement_live_tail_predecessor_staged",
    "retirement_live_tail_dependencies_synced",
    "committed",
    "retirement_live_tail_relocation_committed",
    "retirement_arena_truncated",
    "retirement_live_tail_relocation_synced",
)
PRECOMMIT_FAILPOINTS = {
    "retirement_live_tail_destination_staged",
    "retirement_live_tail_predecessor_staged",
    "retirement_live_tail_dependencies_synced",
}
POSTCOMMIT_PRETRUNCATE_FAILPOINTS = {
    "committed",
    "retirement_live_tail_relocation_committed",
}

StoreType = Type[SegregatedRetirementDescriptorPrimaryStore]


def _next_key(index: int) -> str:
    return f"k-{index:04d}"


def _insert_until_queue_count(
    store: SegregatedRetirementDescriptorPrimaryStore,
    *,
    index: int,
    target: int,
    cap: int = 65536,
) -> int:
    while int(store.retirement_queue_snapshot()["queue_count"]) < target:
        if index >= cap:
            raise AssertionError(f"v0.40 fixture did not reach queue count {target}")
        store.insert(_next_key(index))
        index += 1
    if int(store.retirement_queue_snapshot()["queue_count"]) != target:
        raise AssertionError("v0.40 fixture overshot requested queue count")
    return index


def _free_first_descriptor(
    store: SegregatedRetirementDescriptorPrimaryStore,
    *,
    budget: int,
) -> None:
    snapshot = store.retirement_queue_snapshot()
    if int(snapshot["head_page"]) != 0:
        raise AssertionError("v0.40 fixture does not begin at descriptor page 0")
    while int(store.retirement_queue_snapshot()["head_page"]) == 0:
        trace = store.reclaim_step(budget=budget)
        if int(trace.retirement_descriptors_scanned) != 0:
            raise AssertionError("v0.40 setup reclaim scanned descriptor history")


def _build_live_tail_fixture(path: Path, store_cls: StoreType) -> dict[str, Any]:
    store = store_cls(str(path))
    store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=4096)
    index = _insert_until_queue_count(store, index=0, target=3)
    initial = store.retirement_queue_snapshot()
    pages = [int(row["descriptor_page"]) for row in initial["descriptors"]]
    if pages != [0, 2, 4] or int(initial["descriptor_arena_pages"]) != 6:
        raise AssertionError(f"v0.40 initial descriptor layout drifted: {pages}")

    _free_first_descriptor(store, budget=SEGMENT_BUDGET)
    ready = store.retirement_queue_snapshot()
    if [int(row["descriptor_page"]) for row in ready["descriptors"]] != [2, 4]:
        raise AssertionError("v0.40 live queue is not [2,4]")
    if int(ready["queue_count"]) != 2:
        raise AssertionError("v0.40 live-tail fixture must retain two queued descriptors")
    if int(ready["tail_page"]) != 4 or int(ready["descriptor_arena_pages"]) != 6:
        raise AssertionError("v0.40 page 4 is not the live physical queue tail")
    if int(ready["descriptor_free_count"]) != 1:
        raise AssertionError("v0.40 fixture must expose one lower free descriptor")
    if int(ready["descriptor_free_head_page"]) != 0:
        raise AssertionError("v0.40 lower relocation destination is not free head page 0")
    if store_cls is LiveTailEvacuationRetirementDescriptorPrimaryStore:
        if int(ready["tail_predecessor_page"]) != 2:
            raise AssertionError("v0.40 queue-tail predecessor authority drifted")
        if int(ready["tail_predecessor_incarnation"]) != int(
            ready["descriptors"][0]["descriptor_incarnation"]
        ):
            raise AssertionError("v0.40 queue-tail predecessor tag drifted")

    return {
        "next_key_index": index,
        "keys": tuple(_next_key(i) for i in range(index)),
        "initial": initial,
        "ready": ready,
        "tail_page": 4,
        "tail_incarnation": int(ready["tail_incarnation"]),
        "destination_page": 0,
        "destination_incarnation": int(ready["free_descriptors"][0]["descriptor_incarnation"]),
    }


def _v039_control_case() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v040-v039-control-") as tmp:
        path = Path(tmp) / "primary.pages"
        fixture = _build_live_tail_fixture(path, BidirectionalRetirementDescriptorPrimaryStore)
        store = BidirectionalRetirementDescriptorPrimaryStore(str(path))
        before = store.retirement_queue_snapshot()
        trace = store.shrink_retirement_arena_tail_step()
        after = store.retirement_queue_snapshot()
        if trace.released or before != after:
            raise AssertionError("v0.39 control unexpectedly moved a live physical tail")
        if int(trace.retirement_descriptor_preads) != 2:
            raise AssertionError("v0.39 control should read only the physical tail copies")
        if bool(trace.tail_was_free):
            raise AssertionError("v0.39 control no longer exercises a live tail")
        return {
            "keys_inserted": int(fixture["next_key_index"]),
            "queue_before": before,
            "trace": trace.to_dict(),
            "queue_after": after,
            "retained_arena_pages": 6,
            "retained_arena_bytes": 6 * PAGE_SIZE,
        }


def _candidate_release_case() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v040-release-") as tmp:
        path = Path(tmp) / "primary.pages"
        fixture = _build_live_tail_fixture(
            path, LiveTailEvacuationRetirementDescriptorPrimaryStore
        )
        store = LiveTailEvacuationRetirementDescriptorPrimaryStore(str(path))
        before = store.retirement_queue_snapshot()
        arena_before = store.descriptor_arena_diagnostic()
        stale_tail_page = int(fixture["tail_page"])
        stale_tail_incarnation = int(fixture["tail_incarnation"])
        stale_destination_page = int(fixture["destination_page"])
        stale_destination_incarnation = int(fixture["destination_incarnation"])

        trace = store.evacuate_live_retirement_arena_tail_step()
        after = store.retirement_queue_snapshot()
        arena_after = store.descriptor_arena_diagnostic()

        if not trace.released or not trace.tail_was_queued or not trace.tail_was_queue_tail:
            raise AssertionError("v0.40 did not evacuate the live physical queue tail")
        if int(trace.predecessor_page or -1) != 2 or int(trace.destination_page or -1) != 0:
            raise AssertionError("v0.40 relocation did not use named predecessor/destination")
        if int(trace.retirement_descriptor_preads) != 6:
            raise AssertionError("v0.40 relocation should perform exactly three dual-copy reads")
        if int(trace.retirement_descriptor_pwrites) != 2:
            raise AssertionError("v0.40 relocation should perform exactly two descriptor rewrites")
        if int(trace.retirement_descriptors_scanned) != 0:
            raise AssertionError("v0.40 relocation scanned descriptor history")
        if int(trace.live_descriptor_relocations) != 1:
            raise AssertionError("v0.40 relocation count drifted")
        if int(before["descriptor_arena_pages"]) != 6 or int(after["descriptor_arena_pages"]) != 4:
            raise AssertionError("v0.40 committed arena frontier did not shrink 6 -> 4")
        if int(arena_before["arena_file_bytes"]) != 6 * PAGE_SIZE:
            raise AssertionError("v0.40 pre-relocation physical arena length drifted")
        if int(arena_after["arena_file_bytes"]) != 4 * PAGE_SIZE:
            raise AssertionError("v0.40 post-relocation physical arena length drifted")
        if [int(row["descriptor_page"]) for row in after["descriptors"]] != [2, 0]:
            raise AssertionError("v0.40 relocated queue is not [2,0]")
        if int(after["queue_count"]) != 2 or int(after["descriptor_free_count"]) != 0:
            raise AssertionError("v0.40 relocation changed queue/free counts incorrectly")
        if int(after["tail_page"]) != 0 or int(after["tail_predecessor_page"]) != 2:
            raise AssertionError("v0.40 committed queue-tail authority drifted")

        stale_tail_rejected = False
        try:
            store.retirement_descriptor_reference(
                stale_tail_page,
                stale_tail_incarnation,
                expected_status=RETIREMENT_STATUS_QUEUED,
            )
        except RuntimeError:
            stale_tail_rejected = True
        if not stale_tail_rejected:
            raise AssertionError("v0.40 truncated live-tail identity remained visible")

        stale_destination_rejected = False
        try:
            store.retirement_descriptor_reference(
                stale_destination_page,
                stale_destination_incarnation,
                expected_status=RETIREMENT_STATUS_QUEUED,
            )
        except RuntimeError:
            stale_destination_rejected = True
        if not stale_destination_rejected:
            raise AssertionError("v0.40 old FREE destination identity aliased relocated live state")

        current = store.retirement_descriptor_reference(
            int(after["tail_page"]),
            int(after["tail_incarnation"]),
            expected_status=RETIREMENT_STATUS_QUEUED,
        )
        return {
            "keys_inserted": int(fixture["next_key_index"]),
            "queue_before": before,
            "arena_before": arena_before,
            "trace": trace.to_dict(),
            "queue_after": after,
            "arena_after": arena_after,
            "stale_tail_rejected": stale_tail_rejected,
            "stale_destination_rejected": stale_destination_rejected,
            "current_destination_generation": int(current["generation"]),
            "released_arena_pages": 2,
            "released_arena_bytes": 2 * PAGE_SIZE,
        }


def _copy_candidate_store(source: Path, destination: Path) -> None:
    _sparse_copy(source, destination)
    source_arena = LiveTailEvacuationRetirementDescriptorPrimaryStore.arena_path_for(source)
    destination_arena = LiveTailEvacuationRetirementDescriptorPrimaryStore.arena_path_for(destination)
    shutil.copyfile(source_arena, destination_arena)


def _semantic_state(
    store: LiveTailEvacuationRetirementDescriptorPrimaryStore,
    keys: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "state": store.committed_state(keys),
        "queue": store.retirement_queue_snapshot(),
    }


def _require_scan_free(recovery: dict[str, Any]) -> None:
    if int(recovery["logical_work"]) != 0:
        raise AssertionError("v0.40 recovery required logical redo")
    if int(recovery["generation_pages_scanned"]) != 0:
        raise AssertionError("v0.40 recovery scanned generation pages")
    if int(recovery["mapping_nodes_scanned"]) != 0:
        raise AssertionError("v0.40 recovery scanned radix nodes")
    if int(recovery.get("retirement_descriptors_scanned", 0)) != 0:
        raise AssertionError("v0.40 recovery scanned retirement descriptors")


def _recover_twice(
    store: LiveTailEvacuationRetirementDescriptorPrimaryStore,
) -> tuple[dict[str, Any], dict[str, Any]]:
    one = store.recover()
    two = store.recover()
    _require_scan_free(one)
    _require_scan_free(two)
    if int(two["physical_truncated_bytes"]) != 0:
        raise AssertionError("v0.40 second recovery changed primary physical length")
    if int(two["retirement_descriptor_arena_truncated_bytes"]) != 0:
        raise AssertionError("v0.40 second recovery changed descriptor arena length")
    return one, two


def _worker(path: Path, *, failpoint: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(WORKER),
            "--file",
            str(path),
            "evacuate",
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


def _crash_matrix() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v040-crash-") as tmp:
        directory = Path(tmp)
        base_path = directory / "base.pages"
        fixture = _build_live_tail_fixture(
            base_path, LiveTailEvacuationRetirementDescriptorPrimaryStore
        )
        keys = tuple(fixture["keys"])
        base = LiveTailEvacuationRetirementDescriptorPrimaryStore(str(base_path))
        pre = _semantic_state(base, keys)

        clean_path = directory / "clean.pages"
        _copy_candidate_store(base_path, clean_path)
        clean = LiveTailEvacuationRetirementDescriptorPrimaryStore(str(clean_path))
        clean_trace = clean.evacuate_live_retirement_arena_tail_step()
        if not clean_trace.released:
            raise AssertionError("v0.40 clean crash oracle did not evacuate live tail")
        post = _semantic_state(clean, keys)

        cases: list[dict[str, Any]] = []
        for failpoint in EVACUATION_FAILPOINTS:
            crash_path = directory / f"crash-{failpoint}.pages"
            _copy_candidate_store(base_path, crash_path)
            proc = _worker(crash_path, failpoint=failpoint)
            _require_sigkill(proc, failpoint)

            crashed = LiveTailEvacuationRetirementDescriptorPrimaryStore(str(crash_path))
            before_recovery = crashed.descriptor_arena_diagnostic()
            one, two = _recover_twice(crashed)
            recovered = _semantic_state(crashed, keys)
            expected = pre if failpoint in PRECOMMIT_FAILPOINTS else post
            if recovered != expected:
                raise AssertionError(f"v0.40 {failpoint} recovered wrong committed state")

            expected_truncated = (
                2 * PAGE_SIZE if failpoint in POSTCOMMIT_PRETRUNCATE_FAILPOINTS else 0
            )
            observed_truncated = int(one["retirement_descriptor_arena_truncated_bytes"])
            if observed_truncated != expected_truncated:
                raise AssertionError(
                    f"v0.40 {failpoint} recovery truncated {observed_truncated}, "
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
            "failpoints": list(EVACUATION_FAILPOINTS),
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


def _scaling_case(target_count: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"dic-v040-scale-{target_count}-") as tmp:
        path = Path(tmp) / "primary.pages"
        store = LiveTailEvacuationRetirementDescriptorPrimaryStore(str(path))
        store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=4096)
        index = _insert_until_queue_count(store, index=0, target=target_count)
        initial = store.retirement_queue_snapshot()
        expected_pages = list(range(0, 2 * target_count, 2))
        observed_pages = [int(row["descriptor_page"]) for row in initial["descriptors"]]
        if observed_pages != expected_pages:
            raise AssertionError(f"v0.40 scaling initial layout drifted: {observed_pages}")

        _free_first_descriptor(store, budget=SETUP_RECLAIM_BUDGET)
        ready = store.retirement_queue_snapshot()
        physical_tail = expected_pages[-1]
        predecessor = expected_pages[-2]
        if int(ready["descriptor_free_head_page"]) != 0:
            raise AssertionError("v0.40 scaling destination is not lower free head page 0")
        if int(ready["tail_page"]) != physical_tail:
            raise AssertionError("v0.40 scaling physical tail is not logical queue tail")
        if int(ready["tail_predecessor_page"]) != predecessor:
            raise AssertionError("v0.40 scaling tail predecessor authority drifted")
        if int(ready["queue_count"]) != target_count - 1:
            raise AssertionError("v0.40 scaling live queue depth drifted")

        trace = store.evacuate_live_retirement_arena_tail_step()
        after = store.retirement_queue_snapshot()
        if not trace.released:
            raise AssertionError("v0.40 scaling did not release physical tail pair")
        if int(trace.retirement_descriptor_preads) != 6:
            raise AssertionError("v0.40 scaling relocation reads depend on queue depth")
        if int(trace.retirement_descriptor_pwrites) != 2:
            raise AssertionError("v0.40 scaling relocation writes depend on queue depth")
        if int(trace.retirement_descriptors_scanned) != 0:
            raise AssertionError("v0.40 scaling relocation scanned queue/history")
        if int(trace.live_descriptor_relocations) != 1:
            raise AssertionError("v0.40 scaling relocation count drifted")
        if int(after["descriptor_arena_pages"]) != 2 * (target_count - 1):
            raise AssertionError("v0.40 scaling frontier did not shrink by one pair")
        if int(after["queue_count"]) != target_count - 1:
            raise AssertionError("v0.40 scaling relocation changed live queue depth")

        return {
            "target_descriptor_count": target_count,
            "keys_inserted": index,
            "live_queue_depth_before": target_count - 1,
            "physical_tail_page": physical_tail,
            "predecessor_page": predecessor,
            "destination_page": 0,
            "arena_pages_before": 2 * target_count,
            "arena_pages_after": 2 * (target_count - 1),
            "retirement_descriptor_preads": int(trace.retirement_descriptor_preads),
            "retirement_descriptor_pwrites": int(trace.retirement_descriptor_pwrites),
            "retirement_descriptors_scanned": int(trace.retirement_descriptors_scanned),
            "live_descriptor_relocations": int(trace.live_descriptor_relocations),
        }


def _scaling() -> dict[str, Any]:
    rows = [_scaling_case(count) for count in TARGET_DESCRIPTOR_COUNTS]
    return {
        "target_descriptor_counts": list(TARGET_DESCRIPTOR_COUNTS),
        "rows": rows,
        "all_constant_relocation_work": all(
            int(row["retirement_descriptor_preads"]) == 6
            and int(row["retirement_descriptor_pwrites"]) == 2
            and int(row["retirement_descriptors_scanned"]) == 0
            and int(row["live_descriptor_relocations"]) == 1
            for row in rows
        ),
    }


def run_live_tail_evacuation_experiment() -> dict[str, Any]:
    control = _v039_control_case()
    candidate = _candidate_release_case()
    crashes = _crash_matrix()
    scaling = _scaling()
    return {
        "experiment": "v0.40-live-tail-evacuation",
        "survived": True,
        "v039_control": control,
        "live_tail_evacuation": candidate,
        "crash_matrix": crashes,
        "queue_depth_scaling": scaling,
        "claim": (
            "When the physical descriptor tail is also the logical queue tail and the current "
            "free-list head is a lower pair, one tagged queue-tail predecessor is sufficient "
            "to evacuate the live tail with three direct dual-copy reads, two descriptor "
            "rewrites, zero queue/history scans, and one live relocation before publishing a "
            "two-page-shorter authoritative arena frontier."
        ),
        "nonclaims": [
            "The physical tail must also be the logical queue tail in this experiment.",
            "The relocation destination must be the current lower free-list head.",
            "A live physical tail in the middle of the queue remains outside this claim.",
            "File-length truncation is not a claim about filesystem allocated-block reclamation.",
            "Process SIGKILL evidence is not a hardware power-loss or torn-write proof.",
            "Descriptor page/incarnation authority remains finite-width.",
        ],
    }
