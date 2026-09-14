from __future__ import annotations

import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Type

from simulator.retired_generation_reclamation import _sparse_copy
from storage.fixed_page_primary import PAGE_SIZE
from storage.partial_tail_shrinking_retirement_descriptor_primary import (
    PartialTailShrinkingRetirementDescriptorPrimaryStore,
)
from storage.retirement_descriptor_pool import RETIREMENT_STATUS_FREE
from storage.segregated_retirement_descriptor_primary import (
    SegregatedRetirementDescriptorPrimaryStore,
)

ROOT = Path(__file__).resolve().parent.parent
WORKER = ROOT / "partial_tail_shrink_worker.py"
SEGMENT_BUDGET = 2
SHRINK_FAILPOINTS = (
    "retirement_tail_release_staged",
    "committed",
    "retirement_tail_release_committed",
    "retirement_arena_truncated",
    "retirement_tail_release_synced",
)

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
    source_arena = PartialTailShrinkingRetirementDescriptorPrimaryStore.arena_path_for(source)
    destination_arena = PartialTailShrinkingRetirementDescriptorPrimaryStore.arena_path_for(destination)
    shutil.copyfile(source_arena, destination_arena)


def _semantic_state(
    store: PartialTailShrinkingRetirementDescriptorPrimaryStore,
    keys: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "state": store.committed_state(keys),
        "queue": store.retirement_queue_snapshot(),
    }


def _require_scan_free(recovery: dict[str, Any]) -> None:
    if int(recovery["logical_work"]) != 0:
        raise AssertionError("v0.38 recovery required logical redo")
    if int(recovery["generation_pages_scanned"]) != 0:
        raise AssertionError("v0.38 recovery scanned generation pages")
    if int(recovery["mapping_nodes_scanned"]) != 0:
        raise AssertionError("v0.38 recovery scanned radix nodes")
    if int(recovery.get("retirement_descriptors_scanned", 0)) != 0:
        raise AssertionError("v0.38 recovery scanned retirement descriptors")


def _recover_twice(
    store: PartialTailShrinkingRetirementDescriptorPrimaryStore,
) -> tuple[dict[str, Any], dict[str, Any]]:
    one = store.recover()
    two = store.recover()
    _require_scan_free(one)
    _require_scan_free(two)
    if int(two["physical_truncated_bytes"]) != 0:
        raise AssertionError("v0.38 second recovery changed primary physical length")
    if int(two["retirement_descriptor_arena_truncated_bytes"]) != 0:
        raise AssertionError("v0.38 second recovery changed descriptor arena length")
    return one, two


def _next_key(index: int) -> str:
    return f"k-{index:04d}"


def _insert_until_queue_count(
    store: SegregatedRetirementDescriptorPrimaryStore,
    *,
    index: int,
    target: int,
    cap: int = 1024,
) -> int:
    while int(store.retirement_queue_snapshot()["queue_count"]) < target:
        if index >= cap:
            raise AssertionError(f"v0.38 fixture did not reach queue count {target}")
        store.insert(_next_key(index))
        index += 1
    if int(store.retirement_queue_snapshot()["queue_count"]) != target:
        raise AssertionError("v0.38 fixture overshot requested queue count")
    return index


