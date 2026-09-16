from __future__ import annotations

import hashlib
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from simulator.retired_generation_reclamation import _sparse_copy
from storage.bidirectional_queued_retirement_descriptor_primary import (
    BidirectionalQueuedRetirementDescriptorPrimaryStore,
)
from storage.deep_middle_live_tail_evacuation_retirement_descriptor_primary import (
    DeepMiddleLiveTailEvacuationRetirementDescriptorPrimaryStore,
)
from storage.queued_predecessor_index import QueuedPredecessorIO, encode_queued_predecessor
from storage.retirement_descriptor_pool import RETIREMENT_STATUS_QUEUED

ROOT = Path(__file__).resolve().parent.parent
WORKER = ROOT / "bidirectional_queued_retirement_worker.py"
SEGMENT_BUDGET = 2

RELOCATION_FAILPOINTS = (
    "retirement_biqueue_destination_staged",
    "retirement_biqueue_predecessor_staged",
    "retirement_biqueue_destination_prev_staged",
    "retirement_biqueue_successor_prev_staged",
    "retirement_biqueue_dependencies_synced",
    "committed",
    "retirement_biqueue_relocation_committed",
    "retirement_arena_truncated",
    "retirement_queued_predecessor_truncated",
    "retirement_biqueue_relocation_synced",
)
RELOCATION_PRECOMMIT = set(RELOCATION_FAILPOINTS[:5])

APPEND_FAILPOINTS = (
    "retirement_arena_descriptor_written",
    "retirement_tail_linked",
    "retirement_arena_synced",
    "retirement_queued_predecessor_synced",
    "data_synced",
    "committed",
)
REUSE_FAILPOINTS = (
    "retirement_descriptor_reused",
    "retirement_tail_linked",
    "retirement_arena_synced",
    "retirement_queued_predecessor_synced",
    "data_synced",
    "committed",
)
RECLAIM_FAILPOINTS = (
    "retirement_descriptor_freed",
    "retirement_queued_head_predecessor_synced",
    "retirement_arena_synced",
    "retirement_dequeued",
    "dependencies_synced",
    "committed",
)


def _key(index: int) -> str:
    return f"k-{index:04d}"


def _insert_until_queue_count(store, *, index: int, target: int, cap: int = 65536) -> int:
    while int(store.retirement_queue_snapshot()["queue_count"]) < target:
        if index >= cap:
            raise AssertionError(f"v0.44 fixture did not reach queue count {target}")
        store.insert(_key(index))
        index += 1
    if int(store.retirement_queue_snapshot()["queue_count"]) != target:
        raise AssertionError("v0.44 fixture overshot requested queue count")
    return index


def _reclaim_to_depth(store, *, target: int) -> None:
    while int(store.retirement_queue_snapshot()["queue_count"]) > target:
        trace = store.reclaim_step(budget=SEGMENT_BUDGET)
        if int(trace.retirement_descriptors_scanned) != 0:
            raise AssertionError("v0.44 setup reclaim scanned retirement descriptors")
    if int(store.retirement_queue_snapshot()["queue_count"]) != target:
        raise AssertionError("v0.44 setup reclaim overshot target depth")


def _copy_v044(source: Path, destination: Path) -> None:
    _sparse_copy(source, destination)
    shutil.copyfile(
        BidirectionalQueuedRetirementDescriptorPrimaryStore.arena_path_for(source),
        BidirectionalQueuedRetirementDescriptorPrimaryStore.arena_path_for(destination),
    )
    shutil.copyfile(
        BidirectionalQueuedRetirementDescriptorPrimaryStore.predecessor_path_for(source),
        BidirectionalQueuedRetirementDescriptorPrimaryStore.predecessor_path_for(destination),
    )


