from __future__ import annotations

import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

from simulator.retired_generation_reclamation import _sparse_copy
from storage.fixed_page_primary import PAGE_SIZE
from storage.segregated_retirement_descriptor_primary import (
    SegregatedRetirementDescriptorPrimaryStore,
)

ROOT = Path(__file__).resolve().parent.parent
WORKER = ROOT / "segregated_retirement_descriptor_worker.py"
SEGMENT_BUDGET = 2

FRESH_EMPTY_FAILPOINTS = (
    "retirement_arena_descriptor_written",
    "retirement_arena_synced",
    "pages_written",
    "data_synced",
    "committed",
)
FRESH_NONEMPTY_FAILPOINTS = (
    "retirement_arena_descriptor_written",
    "retirement_tail_linked",
    "retirement_arena_synced",
    "pages_written",
    "data_synced",
    "committed",
)
PARTIAL_RECLAIM_FAILPOINTS = (
    "mapping_unlinked",
    "free_header_written",
    "retirement_descriptor_updated",
    "retirement_arena_synced",
    "dependencies_synced",
    "committed",
)
RESET_FAILPOINTS = (
    "mapping_unlinked",
    "free_header_written",
    "retirement_arena_reset_staged",
    "retirement_dequeued",
    "dependencies_synced",
    "committed",
    "retirement_arena_reset_committed",
    "retirement_arena_truncated",
    "retirement_arena_reset_synced",
)


def _worker(
    path: Path,
    command: str,
    *,
    failpoint: str,
    key: str | None = None,
    budget: int | None = None,
) -> subprocess.CompletedProcess[str]:
    argv = [sys.executable, str(WORKER), "--file", str(path), command]
    if key is not None:
        argv.extend(["--key", key])
    if budget is not None:
        argv.extend(["--budget", str(budget)])
    argv.extend(["--failpoint", failpoint])
    return subprocess.run(
        argv,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _require_sigkill(proc: subprocess.CompletedProcess[str], label: str) -> None:
    if proc.returncode != -signal.SIGKILL:
        raise AssertionError(f"{label} did not SIGKILL: {proc.returncode} {proc.stderr}")


def _copy_store(source: Path, destination: Path) -> None:
    _sparse_copy(source, destination)
    source_arena = SegregatedRetirementDescriptorPrimaryStore.arena_path_for(source)
    destination_arena = SegregatedRetirementDescriptorPrimaryStore.arena_path_for(destination)
    shutil.copyfile(source_arena, destination_arena)


def _semantic_state(
    store: SegregatedRetirementDescriptorPrimaryStore,
    keys: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "state": store.committed_state(keys),
        "queue": store.retirement_queue_snapshot(),
    }


def _require_scan_free(recovery: dict[str, Any]) -> None:
    if int(recovery["logical_work"]) != 0:
        raise AssertionError("v0.37 recovery required logical redo")
    if int(recovery["generation_pages_scanned"]) != 0:
        raise AssertionError("v0.37 recovery scanned generation pages")
    if int(recovery["mapping_nodes_scanned"]) != 0:
        raise AssertionError("v0.37 recovery scanned radix nodes")
    if int(recovery.get("retirement_descriptors_scanned", 0)) != 0:
        raise AssertionError("v0.37 recovery scanned retirement descriptors")


def _recover_twice(
    store: SegregatedRetirementDescriptorPrimaryStore,
) -> tuple[dict[str, Any], dict[str, Any]]:
    one = store.recover()
    two = store.recover()
    _require_scan_free(one)
    _require_scan_free(two)
    if int(two["physical_truncated_bytes"]) != 0:
        raise AssertionError("v0.37 second recovery changed primary physical length")
    if int(two["retirement_descriptor_arena_truncated_bytes"]) != 0:
        raise AssertionError("v0.37 second recovery changed descriptor arena length")
    return one, two


def _drain_all(store: SegregatedRetirementDescriptorPrimaryStore) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    while store.retirement_queue_snapshot()["queue_count"]:
        trace = store.reclaim_step(budget=SEGMENT_BUDGET)
        if trace.reclaimed_segments > SEGMENT_BUDGET:
            raise AssertionError("v0.37 reclaim exceeded fixed segment budget")
        rows.append(trace.to_dict())
    return rows


def _build_fresh_empty_fixture(path: Path) -> None:
    store = SegregatedRetirementDescriptorPrimaryStore(str(path))
    store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=512)
    for index in range(16):
        store.insert(f"k-{index:03d}")
    queue = store.retirement_queue_snapshot()
    if queue["queue_count"] != 0 or queue["descriptor_arena_pages"] != 0:
        raise AssertionError("fresh v0.37 empty fixture already owns descriptor arena pages")


