from __future__ import annotations

import json
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from simulator.retired_generation_reclamation import _sparse_copy
from storage.aligned_generation_reclaiming_primary import (
    AlignedGenerationReclaimingPrimaryStore,
)
from storage.segmented_fixed_page_primary import SEGMENT_BUCKET_PAGES

ROOT = Path(__file__).resolve().parent.parent
WORKER = ROOT / "generation_boundary_worker.py"
CONTROL_CAPACITIES = (32, 128, 2048, 131072, 4194304)
FIRST_INSERT_FAILPOINTS = (
    "allocated",
    "pages_written",
    "data_synced",
    "committed",
)
REUSE_INSERT_FAILPOINTS = (
    "reused_extent_scrubbed",
    "pages_written",
    "data_synced",
    "committed",
)
RECLAIM_FAILPOINTS = (
    "mapping_unlinked",
    "free_header_written",
    "dependencies_synced",
    "committed",
)


def _generation_pages(capacity: int, bucket_size: int = 4) -> int:
    return int(capacity) // int(bucket_size) + 1


def _align(page_id: int) -> int:
    width = SEGMENT_BUCKET_PAGES
    return ((int(page_id) + width - 1) // width) * width


def run_boundary_control() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for capacity in CONTROL_CAPACITIES:
        old_pages = _generation_pages(capacity)
        unaligned_new_base = old_pages
        aligned_new_base = _align(unaligned_new_base)
        old_last_segment = (old_pages - 1) // SEGMENT_BUCKET_PAGES
        unaligned_new_first_segment = unaligned_new_base // SEGMENT_BUCKET_PAGES
        aligned_new_first_segment = aligned_new_base // SEGMENT_BUCKET_PAGES
        row = {
            "old_capacity": capacity,
            "old_generation_pages": old_pages,
            "old_last_segment": old_last_segment,
            "unaligned_new_base_page": unaligned_new_base,
            "unaligned_new_first_segment": unaligned_new_first_segment,
            "unaligned_shares_boundary_segment": (
                unaligned_new_first_segment == old_last_segment
            ),
            "aligned_new_base_page": aligned_new_base,
            "aligned_new_first_segment": aligned_new_first_segment,
            "aligned_shares_boundary_segment": (
                aligned_new_first_segment == old_last_segment
            ),
            "alignment_padding_pages": aligned_new_base - unaligned_new_base,
        }
        if not row["unaligned_shares_boundary_segment"]:
            raise AssertionError("unaligned control unexpectedly avoided the boundary segment")
        if row["aligned_shares_boundary_segment"]:
            raise AssertionError("aligned candidate still shares a boundary segment")
        if int(row["alignment_padding_pages"]) >= SEGMENT_BUCKET_PAGES:
            raise AssertionError("alignment overhead escaped one-segment bound")
        rows.append(row)
    return {
        "capacities": list(CONTROL_CAPACITIES),
        "segment_pages": SEGMENT_BUCKET_PAGES,
        "rows": rows,
        "max_alignment_padding_pages": max(
            int(row["alignment_padding_pages"]) for row in rows
        ),
    }


def _drain_reclamation(
    store: AlignedGenerationReclaimingPrimaryStore,
    *,
    budget: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    while int(store.generation_layout_snapshot()["retire_remaining"]):
        trace = store.reclaim_step(budget=budget)
        if trace.reclaimed_segments > budget:
            raise AssertionError("generation-integrated reclaim exceeded fixed budget")
        if trace.physical_pages_appended != 0:
            raise AssertionError("generation-integrated reclaim appended storage")
        if trace.generation_pages_scanned or trace.mapping_nodes_scanned or trace.logical_redo:
            raise AssertionError("generation-integrated reclaim introduced scan/redo work")
        rows.append(trace.to_dict())
    return rows


def run_real_two_generation_cycles() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v033-cycles-") as tmp:
        path = Path(tmp) / "primary.pages"
        store = AlignedGenerationReclaimingPrimaryStore(path)
        store.initialize(
            initial_capacity=32,
            max_load=0.50,
            migration_slot_budget=64,
        )

        first_traces = [store.insert(f"k-{index:03d}") for index in range(17)]
        first_trigger = first_traces[-1]
        if not first_trigger.migration_started or not first_trigger.migration_completed:
            raise AssertionError("first real migration did not start and complete")
        if first_trigger.generation_alignment_padding_pages != 7:
            raise AssertionError("first generation alignment padding drifted")
        first_after = store.generation_layout_snapshot()
        if int(first_after["current"]["base_page"]) != 16:
            raise AssertionError("first aligned generation base drifted")
        if int(first_after["retire_remaining"]) <= 0:
            raise AssertionError("first real migration did not publish retirement ownership")

        first_reclaim = _drain_reclamation(store, budget=2)
        free_after_first = int(store.generation_layout_snapshot()["free_count"])
        if free_after_first <= 0:
            raise AssertionError("first retired generation produced no reusable extent")
        for index in range(17):
            if not store.lookup(f"k-{index:03d}").found:
                raise AssertionError("first cleanup lost a live key")

        for index in range(17, 32):
            trace = store.insert(f"k-{index:03d}")
            if trace.migration_started:
                raise AssertionError("second migration started before threshold fixture")

        second_trigger = store.insert("k-032")
        if not second_trigger.migration_started:
            raise AssertionError("second migration did not start at expected threshold")
        if second_trigger.migration_completed:
            raise AssertionError("second migration control unexpectedly completed in one step")
        active = store.generation_layout_snapshot()
        if active["old"] is None:
            raise AssertionError("second migration did not expose an active old generation")
        old_last = int(active["old"]["last_segment"])
        current_first = int(active["current"]["first_segment"])
        if current_first <= old_last:
            raise AssertionError("real aligned generations overlap a mapping segment")
        if int(active["current"]["base_page"]) != 48:
            raise AssertionError("second aligned generation base drifted")
        if second_trigger.generation_alignment_padding_pages != 15:
            raise AssertionError("second alignment padding drifted")

        second_completion = store.insert("k-033")
        if not second_completion.migration_completed:
            raise AssertionError("second migration did not complete under bounded second step")
        second_after = store.generation_layout_snapshot()
        if int(second_after["retire_remaining"]) <= 0:
            raise AssertionError("second migration did not publish retired ownership")

        reuse_count = (
            int(second_trigger.reused_free_extents)
            + int(second_completion.reused_free_extents)
        )
        scrub_writes = (
            int(second_trigger.data_page_scrub_pwrites)
            + int(second_completion.data_page_scrub_pwrites)
        )
        if reuse_count <= 0 or scrub_writes < 32:
            raise AssertionError("second migration did not safely reuse a reclaimed extent")

        second_reclaim = _drain_reclamation(store, budget=3)
        for index in range(34):
            if not store.lookup(f"k-{index:03d}").found:
                raise AssertionError("second cleanup lost a live key")

        return {
            "first_trigger": first_trigger.to_dict(),
            "first_after_migration": first_after,
            "first_reclaim_steps": first_reclaim,
            "free_after_first_reclaim": free_after_first,
            "second_trigger": second_trigger.to_dict(),
            "second_active_layout": active,
            "second_completion": second_completion.to_dict(),
            "second_after_migration": second_after,
            "second_reclaim_steps": second_reclaim,
            "second_reused_free_extents": reuse_count,
            "second_scrub_pwrites": scrub_writes,
            "all_34_keys_visible": True,
            "max_reclaim_segments_per_step": max(
                [int(row["reclaimed_segments"]) for row in first_reclaim + second_reclaim]
                or [0]
            ),
            "max_reclaim_pages_appended": max(
                [int(row["physical_pages_appended"]) for row in first_reclaim + second_reclaim]
                or [0]
            ),
        }


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


def _semantic_state(
    store: AlignedGenerationReclaimingPrimaryStore,
    keys: tuple[str, ...],
) -> dict[str, Any]:
    return store.committed_state(keys)


def _require_scan_free(recovery: dict[str, Any]) -> None:
    if int(recovery["logical_work"]) != 0:
        raise AssertionError("recovery required logical redo")
    if int(recovery["generation_pages_scanned"]) != 0:
        raise AssertionError("recovery scanned generation pages")
    if int(recovery["mapping_nodes_scanned"]) != 0:
        raise AssertionError("recovery scanned radix nodes")


def _run_insert_crash_matrix(
    *,
    fixture_builder,
    trigger_key: str,
    keys_before: tuple[str, ...],
    failpoints: tuple[str, ...],
    prefix: str,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=prefix) as tmp:
        directory = Path(tmp)
        base_path = directory / "base.pages"
        fixture_builder(base_path)
        base = AlignedGenerationReclaimingPrimaryStore(base_path)
        pre = _semantic_state(base, keys_before + (trigger_key,))

        clean_path = directory / "clean.pages"
        _sparse_copy(base_path, clean_path)
        clean = AlignedGenerationReclaimingPrimaryStore(clean_path)
        clean_trace = clean.insert(trigger_key)
        post = _semantic_state(clean, keys_before + (trigger_key,))

        rows: list[dict[str, Any]] = []
        for failpoint in failpoints:
            crash_path = directory / f"{failpoint}.pages"
            _sparse_copy(base_path, crash_path)
            proc = _worker(
                crash_path,
                "insert",
                key=trigger_key,
                failpoint=failpoint,
            )
            if proc.returncode != -signal.SIGKILL:
                raise AssertionError(
                    f"insert failpoint {failpoint} did not SIGKILL: {proc.returncode} {proc.stderr}"
                )
            crashed = AlignedGenerationReclaimingPrimaryStore(crash_path)
            observed = _semantic_state(crashed, keys_before + (trigger_key,))
            expected = post if failpoint == "committed" else pre
            exact = observed == expected
            if not exact:
                raise AssertionError(f"insert crash state drifted at {failpoint}")
            recovery_one = crashed.recover()
            recovery_two = crashed.recover()
            _require_scan_free(recovery_one)
            _require_scan_free(recovery_two)
            if int(recovery_two["physical_truncated_bytes"]) != 0:
                raise AssertionError("second recovery ceased to be physically idempotent")
            rows.append(
                {
                    "failpoint": failpoint,
                    "expected_committed": failpoint == "committed",
                    "exact_committed_state_match": exact,
                    "recovery_one": recovery_one,
                    "recovery_two": recovery_two,
                }
            )
        return {
            "failpoints": list(failpoints),
            "case_count": len(rows),
            "clean_trace": clean_trace.to_dict(),
            "rows": rows,
            "all_exact_committed_state_match": all(
                bool(row["exact_committed_state_match"]) for row in rows
            ),
            "all_recovery_scan_free": True,
            "all_second_recovery_idempotent": True,
        }


def _build_first_trigger_fixture(path: Path) -> None:
    store = AlignedGenerationReclaimingPrimaryStore(path)
    store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=64)
    for index in range(16):
        store.insert(f"k-{index:03d}")


def _build_reuse_trigger_fixture(path: Path) -> None:
    store = AlignedGenerationReclaimingPrimaryStore(path)
    store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=64)
    for index in range(17):
        store.insert(f"k-{index:03d}")
    _drain_reclamation(store, budget=2)
    for index in range(17, 32):
        store.insert(f"k-{index:03d}")
    if int(store.generation_layout_snapshot()["free_count"]) <= 0:
        raise AssertionError("reuse crash fixture has no free extent")