def _find_next_enqueue_trigger(path: Path, *, index: int, target: int) -> tuple[int, str]:
    while index < 65536:
        key = _key(index)
        with tempfile.TemporaryDirectory(prefix="dic-v044-probe-") as probe_tmp:
            probe_path = Path(probe_tmp) / "probe.pages"
            _copy_v044(path, probe_path)
            probe = BidirectionalQueuedRetirementDescriptorPrimaryStore(str(probe_path))
            probe.insert(key)
            if int(probe.retirement_queue_snapshot()["queue_count"]) == target:
                return index, key
        BidirectionalQueuedRetirementDescriptorPrimaryStore(str(path)).insert(key)
        index += 1
    raise AssertionError("v0.44 did not find next descriptor-producing insertion")


def _prepare_pre_append_fixture(path: Path) -> dict[str, Any]:
    store = BidirectionalQueuedRetirementDescriptorPrimaryStore(str(path))
    store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=4096)
    index = _insert_until_queue_count(store, index=0, target=2)
    trigger_index, trigger_key = _find_next_enqueue_trigger(path, index=index, target=3)
    return {
        "trigger_index": trigger_index,
        "trigger_key": trigger_key,
        "keys": tuple(_key(i) for i in range(trigger_index + 1)),
        "before": store.retirement_queue_snapshot(),
    }


def _prepare_pre_reuse_fixture(path: Path, *, depth: int = 5) -> dict[str, Any]:
    if depth < 5:
        raise ValueError("v0.44 deeper fixture requires depth >= 5")
    store = BidirectionalQueuedRetirementDescriptorPrimaryStore(str(path))
    store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=4096)
    index = _insert_until_queue_count(store, index=0, target=depth + 1)
    _reclaim_to_depth(store, target=depth - 1)
    before = store.retirement_queue_snapshot()
    if [int(row["descriptor_page"]) for row in before["free_descriptors"]] != [2, 0]:
        raise AssertionError("v0.44 pre-reuse FREE chain drifted")
    trigger_index, trigger_key = _find_next_enqueue_trigger(
        path, index=index, target=depth
    )
    return {
        "trigger_index": trigger_index,
        "trigger_key": trigger_key,
        "keys": tuple(_key(i) for i in range(trigger_index + 1)),
        "before": store.retirement_queue_snapshot(),
    }


def _advance_to_deeper_fixture(path: Path, *, depth: int = 5) -> dict[str, Any]:
    fixture = _prepare_pre_reuse_fixture(path, depth=depth)
    store = BidirectionalQueuedRetirementDescriptorPrimaryStore(str(path))
    trace = store.insert(str(fixture["trigger_key"]))
    ready = store.retirement_queue_snapshot()
    expected = list(range(4, 2 * (depth + 1), 2)) + [2]
    if [int(row["descriptor_page"]) for row in ready["descriptors"]] != expected:
        raise AssertionError(f"v0.44 ready queue drifted: {expected}")
    if [int(row["descriptor_page"]) for row in ready["free_descriptors"]] != [0]:
        raise AssertionError("v0.44 ready FREE list is not [0]")
    fixture["ready"] = ready
    fixture["trigger_trace"] = trace
    return fixture


def _prepare_v043_deeper_fixture(path: Path) -> tuple[Any, tuple[str, ...]]:
    store = DeepMiddleLiveTailEvacuationRetirementDescriptorPrimaryStore(str(path))
    store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=4096)
    index = _insert_until_queue_count(store, index=0, target=6)
    _reclaim_to_depth(store, target=4)
    index = _insert_until_queue_count(store, index=index, target=5)
    ready = store.retirement_queue_snapshot()
    if [int(row["descriptor_page"]) for row in ready["descriptors"]] != [4, 6, 8, 10, 2]:
        raise AssertionError("v0.43 deeper control fixture drifted")
    return store, tuple(_key(i) for i in range(index))