def _build_fresh_nonempty_fixture(path: Path) -> None:
    store = SegregatedRetirementDescriptorPrimaryStore(str(path))
    store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=512)
    for index in range(32):
        store.insert(f"k-{index:03d}")
    queue = store.retirement_queue_snapshot()
    if queue["queue_count"] != 1 or queue["descriptor_arena_pages"] != 2:
        raise AssertionError("fresh v0.37 non-empty fixture has wrong queue/arena depth")


def _build_partial_reclaim_fixture(path: Path) -> None:
    store = SegregatedRetirementDescriptorPrimaryStore(str(path))
    store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=512)
    for index in range(65):
        store.insert(f"k-{index:03d}")
    while True:
        queue = store.retirement_queue_snapshot()
        if queue["queue_count"] <= 0:
            raise AssertionError("could not find multi-segment v0.37 retirement head")
        remaining = int(queue["descriptors"][0]["remaining_segments"])
        if remaining > 1:
            return
        store.reclaim_step(budget=remaining)


def _build_final_reset_fixture(path: Path) -> None:
    store = SegregatedRetirementDescriptorPrimaryStore(str(path))
    store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=512)
    for index in range(65):
        store.insert(f"k-{index:03d}")
    while True:
        queue = store.retirement_queue_snapshot()
        if queue["queue_count"] == 1:
            remaining = int(queue["descriptors"][0]["remaining_segments"])
            if remaining == 1:
                if queue["descriptor_free_count"] != 2:
                    raise AssertionError("v0.37 final reset fixture lacks two prior free descriptors")
                if queue["descriptor_arena_pages"] != 6:
                    raise AssertionError("v0.37 final reset fixture lacks six arena pages")
                return
            store.reclaim_step(budget=1)
            continue
        store.reclaim_step(budget=SEGMENT_BUDGET)


def _run_insert_matrix(
    *,
    fixture_builder: Callable[[Path], None],
    trigger_key: str,
    keys_before: tuple[str, ...],
    failpoints: tuple[str, ...],
    prefix: str,
    expected_arena_pages_after: int,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=prefix) as tmp:
        directory = Path(tmp)
        base_path = directory / "base.pages"
        fixture_builder(base_path)
        base = SegregatedRetirementDescriptorPrimaryStore(str(base_path))
        keys = keys_before + (trigger_key,)
        pre = _semantic_state(base, keys)

        clean_path = directory / "clean.pages"
        _copy_store(base_path, clean_path)
        clean = SegregatedRetirementDescriptorPrimaryStore(str(clean_path))
        clean_trace = clean.insert(trigger_key)
        post = _semantic_state(clean, keys)
        if clean_trace.retirement_descriptors_enqueued != 1:
            raise AssertionError("clean v0.37 enqueue did not retire one generation")
        if clean.retirement_queue_snapshot()["descriptor_arena_pages"] != expected_arena_pages_after:
            raise AssertionError("clean v0.37 enqueue arena length drifted")

        rows: list[dict[str, Any]] = []
        for failpoint in failpoints:
            crash_path = directory / f"{failpoint}.pages"
            _copy_store(base_path, crash_path)
            proc = _worker(crash_path, "insert", key=trigger_key, failpoint=failpoint)
            _require_sigkill(proc, f"v0.37 enqueue failpoint {failpoint}")
            crashed = SegregatedRetirementDescriptorPrimaryStore(str(crash_path))
            observed = _semantic_state(crashed, keys)
            expected = post if failpoint == "committed" else pre
            if observed != expected:
                raise AssertionError(f"v0.37 enqueue crash state drifted at {failpoint}")
            recovery_one, recovery_two = _recover_twice(crashed)
            rows.append(
                {
                    "failpoint": failpoint,
                    "expected_committed": failpoint == "committed",
                    "exact_committed_state_match": True,
                    "recovery_one": recovery_one,
                    "recovery_two": recovery_two,
                }
            )
        return {
            "failpoints": list(failpoints),
            "case_count": len(rows),
            "clean_trace": clean_trace.to_dict(),
            "rows": rows,
            "all_exact_committed_state_match": True,
            "all_recovery_scan_free": True,
            "all_second_recovery_idempotent": True,
        }