def _build_aligned_tail_fixture(
    path: Path,
    store_cls: StoreType,
) -> dict[str, Any]:
    store = store_cls(str(path))
    store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=4096)
    index = _insert_until_queue_count(store, index=0, target=3)
    initial_peak = store.retirement_queue_snapshot()
    if int(initial_peak["descriptor_arena_pages"]) != 6:
        raise AssertionError("v0.38 fixture did not allocate a three-pair arena")
    initial_pages = [int(row["descriptor_page"]) for row in initial_peak["descriptors"]]
    if initial_pages != [0, 2, 4]:
        raise AssertionError(f"v0.38 fixture allocation order drifted: {initial_pages}")

    while int(store.retirement_queue_snapshot()["queue_count"]) > 1:
        trace = store.reclaim_step(budget=SEGMENT_BUDGET)
        if int(trace.retirement_descriptors_scanned) != 0:
            raise AssertionError("v0.38 setup reclaim scanned descriptor history")
    one_live = store.retirement_queue_snapshot()
    if int(one_live["head_page"]) != 4 or int(one_live["descriptor_free_count"]) != 2:
        raise AssertionError("v0.38 fixture did not leave page 4 live with two reusable descriptors")

    index = _insert_until_queue_count(store, index=index, target=3)
    reused = store.retirement_queue_snapshot()
    reused_pages = [int(row["descriptor_page"]) for row in reused["descriptors"]]
    if reused_pages != [4, 2, 0]:
        raise AssertionError(f"v0.38 fixture did not produce reuse order [4,2,0]: {reused_pages}")
    if int(reused["descriptor_free_count"]) != 0 or int(reused["descriptor_arena_pages"]) != 6:
        raise AssertionError("v0.38 fixture reuse did not consume the two free descriptors")

    stale_tail_page = int(reused["head_page"])
    stale_tail_incarnation = int(reused["head_incarnation"])
    while int(store.retirement_queue_snapshot()["head_page"]) == stale_tail_page:
        trace = store.reclaim_step(budget=SEGMENT_BUDGET)
        if int(trace.retirement_descriptors_scanned) != 0:
            raise AssertionError("v0.38 setup reclaim scanned descriptor history")

    aligned = store.retirement_queue_snapshot()
    tail_base = int(aligned["descriptor_arena_pages"]) - 2
    if int(aligned["queue_count"]) <= 0:
        raise AssertionError("v0.38 aligned fixture lost all live queue state")
    if int(aligned["descriptor_arena_pages"]) != 6 or tail_base != 4:
        raise AssertionError("v0.38 aligned fixture arena frontier drifted")
    if int(aligned["descriptor_free_count"]) != 1:
        raise AssertionError("v0.38 aligned fixture must have exactly one free descriptor")
    if int(aligned["descriptor_free_head_page"]) != tail_base:
        raise AssertionError("v0.38 aligned fixture free-list head is not the physical tail")
    if int(aligned["descriptor_free_head_incarnation"]) != stale_tail_incarnation:
        raise AssertionError("v0.38 aligned fixture tail incarnation drifted")

    return {
        "next_key_index": index,
        "keys": tuple(_next_key(i) for i in range(index)),
        "initial_peak": initial_peak,
        "after_two_dequeues": one_live,
        "after_two_reuses": reused,
        "aligned": aligned,
        "stale_tail_page": stale_tail_page,
        "stale_tail_incarnation": stale_tail_incarnation,
    }


def _control_case() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v038-control-") as tmp:
        path = Path(tmp) / "primary.pages"
        fixture = _build_aligned_tail_fixture(path, SegregatedRetirementDescriptorPrimaryStore)
        store = SegregatedRetirementDescriptorPrimaryStore(str(path))
        queue = store.retirement_queue_snapshot()
        arena = store.descriptor_arena_diagnostic()
        if int(queue["queue_count"]) <= 0:
            raise AssertionError("v0.38 control lost live backlog")
        if int(queue["descriptor_arena_pages"]) != 6 or int(arena["arena_file_bytes"]) != 6 * PAGE_SIZE:
            raise AssertionError("v0.37 control did not retain the aligned tail pair")
        return {
            "queue": queue,
            "arena": arena,
            "keys_inserted": int(fixture["next_key_index"]),
            "retained_arena_pages": 6,
            "retained_arena_bytes": 6 * PAGE_SIZE,
        }