def _topology(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "queue": [int(row["descriptor_page"]) for row in snapshot["descriptors"]],
        "free": [int(row["descriptor_page"]) for row in snapshot["free_descriptors"]],
        "arena_pages": int(snapshot["descriptor_arena_pages"]),
        "head": snapshot.get("head_page"),
        "tail": snapshot.get("tail_page"),
        "tail_predecessor": snapshot.get("tail_predecessor_page"),
        "physical_tail": snapshot.get("physical_tail_page"),
        "physical_tail_predecessor": snapshot.get("physical_tail_predecessor_page"),
        "queued_predecessors": [row.get("queued_prev_page") for row in snapshot["descriptors"]],
    }


def _work(trace: Any) -> dict[str, Any]:
    return {
        "released": bool(trace.released),
        "descriptor_preads": int(trace.retirement_descriptor_preads),
        "descriptor_pwrites": int(trace.retirement_descriptor_pwrites),
        "predecessor_preads": int(trace.queued_predecessor_preads),
        "predecessor_pwrites": int(trace.queued_predecessor_pwrites),
        "descriptor_scans": int(trace.retirement_descriptors_scanned),
        "predecessor_scans": int(trace.queued_predecessors_scanned),
        "relocations": int(trace.live_descriptor_relocations),
        "arena_before": int(trace.retirement_arena_pages_before),
        "arena_after": int(trace.retirement_arena_pages_after),
        "bytes_released": int(trace.retirement_arena_bytes_released),
    }


def _semantic_state(store, keys: tuple[str, ...]) -> dict[str, Any]:
    return {
        "state": store.committed_state(keys),
        "queue": store.retirement_queue_snapshot(),
        "arena": store.descriptor_arena_diagnostic(),
        "queued_predecessor_sidecar_bytes": store.predecessor_path.stat().st_size,
    }


def _require_scan_free(recovery: dict[str, Any]) -> None:
    if int(recovery["logical_work"]) != 0:
        raise AssertionError("v0.44 recovery required logical redo")
    if int(recovery["generation_pages_scanned"]) != 0:
        raise AssertionError("v0.44 recovery scanned generation pages")
    if int(recovery["mapping_nodes_scanned"]) != 0:
        raise AssertionError("v0.44 recovery scanned mapping nodes")
    if int(recovery.get("retirement_descriptors_scanned", 0)) != 0:
        raise AssertionError("v0.44 recovery scanned retirement descriptors")
    if int(recovery.get("queued_predecessors_scanned", 0)) != 0:
        raise AssertionError("v0.44 recovery scanned queued predecessors")


def _recover_twice(store) -> tuple[dict[str, Any], dict[str, Any]]:
    one = store.recover()
    two = store.recover()
    _require_scan_free(one)
    _require_scan_free(two)
    for key in (
        "physical_truncated_bytes",
        "retirement_descriptor_arena_truncated_bytes",
        "queued_predecessor_sidecar_truncated_bytes",
    ):
        if int(two.get(key, 0)) != 0:
            raise AssertionError(f"v0.44 second recovery changed physical state: {key}")
    return one, two


def _worker(
    path: Path,
    command: str,
    *,
    failpoint: str,
    key: str | None = None,
    budget: int = SEGMENT_BUDGET,
) -> subprocess.CompletedProcess[str]:
    args = [sys.executable, str(WORKER), "--file", str(path), command]
    if key is not None:
        args.extend(["--key", key])
    if command == "reclaim":
        args.extend(["--budget", str(budget)])
    args.extend(["--failpoint", failpoint])
    return subprocess.run(args, cwd=ROOT, text=True, capture_output=True, check=False)


def _require_sigkill(proc: subprocess.CompletedProcess[str], label: str) -> None:
    if proc.returncode != -signal.SIGKILL:
        raise AssertionError(f"{label} did not SIGKILL: {proc.returncode} {proc.stderr}")