def _run_reclaim_matrix(
    *,
    fixture_builder: Callable[[Path], None],
    keys: tuple[str, ...],
    failpoints: tuple[str, ...],
    prefix: str,
    expect_reset: bool,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=prefix) as tmp:
        directory = Path(tmp)
        base_path = directory / "base.pages"
        fixture_builder(base_path)
        base = SegregatedRetirementDescriptorPrimaryStore(str(base_path))
        visible_keys = tuple(key for key in keys if base.lookup(key).found)
        pre = _semantic_state(base, visible_keys)

        clean_path = directory / "clean.pages"
        _copy_store(base_path, clean_path)
        clean = SegregatedRetirementDescriptorPrimaryStore(str(clean_path))
        clean_trace = clean.reclaim_step(budget=1)
        post = _semantic_state(clean, visible_keys)
        if expect_reset:
            if clean_trace.retirement_arena_pages_released != 6:
                raise AssertionError("clean v0.37 reset did not release the six-page peak arena")
            if clean.retirement_queue_snapshot()["descriptor_arena_pages"] != 0:
                raise AssertionError("clean v0.37 reset retained committed arena pages")
            if clean.descriptor_arena_diagnostic()["arena_file_bytes"] != 0:
                raise AssertionError("clean v0.37 reset did not physically truncate arena")
        elif clean_trace.retirement_descriptor_pwrites != 1:
            raise AssertionError("clean v0.37 partial reclaim did not rewrite arena descriptor")

        rows: list[dict[str, Any]] = []
        for failpoint in failpoints:
            crash_path = directory / f"{failpoint}.pages"
            _copy_store(base_path, crash_path)
            proc = _worker(crash_path, "reclaim", budget=1, failpoint=failpoint)
            _require_sigkill(proc, f"v0.37 reclaim failpoint {failpoint}")
            crashed = SegregatedRetirementDescriptorPrimaryStore(str(crash_path))
            observed = _semantic_state(crashed, visible_keys)
            committed = failpoint in {
                "committed",
                "retirement_arena_reset_committed",
                "retirement_arena_truncated",
                "retirement_arena_reset_synced",
            }
            expected = post if committed else pre
            if observed != expected:
                raise AssertionError(f"v0.37 reclaim crash state drifted at {failpoint}")
            for key in visible_keys:
                if not crashed.lookup(key).found:
                    raise AssertionError("v0.37 reclaim crash hid a live key")
            before_recovery = crashed.descriptor_arena_diagnostic()
            recovery_one, recovery_two = _recover_twice(crashed)
            after_recovery = crashed.descriptor_arena_diagnostic()
            if committed and expect_reset and after_recovery["arena_file_bytes"] != 0:
                raise AssertionError("v0.37 reset crash recovery retained arena residue")
            rows.append(
                {
                    "failpoint": failpoint,
                    "expected_committed": committed,
                    "exact_committed_state_match": True,
                    "all_live_keys_visible": True,
                    "arena_before_recovery": before_recovery,
                    "arena_after_recovery": after_recovery,
                    "recovery_one": recovery_one,
                    "recovery_two": recovery_two,
                }
            )
        return {
            "failpoints": list(failpoints),
            "case_count": len(rows),
            "clean_trace": clean_trace.to_dict(),
            "rows": rows,
            "all_exact_committed_state_match": True,
            "all_live_keys_visible": True,
            "all_recovery_scan_free": True,
            "all_second_recovery_idempotent": True,
        }