def _nonaligned_noop_case() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v038-nonaligned-") as tmp:
        path = Path(tmp) / "primary.pages"
        store = PartialTailShrinkingRetirementDescriptorPrimaryStore(str(path))
        store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=4096)
        _insert_until_queue_count(store, index=0, target=3)
        while int(store.retirement_queue_snapshot()["queue_count"]) == 3:
            store.reclaim_step(budget=SEGMENT_BUDGET)
        before = store.retirement_queue_snapshot()
        if int(before["descriptor_free_head_page"]) == int(before["descriptor_arena_pages"]) - 2:
            raise AssertionError("v0.38 non-aligned fixture accidentally aligned free head and tail")
        trace = store.shrink_retirement_arena_tail_step()
        after = store.retirement_queue_snapshot()
        if trace.released:
            raise AssertionError("v0.38 non-aligned maintenance step released capacity by searching")
        if before != after:
            raise AssertionError("v0.38 non-aligned no-op changed committed descriptor state")
        if int(trace.retirement_descriptor_preads) != 0 or int(trace.retirement_descriptors_scanned) != 0:
            raise AssertionError("v0.38 non-aligned no-op searched descriptor storage")
        return {
            "queue_before": before,
            "trace": trace.to_dict(),
            "queue_after": after,
        }


def _live_backlog_release_case() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v038-live-release-") as tmp:
        path = Path(tmp) / "primary.pages"
        fixture = _build_aligned_tail_fixture(path, PartialTailShrinkingRetirementDescriptorPrimaryStore)
        store = PartialTailShrinkingRetirementDescriptorPrimaryStore(str(path))
        before = store.retirement_queue_snapshot()
        arena_before = store.descriptor_arena_diagnostic()
        trace = store.shrink_retirement_arena_tail_step()
        after = store.retirement_queue_snapshot()
        arena_after = store.descriptor_arena_diagnostic()
        if not trace.released:
            raise AssertionError("v0.38 aligned maintenance step did not release the tail pair")
        if int(trace.retirement_arena_pages_released) != 2 or int(trace.retirement_arena_bytes_released) != 2 * PAGE_SIZE:
            raise AssertionError("v0.38 aligned maintenance released the wrong capacity")
        if int(after["queue_count"]) <= 0:
            raise AssertionError("v0.38 partial shrink drained the live queue")
        if int(before["descriptor_arena_pages"]) != 6 or int(after["descriptor_arena_pages"]) != 4:
            raise AssertionError("v0.38 committed arena frontier did not shrink 6 -> 4 pages")
        if int(arena_before["arena_file_bytes"]) != 6 * PAGE_SIZE or int(arena_after["arena_file_bytes"]) != 4 * PAGE_SIZE:
            raise AssertionError("v0.38 physical sidecar length did not shrink 6 -> 4 pages")
        if int(trace.retirement_descriptor_preads) != 2:
            raise AssertionError("v0.38 candidate should read exactly one dual-copy free descriptor")
        if int(trace.retirement_descriptor_pwrites) != 0 or int(trace.retirement_descriptors_scanned) != 0:
            raise AssertionError("v0.38 candidate rewrote/scanned descriptor history")
        if int(trace.candidate_relocations) != 0:
            raise AssertionError("v0.38 candidate relocated a live descriptor")
        return {
            "keys_inserted": int(fixture["next_key_index"]),
            "queue_before": before,
            "arena_before": arena_before,
            "trace": trace.to_dict(),
            "queue_after": after,
            "arena_after": arena_after,
            "live_backlog_preserved": int(after["queue_count"]) > 0,
            "released_arena_pages": 2,
            "released_arena_bytes": 2 * PAGE_SIZE,
            "candidate_descriptor_history_walks": 0,
            "candidate_relocations": 0,
        }


