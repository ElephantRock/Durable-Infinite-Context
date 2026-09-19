from __future__ import annotations

import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Type

from simulator.retired_generation_reclamation import _sparse_copy
from storage.bidirectional_queued_retirement_descriptor_primary import (
    BidirectionalQueuedRetirementDescriptorPrimaryStore,
)
from storage.multi_free_bidirectional_queued_retirement_descriptor_primary import (
    MultiFreeBidirectionalQueuedRetirementDescriptorPrimaryStore,
)
from storage.retirement_descriptor_pool import RETIREMENT_STATUS_FREE, RETIREMENT_STATUS_QUEUED

ROOT = Path(__file__).resolve().parent.parent
WORKER = ROOT / "multi_free_bidirectional_queued_retirement_worker.py"
SEGMENT_BUDGET = 2

RELOCATION_FAILPOINTS = (
    "retirement_mfbq_destination_staged",
    "retirement_mfbq_predecessor_staged",
    "retirement_mfbq_free_successor_staged",
    "retirement_mfbq_destination_prev_staged",
    "retirement_mfbq_successor_prev_staged",
    "retirement_mfbq_dependencies_synced",
    "committed",
    "retirement_mfbq_relocation_committed",
    "retirement_arena_truncated",
    "retirement_queued_predecessor_truncated",
    "retirement_mfbq_relocation_synced",
)
RELOCATION_PRECOMMIT = set(RELOCATION_FAILPOINTS[:6])


def _key(index: int) -> str:
    return f"k-{index:04d}"


def _insert_until_queue_count(store, *, index: int, target: int, cap: int = 131072) -> int:
    while int(store.retirement_queue_snapshot()["queue_count"]) < target:
        if index >= cap:
            raise AssertionError(f"v0.45 fixture did not reach queue count {target}")
        store.insert(_key(index))
        index += 1
    if int(store.retirement_queue_snapshot()["queue_count"]) != target:
        raise AssertionError("v0.45 fixture overshot requested queue count")
    return index


def _reclaim_to_depth(store, *, target: int) -> None:
    while int(store.retirement_queue_snapshot()["queue_count"]) > target:
        trace = store.reclaim_step(budget=SEGMENT_BUDGET)
        if int(trace.retirement_descriptors_scanned) != 0:
            raise AssertionError("v0.45 setup reclaim scanned retirement descriptors")
    if int(store.retirement_queue_snapshot()["queue_count"]) != target:
        raise AssertionError("v0.45 setup reclaim overshot target")


def _prepare_fixture(
    path: Path,
    store_cls: Type,
    *,
    depth: int = 5,
    free_depth: int = 2,
) -> dict[str, Any]:
    if depth < 5 or free_depth < 2:
        raise ValueError("v0.45 requires depth >= 5 and free_depth >= 2")
    store = store_cls(str(path))
    store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=4096)
    index = _insert_until_queue_count(store, index=0, target=depth + free_depth)
    _reclaim_to_depth(store, target=depth - 1)
    index = _insert_until_queue_count(store, index=index, target=depth)
    ready = store.retirement_queue_snapshot()
    expected_queue = list(range(2 * (free_depth + 1), 2 * (depth + free_depth), 2)) + [
        2 * free_depth
    ]
    expected_free = list(range(2 * (free_depth - 1), -1, -2))
    if [int(row["descriptor_page"]) for row in ready["descriptors"]] != expected_queue:
        raise AssertionError(f"v0.45 ready queue drifted: {expected_queue}")
    if [int(row["descriptor_page"]) for row in ready["free_descriptors"]] != expected_free:
        raise AssertionError(f"v0.45 ready FREE chain drifted: {expected_free}")
    return {
        "keys": tuple(_key(i) for i in range(index)),
        "ready": ready,
        "depth": depth,
        "free_depth": free_depth,
    }


def _copy_v045(source: Path, destination: Path) -> None:
    _sparse_copy(source, destination)
    shutil.copyfile(
        MultiFreeBidirectionalQueuedRetirementDescriptorPrimaryStore.arena_path_for(source),
        MultiFreeBidirectionalQueuedRetirementDescriptorPrimaryStore.arena_path_for(destination),
    )
    shutil.copyfile(
        MultiFreeBidirectionalQueuedRetirementDescriptorPrimaryStore.predecessor_path_for(source),
        MultiFreeBidirectionalQueuedRetirementDescriptorPrimaryStore.predecessor_path_for(destination),
    )


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
        raise AssertionError("v0.45 recovery required logical redo")
    if int(recovery["generation_pages_scanned"]) != 0:
        raise AssertionError("v0.45 recovery scanned generation pages")
    if int(recovery["mapping_nodes_scanned"]) != 0:
        raise AssertionError("v0.45 recovery scanned mapping nodes")
    if int(recovery.get("retirement_descriptors_scanned", 0)) != 0:
        raise AssertionError("v0.45 recovery scanned retirement descriptors")
    if int(recovery.get("queued_predecessors_scanned", 0)) != 0:
        raise AssertionError("v0.45 recovery scanned queued predecessors")


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
            raise AssertionError(f"v0.45 second recovery changed physical state: {key}")
    return one, two