def _peak_release_case() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v037-peak-release-") as tmp:
        path = Path(tmp) / "primary.pages"
        store = SegregatedRetirementDescriptorPrimaryStore(str(path))
        store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=4096)
        for index in range(65):
            store.insert(f"k-{index:03d}")
        before = store.retirement_queue_snapshot()
        arena_before = store.descriptor_arena_diagnostic()
        if before["descriptor_pool_count"] != 3 or before["descriptor_arena_pages"] != 6:
            raise AssertionError("v0.37 peak fixture did not allocate three descriptors")
        traces = _drain_all(store)
        after = store.retirement_queue_snapshot()
        arena_after = store.descriptor_arena_diagnostic()
        if after["queue_count"] != 0 or after["descriptor_pool_count"] != 0:
            raise AssertionError("v0.37 drain did not clear descriptor identities")
        if after["descriptor_arena_pages"] != 0 or arena_after["arena_file_bytes"] != 0:
            raise AssertionError("v0.37 drain did not return arena file length")
        released = sum(int(row["retirement_arena_pages_released"]) for row in traces)
        if released != 6:
            raise AssertionError("v0.37 drain released wrong arena page count")
        if any(int(row["retirement_descriptors_scanned"]) != 0 for row in traces):
            raise AssertionError("v0.37 reclaim scanned descriptor history")
        return {
            "queue_before_drain": before,
            "arena_before_drain": arena_before,
            "reclaim_steps": traces,
            "queue_after_drain": after,
            "arena_after_drain": arena_after,
            "peak_descriptor_pairs": 3,
            "peak_arena_pages": 6,
            "peak_arena_bytes": 6 * PAGE_SIZE,
            "released_arena_pages": released,
            "released_arena_bytes": released * PAGE_SIZE,
            "candidate_descriptor_history_walks": 0,
            "candidate_relocations": 0,
        }


def _identity_after_reset_case() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v037-aba-reset-") as tmp:
        path = Path(tmp) / "primary.pages"
        store = SegregatedRetirementDescriptorPrimaryStore(str(path))
        store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=512)
        for index in range(17):
            store.insert(f"k-{index:03d}")
        first = store.retirement_queue_snapshot()
        if first["queue_count"] != 1:
            raise AssertionError("v0.37 ABA fixture lacks first descriptor")
        first_page = int(first["head_page"])
        first_incarnation = int(first["head_incarnation"])
        _drain_all(store)
        if store.descriptor_arena_diagnostic()["arena_file_bytes"] != 0:
            raise AssertionError("v0.37 ABA fixture did not truncate first arena")

        second = None
        trigger_index = None
        for index in range(17, 257):
            store.insert(f"k-{index:03d}")
            queue = store.retirement_queue_snapshot()
            if queue["queue_count"]:
                second = queue
                trigger_index = index
                break
        if second is None or trigger_index is None:
            raise AssertionError("v0.37 ABA fixture did not allocate a second descriptor")
        second_page = int(second["head_page"])
        second_incarnation = int(second["head_incarnation"])
        if second_page != first_page:
            raise AssertionError("v0.37 ABA fixture did not reuse arena page zero after reset")
        if second_incarnation <= first_incarnation:
            raise AssertionError("v0.37 global descriptor incarnation did not advance")

        stale_rejected = False
        try:
            store.retirement_descriptor_reference(first_page, first_incarnation)
        except RuntimeError:
            stale_rejected = True
        if not stale_rejected:
            raise AssertionError("v0.37 stale pre-reset descriptor identity aliased new record")
        current = store.retirement_descriptor_reference(second_page, second_incarnation)
        return {
            "first_page": first_page,
            "first_incarnation": first_incarnation,
            "second_page": second_page,
            "second_incarnation": second_incarnation,
            "second_trigger_key_index": trigger_index,
            "same_arena_page_reused": second_page == first_page,
            "incarnation_advanced": second_incarnation > first_incarnation,
            "stale_identity_rejected": stale_rejected,
            "current_identity_generation": int(current["generation"]),
        }