def _identity_after_partial_shrink_case() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v038-identity-") as tmp:
        path = Path(tmp) / "primary.pages"
        fixture = _build_aligned_tail_fixture(path, PartialTailShrinkingRetirementDescriptorPrimaryStore)
        store = PartialTailShrinkingRetirementDescriptorPrimaryStore(str(path))
        stale_page = int(fixture["stale_tail_page"])
        stale_incarnation = int(fixture["stale_tail_incarnation"])
        store.shrink_retirement_arena_tail_step()

        stale_rejected_after_shrink = False
        try:
            store.retirement_descriptor_reference(stale_page, stale_incarnation, expected_status=RETIREMENT_STATUS_FREE)
        except RuntimeError:
            stale_rejected_after_shrink = True
        if not stale_rejected_after_shrink:
            raise AssertionError("v0.38 committed frontier exposed a truncated stale descriptor")

        index = int(fixture["next_key_index"])
        trigger_index = None
        reused = None
        while index < 2048:
            trace = store.insert(_next_key(index))
            if trace.retirement_descriptors_enqueued:
                trigger_index = index
                reused = store.retirement_queue_snapshot()
                break
            index += 1
        if trigger_index is None or reused is None:
            raise AssertionError("v0.38 identity fixture did not re-extend descriptor arena")
        rows = [row for row in reused["descriptors"] if int(row["descriptor_page"]) == stale_page]
        if len(rows) != 1:
            raise AssertionError("v0.38 identity fixture did not reuse the truncated physical tail page")
        current_incarnation = int(rows[0]["descriptor_incarnation"])
        if current_incarnation <= stale_incarnation:
            raise AssertionError("v0.38 tail address reuse did not advance incarnation")

        stale_rejected_after_reuse = False
        try:
            store.retirement_descriptor_reference(stale_page, stale_incarnation)
        except RuntimeError:
            stale_rejected_after_reuse = True
        if not stale_rejected_after_reuse:
            raise AssertionError("v0.38 stale pre-shrink identity aliased reused tail address")
        current = store.retirement_descriptor_reference(stale_page, current_incarnation)
        return {
            "stale_page": stale_page,
            "stale_incarnation": stale_incarnation,
            "stale_rejected_after_shrink": stale_rejected_after_shrink,
            "reuse_trigger_key_index": trigger_index,
            "current_incarnation": current_incarnation,
            "incarnation_advanced": current_incarnation > stale_incarnation,
            "stale_rejected_after_reuse": stale_rejected_after_reuse,
            "current_identity_generation": int(current["generation"]),
            "arena_pages_after_reuse": int(reused["descriptor_arena_pages"]),
        }