def _recovery_summary(one: dict[str, Any], two: dict[str, Any]) -> dict[str, Any]:
    return {
        "primary_truncated_bytes": int(one.get("physical_truncated_bytes", 0)),
        "arena_truncated_bytes": int(one.get("retirement_descriptor_arena_truncated_bytes", 0)),
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


def _worker(path: Path, failpoint: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(WORKER), "--file", str(path), "--failpoint", failpoint],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _v044_control_case() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v045-control-") as tmp:
        path = Path(tmp) / "primary.pages"
        _prepare_fixture(path, BidirectionalQueuedRetirementDescriptorPrimaryStore)
        store = BidirectionalQueuedRetirementDescriptorPrimaryStore(str(path))
        before = store.retirement_queue_snapshot()
        trace = store.evacuate_bidirectional_queued_live_retirement_arena_tail_step()
        after = store.retirement_queue_snapshot()
        if trace.released or int(trace.retirement_descriptor_preads) != 0:
            raise AssertionError("v0.44 control did not refuse multiple-FREE before descriptor I/O")
        if before != after:
            raise AssertionError("v0.44 control changed state")
        return {
            "before": _topology(before),
            "released": False,
            "descriptor_preads": 0,
            "predecessor_preads": 0,
            "unchanged": True,
        }


def _candidate_case() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v045-candidate-") as tmp:
        path = Path(tmp) / "primary.pages"
        _prepare_fixture(path, MultiFreeBidirectionalQueuedRetirementDescriptorPrimaryStore)
        store = MultiFreeBidirectionalQueuedRetirementDescriptorPrimaryStore(str(path))
        before = store.retirement_queue_snapshot()
        tail_page = int(before["physical_tail_page"])
        tail_inc = int(before["physical_tail_incarnation"])
        destination_page = int(before["descriptor_free_head_page"])
        destination_inc = int(before["descriptor_free_head_incarnation"])
        free_successor_page = int(before["free_descriptors"][1]["descriptor_page"])
        free_successor_inc = int(before["free_descriptors"][1]["descriptor_incarnation"])
        old_tail = store.retirement_descriptor_reference(
            tail_page, tail_inc, expected_status=RETIREMENT_STATUS_QUEUED
        )

        trace = store.evacuate_multi_free_bidirectional_queued_live_retirement_arena_tail_step()
        after = store.retirement_queue_snapshot()
        expected_work = {
            "released": True,
            "descriptor_preads": 12,
            "descriptor_pwrites": 3,
            "predecessor_preads": 8,
            "predecessor_pwrites": 2,
            "descriptor_scans": 0,
            "predecessor_scans": 0,
            "relocations": 1,
            "arena_before": 14,
            "arena_after": 12,
            "bytes_released": 8192,
        }
        if _work(trace) != expected_work:
            raise AssertionError(f"v0.45 candidate work drifted: {_work(trace)!r}")
        if _topology(after)["queue"] != [6, 8, 10, 2, 4]:
            raise AssertionError("v0.45 post queue drifted")
        if _topology(after)["free"] != [0]:
            raise AssertionError("v0.45 post FREE chain drifted")
        if not bool(after["queued_predecessor_consistent"]):
            raise AssertionError("v0.45 QUEUED predecessor topology is inconsistent")

        surviving_free = store.retirement_descriptor_reference(
            free_successor_page,
            free_successor_inc,
            expected_status=RETIREMENT_STATUS_FREE,
        )
        if store._free_prev(surviving_free) != (None, None):
            raise AssertionError("v0.45 new FREE head predecessor was not cleared")

        checks: dict[str, bool] = {}
        for name, fn in (
            (
                "stale_tail_descriptor_rejected",
                lambda: store.retirement_descriptor_reference(
                    tail_page, tail_inc, expected_status=RETIREMENT_STATUS_QUEUED
                ),
            ),
            (
                "stale_tail_predecessor_rejected",
                lambda: store.queued_predecessor_reference(tail_page, tail_inc),
            ),
            (
                "stale_destination_descriptor_rejected",
                lambda: store.retirement_descriptor_reference(
                    destination_page,
                    destination_inc,
                    expected_status=RETIREMENT_STATUS_QUEUED,
                ),
            ),
            (
                "stale_destination_predecessor_rejected",
                lambda: store.queued_predecessor_reference(destination_page, destination_inc),
            ),
        ):
            try:
                fn()
                checks[name] = False
            except RuntimeError:
                checks[name] = True
        if not all(checks.values()):
            raise AssertionError(f"v0.45 stale identity rejection failed: {checks!r}")

        relocated = store.retirement_descriptor_reference(
            destination_page,
            int(trace.destination_new_incarnation or 0),
            expected_status=RETIREMENT_STATUS_QUEUED,
        )
        for name in ("generation", "cursor_header_page", "remaining_segments"):
            if int(relocated[name]) != int(old_tail[name]):
                raise AssertionError(f"v0.45 relocated payload drifted: {name}")

        return {
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
                "surviving_free_head": [free_successor_page, free_successor_inc],
            },
            **checks,
            "payload_preserved": True,
            "surviving_free_identity_preserved": True,
            "surviving_free_predecessor_cleared": True,
            "queued_predecessor_consistent": True,
        }