def run_insert_crash_matrices() -> dict[str, Any]:
    first = _run_insert_crash_matrix(
        fixture_builder=_build_first_trigger_fixture,
        trigger_key="k-016",
        keys_before=tuple(f"k-{index:03d}" for index in range(16)),
        failpoints=FIRST_INSERT_FAILPOINTS,
        prefix="dic-v033-first-crash-",
    )
    reuse = _run_insert_crash_matrix(
        fixture_builder=_build_reuse_trigger_fixture,
        trigger_key="k-032",
        keys_before=tuple(f"k-{index:03d}" for index in range(32)),
        failpoints=REUSE_INSERT_FAILPOINTS,
        prefix="dic-v033-reuse-crash-",
    )
    if int(reuse["clean_trace"]["reused_free_extents"]) <= 0:
        raise AssertionError("reuse crash control did not reuse an extent")
    if int(reuse["clean_trace"]["data_page_scrub_pwrites"]) < 32:
        raise AssertionError("reuse crash control did not scrub reclaimed data")
    return {"first_migration": first, "reuse_migration": reuse}


def run_reclaim_crash_matrix() -> dict[str, Any]:
    keys = tuple(f"k-{index:03d}" for index in range(17))
    with tempfile.TemporaryDirectory(prefix="dic-v033-reclaim-crash-") as tmp:
        directory = Path(tmp)
        base_path = directory / "base.pages"
        store = AlignedGenerationReclaimingPrimaryStore(base_path)
        store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=64)
        for key in keys:
            store.insert(key)
        if int(store.generation_layout_snapshot()["retire_remaining"]) <= 0:
            raise AssertionError("reclaim crash fixture has no retirement backlog")
        pre = _semantic_state(store, keys)

        clean_path = directory / "clean.pages"
        _sparse_copy(base_path, clean_path)
        clean = AlignedGenerationReclaimingPrimaryStore(clean_path)
        clean_trace = clean.reclaim_step(budget=1)
        post = _semantic_state(clean, keys)
        for key in keys:
            if not clean.lookup(key).found:
                raise AssertionError("clean reclaim lost a live current-generation key")

        rows: list[dict[str, Any]] = []
        for failpoint in RECLAIM_FAILPOINTS:
            crash_path = directory / f"{failpoint}.pages"
            _sparse_copy(base_path, crash_path)
            proc = _worker(
                crash_path,
                "reclaim",
                budget=1,
                failpoint=failpoint,
            )
            if proc.returncode != -signal.SIGKILL:
                raise AssertionError(
                    f"reclaim failpoint {failpoint} did not SIGKILL: {proc.returncode} {proc.stderr}"
                )
            crashed = AlignedGenerationReclaimingPrimaryStore(crash_path)
            observed = _semantic_state(crashed, keys)
            expected = post if failpoint == "committed" else pre
            if observed != expected:
                raise AssertionError(f"reclaim crash state drifted at {failpoint}")
            for key in keys:
                if not crashed.lookup(key).found:
                    raise AssertionError("reclaim crash hid a live current-generation key")
            recovery_one = crashed.recover()
            recovery_two = crashed.recover()
            _require_scan_free(recovery_one)
            _require_scan_free(recovery_two)
            rows.append(
                {
                    "failpoint": failpoint,
                    "expected_committed": failpoint == "committed",
                    "exact_committed_state_match": True,
                    "all_live_keys_visible": True,
                    "recovery_one": recovery_one,
                    "recovery_two": recovery_two,
                }
            )
        return {
            "failpoints": list(RECLAIM_FAILPOINTS),
            "case_count": len(rows),
            "clean_trace": clean_trace.to_dict(),
            "rows": rows,
            "all_exact_committed_state_match": True,
            "all_live_keys_visible": True,
            "all_recovery_scan_free": True,
        }