def _crash_matrix() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v038-crash-") as tmp:
        directory = Path(tmp)
        base_path = directory / "base.pages"
        fixture = _build_aligned_tail_fixture(base_path, PartialTailShrinkingRetirementDescriptorPrimaryStore)
        keys = tuple(fixture["keys"])
        base = PartialTailShrinkingRetirementDescriptorPrimaryStore(str(base_path))
        pre = _semantic_state(base, keys)
        stale_page = int(fixture["stale_tail_page"])
        stale_incarnation = int(fixture["stale_tail_incarnation"])

        clean_path = directory / "clean.pages"
        _copy_candidate_store(base_path, clean_path)
        clean = PartialTailShrinkingRetirementDescriptorPrimaryStore(str(clean_path))
        clean_trace = clean.shrink_retirement_arena_tail_step()
        post = _semantic_state(clean, keys)
        if int(clean_trace.retirement_arena_pages_released) != 2:
            raise AssertionError("clean v0.38 shrink did not release one descriptor pair")

        rows: list[dict[str, Any]] = []
        for failpoint in SHRINK_FAILPOINTS:
            crash_path = directory / f"{failpoint}.pages"
            _copy_candidate_store(base_path, crash_path)
            proc = _worker(crash_path, failpoint=failpoint)
            _require_sigkill(proc, f"v0.38 shrink failpoint {failpoint}")
            crashed = PartialTailShrinkingRetirementDescriptorPrimaryStore(str(crash_path))
            observed = _semantic_state(crashed, keys)
            committed = failpoint != "retirement_tail_release_staged"
            expected = post if committed else pre
            if observed != expected:
                raise AssertionError(f"v0.38 shrink crash state drifted at {failpoint}")
            before_recovery = crashed.descriptor_arena_diagnostic()

            frontier_rejected_stale = False
            if committed:
                try:
                    crashed.retirement_descriptor_reference(
                        stale_page,
                        stale_incarnation,
                        expected_status=RETIREMENT_STATUS_FREE,
                    )
                except RuntimeError:
                    frontier_rejected_stale = True
                if not frontier_rejected_stale:
                    raise AssertionError("v0.38 committed frontier exposed post-shrink tail residue")

            recovery_one, recovery_two = _recover_twice(crashed)
            after_recovery = crashed.descriptor_arena_diagnostic()
            if committed and int(after_recovery["arena_file_bytes"]) != 4 * PAGE_SIZE:
                raise AssertionError("v0.38 committed crash recovery retained released tail bytes")
            if not committed and int(after_recovery["arena_file_bytes"]) != 6 * PAGE_SIZE:
                raise AssertionError("v0.38 precommit crash recovery truncated committed tail capacity")
            rows.append(
                {
                    "failpoint": failpoint,
                    "expected_committed": committed,
                    "exact_committed_state_match": True,
                    "frontier_rejected_stale_tail": frontier_rejected_stale if committed else None,
                    "arena_before_recovery": before_recovery,
                    "arena_after_recovery": after_recovery,
                    "recovery_one": recovery_one,
                    "recovery_two": recovery_two,
                }
            )

        for row in rows:
            if row["failpoint"] in {"committed", "retirement_tail_release_committed"}:
                if int(row["recovery_one"]["retirement_descriptor_arena_truncated_bytes"]) != 2 * PAGE_SIZE:
                    raise AssertionError("v0.38 postcommit/pretruncate recovery did not remove exactly one pair")
            if row["failpoint"] in {"retirement_arena_truncated", "retirement_tail_release_synced"}:
                if int(row["recovery_one"]["retirement_descriptor_arena_truncated_bytes"]) != 0:
                    raise AssertionError("v0.38 already-truncated crash performed redundant arena truncation")

        return {
            "failpoints": list(SHRINK_FAILPOINTS),
            "case_count": len(rows),
            "clean_trace": clean_trace.to_dict(),
            "rows": rows,
            "all_exact_committed_state_match": True,
            "all_recovery_scan_free": True,
            "all_second_recovery_idempotent": True,
        }


def run_partial_tail_shrink_experiment() -> dict[str, Any]:
    control = _control_case()
    nonaligned = _nonaligned_noop_case()
    release = _live_backlog_release_case()
    identity = _identity_after_partial_shrink_case()
    crashes = _crash_matrix()
    return {
        "experiment": "v0.38-partial-retirement-descriptor-tail-shrink",
        "hypothesis": (
            "when the committed descriptor free-list head is also the physical arena tail, a bounded maintenance "
            "transaction can publish a one-pair-shorter arena frontier and then truncate that pair without "
            "descriptor-history traversal, live relocation, or stale-identity aliasing while retirement backlog remains live"
        ),
        "v037_control": control,
        "nonaligned_noop": nonaligned,
        "live_backlog_release": release,
        "identity_after_partial_shrink": identity,
        "crash_matrix": crashes,
        "candidate_descriptor_history_walks": 0,
        "candidate_relocations": 0,
        "survived": True,
        "nonclaims": [
            "does not discover or unlink an arbitrary free tail descriptor buried inside the singly linked free list",
            "does not claim more than one descriptor pair released per bounded maintenance step",
            "does not establish filesystem allocated-block deallocation beyond observed sidecar file-length truncation",
            "does not establish hardware power-loss or torn-write safety from process SIGKILL evidence",
            "retirement descriptor incarnation remains a finite uint64 namespace",
            "diagnostic queue snapshots may traverse descriptor chains and are not candidate foreground work",
        ],
    }