def _recovery_summary(one: dict[str, Any], two: dict[str, Any]) -> dict[str, Any]:
    return {
        "primary_truncated_bytes": int(one.get("physical_truncated_bytes", 0)),
        "arena_truncated_bytes": int(
            one.get("retirement_descriptor_arena_truncated_bytes", 0)
        ),
        "predecessor_truncated_bytes": int(
            one.get("queued_predecessor_sidecar_truncated_bytes", 0)
        ),
        "scan_free": True,
        "second_idempotent": all(
            int(two.get(key, 0)) == 0
            for key in (
                "physical_truncated_bytes",
                "retirement_descriptor_arena_truncated_bytes",
                "queued_predecessor_sidecar_truncated_bytes",
            )
        ),
    }


def _v043_control_case() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v044-v043-control-") as tmp:
        path = Path(tmp) / "primary.pages"
        store, _keys = _prepare_v043_deeper_fixture(path)
        before = store.retirement_queue_snapshot()
        trace = store.evacuate_deep_middle_live_retirement_arena_tail_step()
        after = store.retirement_queue_snapshot()
        if trace.released or int(trace.retirement_descriptor_preads) != 0:
            raise AssertionError("v0.43 control did not refuse before descriptor I/O")
        if before != after:
            raise AssertionError("v0.43 control changed state")
        return {
            "before": {
                "queue": [int(row["descriptor_page"]) for row in before["descriptors"]],
                "free": [int(row["descriptor_page"]) for row in before["free_descriptors"]],
                "arena_pages": int(before["descriptor_arena_pages"]),
                "physical_tail": before.get("physical_tail_page"),
            },
            "released": False,
            "descriptor_preads": 0,
            "unchanged": True,
        }


def _candidate_case() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v044-candidate-") as tmp:
        path = Path(tmp) / "primary.pages"
        fixture = _advance_to_deeper_fixture(path, depth=5)
        store = BidirectionalQueuedRetirementDescriptorPrimaryStore(str(path))
        before = store.retirement_queue_snapshot()
        old_tail_inc = int(before["physical_tail_incarnation"])
        old_destination_inc = int(before["descriptor_free_head_incarnation"])
        old_tail = store.retirement_descriptor_reference(
            10, old_tail_inc, expected_status=RETIREMENT_STATUS_QUEUED
        )
        successor_inc = int(before["tail_incarnation"])

        trace = store.evacuate_bidirectional_queued_live_retirement_arena_tail_step()
        after = store.retirement_queue_snapshot()
        if not trace.released:
            raise AssertionError("v0.44 candidate did not release physical tail")
        if _work(trace) != {
            "released": True,
            "descriptor_preads": 10,
            "descriptor_pwrites": 2,
            "predecessor_preads": 8,
            "predecessor_pwrites": 2,
            "descriptor_scans": 0,
            "predecessor_scans": 0,
            "relocations": 1,
            "arena_before": 12,
            "arena_after": 10,
            "bytes_released": 8192,
        }:
            raise AssertionError("v0.44 candidate direct-work result drifted")

        stale_tail_descriptor_rejected = False
        try:
            store.retirement_descriptor_reference(
                10, old_tail_inc, expected_status=RETIREMENT_STATUS_QUEUED
            )
        except RuntimeError:
            stale_tail_descriptor_rejected = True
        stale_tail_predecessor_rejected = False
        try:
            store.queued_predecessor_reference(10, old_tail_inc)
        except RuntimeError:
            stale_tail_predecessor_rejected = True
        stale_destination_descriptor_rejected = False
        try:
            store.retirement_descriptor_reference(
                0, old_destination_inc, expected_status=RETIREMENT_STATUS_QUEUED
            )
        except RuntimeError:
            stale_destination_descriptor_rejected = True
        stale_destination_predecessor_rejected = False
        try:
            store.queued_predecessor_reference(0, old_destination_inc)
        except RuntimeError:
            stale_destination_predecessor_rejected = True
        if not all(
            (
                stale_tail_descriptor_rejected,
                stale_tail_predecessor_rejected,
                stale_destination_descriptor_rejected,
                stale_destination_predecessor_rejected,
            )
        ):
            raise AssertionError("v0.44 stale identity rejection failed")

        relocated = store.retirement_descriptor_reference(
            0,
            int(trace.destination_new_incarnation or 0),
            expected_status=RETIREMENT_STATUS_QUEUED,
        )
        for name in ("generation", "cursor_header_page", "remaining_segments"):
            if int(relocated[name]) != int(old_tail[name]):
                raise AssertionError(f"v0.44 relocated payload drifted: {name}")
        if int(relocated["next_descriptor_page"] or -1) != 2:
            raise AssertionError("v0.44 relocated successor page drifted")
        if int(relocated["next_descriptor_incarnation"] or -1) != successor_inc:
            raise AssertionError("v0.44 relocated successor identity drifted")

        return {
            "trigger_key": fixture["trigger_key"],
            "before": _topology(before),
            "after": _topology(after),
            "work": _work(trace),
            "identity": {
                "physical_tail": [trace.physical_tail_page, trace.physical_tail_incarnation],
                "predecessor": [trace.predecessor_page, trace.predecessor_incarnation],
                "predecessor_predecessor": [
                    trace.predecessor_predecessor_page,
                    trace.predecessor_predecessor_incarnation,
                ],
                "successor": [trace.successor_page, trace.successor_incarnation],
                "destination_old": [trace.destination_page, trace.destination_old_incarnation],
                "destination_new": [trace.destination_page, trace.destination_new_incarnation],
                "new_physical_tail": [
                    trace.new_physical_tail_page,
                    trace.new_physical_tail_incarnation,
                ],
                "new_physical_tail_predecessor": [
                    trace.new_physical_tail_predecessor_page,
                    trace.new_physical_tail_predecessor_incarnation,
                ],
            },
            "stale_tail_descriptor_rejected": stale_tail_descriptor_rejected,
            "stale_tail_predecessor_rejected": stale_tail_predecessor_rejected,
            "stale_destination_descriptor_rejected": stale_destination_descriptor_rejected,
            "stale_destination_predecessor_rejected": stale_destination_predecessor_rejected,
            "payload_preserved": True,
            "queued_predecessor_consistent": bool(after["queued_predecessor_consistent"]),
        }