def _relocation_crash_matrix() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v045-crash-") as tmp:
        directory = Path(tmp)
        base_path = directory / "base.pages"
        fixture = _prepare_fixture(
            base_path, MultiFreeBidirectionalQueuedRetirementDescriptorPrimaryStore
        )
        keys = tuple(fixture["keys"])
        base = MultiFreeBidirectionalQueuedRetirementDescriptorPrimaryStore(str(base_path))
        pre = _semantic_state(base, keys)

        clean_path = directory / "clean.pages"
        _copy_v045(base_path, clean_path)
        clean = MultiFreeBidirectionalQueuedRetirementDescriptorPrimaryStore(str(clean_path))
        clean_trace = clean.evacuate_multi_free_bidirectional_queued_live_retirement_arena_tail_step()
        post = _semantic_state(clean, keys)
        if not clean_trace.released:
            raise AssertionError("v0.45 crash oracle did not release")

        cases = []
        for failpoint in RELOCATION_FAILPOINTS:
            crash_path = directory / f"crash-{failpoint}.pages"
            _copy_v045(base_path, crash_path)
            proc = _worker(crash_path, failpoint)
            if proc.returncode != -signal.SIGKILL:
                raise AssertionError(
                    f"v0.45 {failpoint} did not SIGKILL: {proc.returncode} {proc.stderr}"
                )
            crashed = MultiFreeBidirectionalQueuedRetirementDescriptorPrimaryStore(
                str(crash_path)
            )
            one, two = _recover_twice(crashed)
            recovered = _semantic_state(crashed, keys)
            expected = pre if failpoint in RELOCATION_PRECOMMIT else post
            if recovered != expected:
                raise AssertionError(f"v0.45 {failpoint} recovered wrong committed state")
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


def _scaling_case() -> dict[str, Any]:
    queue_rows = []
    for depth in (5, 6, 7, 8):
        with tempfile.TemporaryDirectory(prefix=f"dic-v045-qscale-{depth}-") as tmp:
            path = Path(tmp) / "primary.pages"
            _prepare_fixture(
                path,
                MultiFreeBidirectionalQueuedRetirementDescriptorPrimaryStore,
                depth=depth,
                free_depth=2,
            )
            store = MultiFreeBidirectionalQueuedRetirementDescriptorPrimaryStore(str(path))
            trace = store.evacuate_multi_free_bidirectional_queued_live_retirement_arena_tail_step()
            queue_rows.append({"depth": depth, **_work(trace)})

    free_rows = []
    for free_depth in (2, 3, 4, 5):
        with tempfile.TemporaryDirectory(prefix=f"dic-v045-fscale-{free_depth}-") as tmp:
            path = Path(tmp) / "primary.pages"
            _prepare_fixture(
                path,
                MultiFreeBidirectionalQueuedRetirementDescriptorPrimaryStore,
                depth=5,
                free_depth=free_depth,
            )
            store = MultiFreeBidirectionalQueuedRetirementDescriptorPrimaryStore(str(path))
            trace = store.evacuate_multi_free_bidirectional_queued_live_retirement_arena_tail_step()
            free_rows.append({"free_depth": free_depth, **_work(trace)})

    signature = lambda row: (
        row["descriptor_preads"],
        row["descriptor_pwrites"],
        row["predecessor_preads"],
        row["predecessor_pwrites"],
        row["descriptor_scans"],
        row["predecessor_scans"],
        row["relocations"],
    )
    if {signature(row) for row in queue_rows + free_rows} != {(12, 3, 8, 2, 0, 0, 1)}:
        raise AssertionError("v0.45 direct work grew with queue or FREE-chain depth")
    return {
        "queue_depths": [5, 6, 7, 8],
        "free_depths": [2, 3, 4, 5],
        "queue_rows": queue_rows,
        "free_rows": free_rows,
        "constant_direct_work": True,
    }


def run_multi_free_bidirectional_queued_retirement_descriptor_experiment() -> dict[str, Any]:
    return {
        "experiment": "v0.45-multiple-free-bidirectional-queued-composition",
        "survived": True,
        "v044_control": _v044_control_case(),
        "candidate": _candidate_case(),
        "relocation_crashes": _relocation_crash_matrix(),
        "scaling": _scaling_case(),
        "claim_boundary": {
            "minimum_live_queue_depth": 5,
            "physical_tail_successor_is_queue_tail": True,
            "minimum_free_chain_depth": 2,
            "relocation_destination_is_free_head": True,
            "tagged_predecessor_per_queued_descriptor": True,
            "queue_or_history_walk": False,
            "free_chain_walk": False,
            "single_writer": True,
        },
    }