def _history_cycles_case() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v037-history-cycles-") as tmp:
        path = Path(tmp) / "primary.pages"
        store = SegregatedRetirementDescriptorPrimaryStore(str(path))
        store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=4096)
        cycles: list[dict[str, Any]] = []
        for index in range(257):
            trace = store.insert(f"k-{index:03d}")
            if trace.retirement_descriptors_enqueued:
                before = store.retirement_queue_snapshot()
                arena_before = store.descriptor_arena_diagnostic()
                reclaim_rows = _drain_all(store)
                after = store.retirement_queue_snapshot()
                arena_after = store.descriptor_arena_diagnostic()
                cycles.append(
                    {
                        "trigger_index": index,
                        "arena_pages_before_drain": int(before["descriptor_arena_pages"]),
                        "arena_file_bytes_before_drain": int(arena_before["arena_file_bytes"]),
                        "arena_pages_after_drain": int(after["descriptor_arena_pages"]),
                        "arena_file_bytes_after_drain": int(arena_after["arena_file_bytes"]),
                        "reclaim_steps": len(reclaim_rows),
                    }
                )
        if len(cycles) < 4:
            raise AssertionError("v0.37 history control did not cross four growth cycles")
        if any(row["arena_pages_after_drain"] != 0 for row in cycles):
            raise AssertionError("v0.37 history control retained committed arena capacity")
        if any(row["arena_file_bytes_after_drain"] != 0 for row in cycles):
            raise AssertionError("v0.37 history control retained physical arena length")
        return {
            "cycle_count": len(cycles),
            "cycles": cycles,
            "all_cycles_return_to_zero_committed_arena_pages": True,
            "all_cycles_return_to_zero_arena_file_bytes": True,
        }


def _crash_matrices() -> dict[str, Any]:
    fresh_empty = _run_insert_matrix(
        fixture_builder=_build_fresh_empty_fixture,
        trigger_key="k-016",
        keys_before=tuple(f"k-{index:03d}" for index in range(16)),
        failpoints=FRESH_EMPTY_FAILPOINTS,
        prefix="dic-v037-fresh-empty-crash-",
        expected_arena_pages_after=2,
    )
    fresh_nonempty = _run_insert_matrix(
        fixture_builder=_build_fresh_nonempty_fixture,
        trigger_key="k-032",
        keys_before=tuple(f"k-{index:03d}" for index in range(32)),
        failpoints=FRESH_NONEMPTY_FAILPOINTS,
        prefix="dic-v037-fresh-nonempty-crash-",
        expected_arena_pages_after=4,
    )
    partial = _run_reclaim_matrix(
        fixture_builder=_build_partial_reclaim_fixture,
        keys=tuple(f"k-{index:03d}" for index in range(65)),
        failpoints=PARTIAL_RECLAIM_FAILPOINTS,
        prefix="dic-v037-partial-reclaim-crash-",
        expect_reset=False,
    )
    reset = _run_reclaim_matrix(
        fixture_builder=_build_final_reset_fixture,
        keys=tuple(f"k-{index:03d}" for index in range(65)),
        failpoints=RESET_FAILPOINTS,
        prefix="dic-v037-final-reset-crash-",
        expect_reset=True,
    )
    return {
        "fresh_empty_queue": fresh_empty,
        "fresh_nonempty_queue": fresh_nonempty,
        "partial_head": partial,
        "final_arena_reset": reset,
        "case_count": sum(
            int(row["case_count"])
            for row in (fresh_empty, fresh_nonempty, partial, reset)
        ),
    }


def run_segregated_descriptor_arena_experiment() -> dict[str, Any]:
    peak_release = _peak_release_case()
    identity = _identity_after_reset_case()
    history = _history_cycles_case()
    crashes = _crash_matrices()
    return {
        "experiment": "v0.37-segregated-retirement-descriptor-arena",
        "hypothesis": (
            "placing retirement descriptors in an independently truncatable arena and publishing arena length "
            "plus a monotonic global incarnation in the primary superblock can return all post-drain descriptor "
            "capacity with O(1) reset metadata, without descriptor-history scans or ABA aliasing"
        ),
        "peak_release": peak_release,
        "identity_after_reset": identity,
        "history_cycles": history,
        "crash_matrices": crashes,
        "candidate_descriptor_history_walks": 0,
        "candidate_relocations": 0,
        "survived": True,
        "nonclaims": [
            "does not release excess arena capacity while any retirement descriptor remains queued",
            "does not establish filesystem allocated-block deallocation beyond observed sidecar file-length truncation",
            "does not establish hardware power-loss or torn-write safety from process SIGKILL evidence",
            "retirement descriptor incarnation remains a finite uint64 namespace",
            "diagnostic queue snapshots may traverse descriptor chains and are not candidate foreground work",
        ],
    }