def _relocation_crash_matrix() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v044-relocation-crash-") as tmp:
        directory = Path(tmp)
        base_path = directory / "base.pages"
        fixture = _advance_to_deeper_fixture(base_path, depth=5)
        keys = tuple(fixture["keys"])
        base = BidirectionalQueuedRetirementDescriptorPrimaryStore(str(base_path))
        pre = _semantic_state(base, keys)

        clean_path = directory / "clean.pages"
        _copy_v044(base_path, clean_path)
        clean = BidirectionalQueuedRetirementDescriptorPrimaryStore(str(clean_path))
        clean_trace = clean.evacuate_bidirectional_queued_live_retirement_arena_tail_step()
        post = _semantic_state(clean, keys)
        if not clean_trace.released:
            raise AssertionError("v0.44 relocation crash oracle did not release")

        cases = []
        for failpoint in RELOCATION_FAILPOINTS:
            crash_path = directory / f"crash-{failpoint}.pages"
            _copy_v044(base_path, crash_path)
            proc = _worker(crash_path, "evacuate", failpoint=failpoint)
            _require_sigkill(proc, f"relocation/{failpoint}")
            crashed = BidirectionalQueuedRetirementDescriptorPrimaryStore(str(crash_path))
            one, two = _recover_twice(crashed)
            recovered = _semantic_state(crashed, keys)
            expected = pre if failpoint in RELOCATION_PRECOMMIT else post
            if recovered != expected:
                raise AssertionError(f"v0.44 relocation {failpoint} recovered wrong state")
            cases.append(
                {
                    "failpoint": failpoint,
                    "expected_state": "pre" if failpoint in RELOCATION_PRECOMMIT else "post",
                    **_recovery_summary(one, two),
                    "exact": True,
                }
            )
        return {
            "count": len(cases),
            "failpoints": list(RELOCATION_FAILPOINTS),
            "clean_work": _work(clean_trace),
            "cases": cases,
            "all_exact": True,
            "all_scan_free": True,
            "all_second_idempotent": True,
        }


def _insert_crash_matrix(*, mode: str) -> dict[str, Any]:
    if mode not in {"append", "reuse"}:
        raise ValueError(mode)
    with tempfile.TemporaryDirectory(prefix=f"dic-v044-{mode}-crash-") as tmp:
        directory = Path(tmp)
        base_path = directory / "base.pages"
        fixture = (
            _prepare_pre_append_fixture(base_path)
            if mode == "append"
            else _prepare_pre_reuse_fixture(base_path, depth=5)
        )
        trigger_key = str(fixture["trigger_key"])
        keys = tuple(fixture["keys"])
        base = BidirectionalQueuedRetirementDescriptorPrimaryStore(str(base_path))
        pre = _semantic_state(base, keys)

        clean_path = directory / "clean.pages"
        _copy_v044(base_path, clean_path)
        clean = BidirectionalQueuedRetirementDescriptorPrimaryStore(str(clean_path))
        clean_trace = clean.insert(trigger_key)
        post = _semantic_state(clean, keys)
        failpoints = APPEND_FAILPOINTS if mode == "append" else REUSE_FAILPOINTS

        cases = []
        for failpoint in failpoints:
            crash_path = directory / f"crash-{failpoint}.pages"
            _copy_v044(base_path, crash_path)
            proc = _worker(crash_path, "insert", failpoint=failpoint, key=trigger_key)
            _require_sigkill(proc, f"{mode}/{failpoint}")
            crashed = BidirectionalQueuedRetirementDescriptorPrimaryStore(str(crash_path))
            one, two = _recover_twice(crashed)
            recovered = _semantic_state(crashed, keys)
            expected = post if failpoint == "committed" else pre
            if recovered != expected:
                raise AssertionError(f"v0.44 {mode} {failpoint} recovered wrong state")
            cases.append(
                {
                    "failpoint": failpoint,
                    "expected_state": "post" if failpoint == "committed" else "pre",
                    **_recovery_summary(one, two),
                    "exact": True,
                }
            )

        return {
            "mode": mode,
            "count": len(cases),
            "trigger_index": int(fixture["trigger_index"]),
            "trigger_key": trigger_key,
            "clean_trace": {
                "retirement_descriptor_reuses": int(clean_trace.retirement_descriptor_reuses),
                "retirement_descriptor_preads": int(clean_trace.retirement_descriptor_preads),
                "retirement_descriptor_pwrites": int(clean_trace.retirement_descriptor_pwrites),
                "queued_predecessor_preads": int(clean_trace.queued_predecessor_preads),
                "queued_predecessor_pwrites": int(clean_trace.queued_predecessor_pwrites),
                "queued_predecessor_fsyncs": int(clean_trace.queued_predecessor_fsyncs),
                "retirement_queue_count": int(clean_trace.retirement_queue_count),
                "retirement_descriptor_free_count": int(clean_trace.retirement_descriptor_free_count),
                "retirement_arena_pages": int(clean_trace.retirement_arena_pages),
            },
            "pre": _topology(pre["queue"]),
            "post": _topology(post["queue"]),
            "cases": cases,
            "all_exact": True,
            "all_scan_free": True,
            "all_second_idempotent": True,
        }


def _prepare_reclaim_fixture(path: Path) -> dict[str, Any]:
    store = BidirectionalQueuedRetirementDescriptorPrimaryStore(str(path))
    store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=4096)
    index = _insert_until_queue_count(store, index=0, target=3)
    snapshot = store.retirement_queue_snapshot()
    remaining = int(snapshot["descriptors"][0]["remaining_segments"])
    if remaining > SEGMENT_BUDGET:
        store.reclaim_step(budget=remaining - SEGMENT_BUDGET)
    before = store.retirement_queue_snapshot()
    if int(before["queue_count"]) != 3:
        raise AssertionError("v0.44 reclaim fixture unexpectedly dequeued head")
    if int(before["descriptors"][0]["remaining_segments"]) > SEGMENT_BUDGET:
        raise AssertionError("v0.44 reclaim fixture head still exceeds test budget")
    return {
        "keys": tuple(_key(i) for i in range(index)),
        "before": before,
        "budget": SEGMENT_BUDGET,
    }


def _reclaim_crash_matrix() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v044-reclaim-crash-") as tmp:
        directory = Path(tmp)
        base_path = directory / "base.pages"
        fixture = _prepare_reclaim_fixture(base_path)
        keys = tuple(fixture["keys"])
        base = BidirectionalQueuedRetirementDescriptorPrimaryStore(str(base_path))
        pre = _semantic_state(base, keys)

        clean_path = directory / "clean.pages"
        _copy_v044(base_path, clean_path)
        clean = BidirectionalQueuedRetirementDescriptorPrimaryStore(str(clean_path))
        clean_trace = clean.reclaim_step(budget=int(fixture["budget"]))
        post = _semantic_state(clean, keys)
        if int(post["queue"]["queue_count"]) != 2:
            raise AssertionError("v0.44 reclaim crash oracle did not dequeue one head")
        if post["queue"]["descriptors"][0]["queued_prev_page"] is not None:
            raise AssertionError("v0.44 reclaim oracle did not null new head predecessor")

        cases = []
        for failpoint in RECLAIM_FAILPOINTS:
            crash_path = directory / f"crash-{failpoint}.pages"
            _copy_v044(base_path, crash_path)
            proc = _worker(
                crash_path,
                "reclaim",
                failpoint=failpoint,
                budget=int(fixture["budget"]),
            )
            _require_sigkill(proc, f"reclaim/{failpoint}")
            crashed = BidirectionalQueuedRetirementDescriptorPrimaryStore(str(crash_path))
            one, two = _recover_twice(crashed)
            recovered = _semantic_state(crashed, keys)
            expected = post if failpoint == "committed" else pre
            if recovered != expected:
                raise AssertionError(f"v0.44 reclaim {failpoint} recovered wrong state")
            cases.append(
                {
                    "failpoint": failpoint,
                    "expected_state": "post" if failpoint == "committed" else "pre",
                    **_recovery_summary(one, two),
                    "exact": True,
                }
            )
        return {
            "count": len(cases),
            "clean_trace": {
                "queued_predecessor_preads": int(clean_trace.queued_predecessor_preads),
                "queued_predecessor_pwrites": int(clean_trace.queued_predecessor_pwrites),
                "queued_predecessor_fsyncs": int(clean_trace.queued_predecessor_fsyncs),
                "queue_count": int(clean_trace.retirement_queue_count),
                "descriptor_scans": int(clean_trace.retirement_descriptors_scanned),
            },
            "pre": _topology(pre["queue"]),
            "post": _topology(post["queue"]),
            "cases": cases,
            "all_exact": True,
            "all_scan_free": True,
            "all_second_idempotent": True,
        }


def _scaling_case() -> dict[str, Any]:
    rows = []
    for depth in (5, 6, 7, 8):
        with tempfile.TemporaryDirectory(prefix=f"dic-v044-scale-{depth}-") as tmp:
            path = Path(tmp) / "primary.pages"
            _advance_to_deeper_fixture(path, depth=depth)
            store = BidirectionalQueuedRetirementDescriptorPrimaryStore(str(path))
            before = store.retirement_queue_snapshot()
            trace = store.evacuate_bidirectional_queued_live_retirement_arena_tail_step()
            after = store.retirement_queue_snapshot()
            if not trace.released or not bool(after["queued_predecessor_consistent"]):
                raise AssertionError(f"v0.44 scaling depth {depth} did not survive")
            rows.append(
                {
                    "depth": depth,
                    "physical_tail": before["physical_tail_page"],
                    "predecessor": trace.predecessor_page,
                    "predecessor_predecessor": trace.predecessor_predecessor_page,
                    **_work(trace),
                }
            )
    expected_work = {
        (row["descriptor_preads"], row["descriptor_pwrites"], row["predecessor_preads"], row["predecessor_pwrites"], row["descriptor_scans"], row["predecessor_scans"], row["relocations"])
        for row in rows
    }
    if expected_work != {(10, 2, 8, 2, 0, 0, 1)}:
        raise AssertionError("v0.44 direct work grew with live prefix depth")
    return {"depths": [5, 6, 7, 8], "rows": rows, "constant_direct_work": True}


def _malformed_predecessor_refusal() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v044-malformed-") as tmp:
        path = Path(tmp) / "primary.pages"
        _advance_to_deeper_fixture(path, depth=5)
        store = BidirectionalQueuedRetirementDescriptorPrimaryStore(str(path))
        snapshot = store.retirement_queue_snapshot()
        tail_page = int(snapshot["physical_tail_page"])
        tail_inc = int(snapshot["physical_tail_incarnation"])
        head_page = int(snapshot["head_page"])
        head_inc = int(snapshot["head_incarnation"])
        fd = store._open()
        try:
            epoch, _meta, _slot = store._read_super(fd)
        finally:
            os.close(fd)
        predecessor_fd = store._open_predecessors()
        try:
            malformed = encode_queued_predecessor(
                epoch,
                incarnation=tail_inc,
                prev_page=head_page,
                prev_incarnation=head_inc,
            )
            os.pwrite(predecessor_fd, malformed, QueuedPredecessorIO.offset(tail_page, 0))
            os.pwrite(predecessor_fd, malformed, QueuedPredecessorIO.offset(tail_page, 1))
            os.fsync(predecessor_fd)
        finally:
            os.close(predecessor_fd)

        def digests() -> tuple[str, str, str]:
            return (
                hashlib.sha256(path.read_bytes()).hexdigest(),
                hashlib.sha256(store.arena_path.read_bytes()).hexdigest(),
                hashlib.sha256(store.predecessor_path.read_bytes()).hexdigest(),
            )

        before = digests()
        rejected = False
        try:
            store.evacuate_bidirectional_queued_live_retirement_arena_tail_step()
        except RuntimeError:
            rejected = True
        after = digests()
        if not rejected or before != after:
            raise AssertionError("v0.44 malformed predecessor was not rejected read-only")
        return {
            "physical_tail": tail_page,
            "malformed_prev": head_page,
            "rejected": True,
            "files_unchanged_by_refusal": True,
        }


def run_bidirectional_queued_retirement_descriptor_experiment() -> dict[str, Any]:
    return {
        "experiment": "v0.44-tagged-queued-predecessor-authority",
        "survived": True,
        "v043_control": _v043_control_case(),
        "candidate": _candidate_case(),
        "append_authority_crashes": _insert_crash_matrix(mode="append"),
        "reuse_authority_crashes": _insert_crash_matrix(mode="reuse"),
        "reclaim_authority_crashes": _reclaim_crash_matrix(),
        "relocation_crashes": _relocation_crash_matrix(),
        "scaling": _scaling_case(),
        "malformed_predecessor_refusal": _malformed_predecessor_refusal(),
        "claim_boundary": {
            "minimum_live_queue_depth": 5,
            "physical_tail_successor_is_queue_tail": True,
            "sole_free_destination": True,
            "tagged_predecessor_per_queued_descriptor": True,
            "queue_or_history_walk": False,
            "superblock_reverse_window_claim_bearing": False,
            "single_writer": True,
        },
    }
