from __future__ import annotations

import json
import signal
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from storage.segmented_fixed_page_primary import (
    PAGE_SIZE,
    RADIX_LEVELS,
    RADIX_NODE_COPIES,
    DATA_PAGE_COPIES,
    SEGMENT_BUCKET_PAGES,
)
from storage.transactional_segmented_primary import SegmentedFixedPagePrimaryStore

ROOT = Path(__file__).resolve().parent.parent
WORKER = ROOT / "segmented_fixed_page_primary_worker.py"

CAPACITIES = (32, 2_048, 131_072, 4_194_304)
FAILPOINTS = ("allocated", "pages_written", "data_synced", "committed")
PER_FRESH_SEGMENT_MAX_PAGES = (
    DATA_PAGE_COPIES * SEGMENT_BUCKET_PAGES
    + RADIX_NODE_COPIES * (RADIX_LEVELS - 1)
)
DEFAULT_MAX_KICKS = 32
DEFAULT_MIGRATION_BUDGET = 8
DERIVED_TRANSACTION_SEGMENT_CAP = (
    (1 + DEFAULT_MIGRATION_BUDGET) * (DEFAULT_MAX_KICKS + 1)
    + DEFAULT_MIGRATION_BUDGET
)
SINGLE_GENERATION_LOOKUP_PREAD_BOUND = 2 + 3 * (2 * RADIX_LEVELS + 2)
TWO_GENERATION_LOOKUP_PREAD_BOUND = 2 + 6 * (2 * RADIX_LEVELS + 2)


@dataclass(frozen=True)
class IntegratedCrashCase:
    old_capacity: int
    failpoint: str
    expected_committed: bool
    target_key: str
    target_visible_after_crash: bool
    existing_seed_visible_after_crash: bool
    exact_committed_state_match: bool
    pre_epoch: int
    post_epoch: int
    crash_epoch: int
    uncommitted_tail_before_recovery_bytes: int
    uncommitted_tail_after_recovery_bytes: int
    control_physical_bytes_appended: int
    control_new_segments_allocated: int
    recovery_one: dict[str, Any]
    recovery_two: dict[str, Any]
    recovery_state_unchanged: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _worker(path: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(WORKER), "--file", str(path), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=check,
    )


def _json_stdout(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise AssertionError(f"worker produced no JSON output: {result.stderr!r}")
    return json.loads(lines[-1])


def _seed_key(capacity: int) -> str:
    return f"v030-seed-{capacity}"


def _target_key(capacity: int) -> str:
    return f"v030-target-{capacity}"


def prepare_capacity_fixture(
    path: Path, old_capacity: int
) -> tuple[SegmentedFixedPagePrimaryStore, str, str]:
    store = SegmentedFixedPagePrimaryStore(path)
    store.initialize(
        initial_capacity=old_capacity,
        max_load=1.5 / old_capacity,
        migration_slot_budget=DEFAULT_MIGRATION_BUDGET,
        max_kicks=DEFAULT_MAX_KICKS,
    )
    seed = _seed_key(old_capacity)
    target = _target_key(old_capacity)
    first = store.insert(seed)
    if first.migration_started or first.new_segments_allocated <= 0:
        raise AssertionError(f"v0.30 seed fixture drifted: {first.to_dict()}")
    meta = store.meta_snapshot()
    if meta["old_generation"] is not None or int(meta["current_rows"]) != 1:
        raise AssertionError(f"v0.30 pre-migration fixture drifted: {meta}")
    if not store.lookup(seed).found or store.lookup(target).found:
        raise AssertionError("v0.30 seed visibility fixture drifted")
    return store, seed, target


def run_capacity_case(old_capacity: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v030-capacity-") as tmp:
        store, seed, target = prepare_capacity_fixture(
            Path(tmp) / "segmented.pages", old_capacity
        )
        pre_meta = store.meta_snapshot()
        trace = store.insert(target)
        post_meta = store.meta_snapshot()
        if not trace.migration_started:
            raise AssertionError(f"v0.30 migration did not start at C={old_capacity}")
        if trace.fsyncs != 2:
            raise AssertionError(f"v0.30 commit barrier count drifted: {trace.to_dict()}")
        if trace.new_segments_allocated <= 0:
            raise AssertionError("v0.30 migration-start insert materialized no new segment")
        if trace.new_segments_allocated > trace.logical_pages_written:
            raise AssertionError("more segments were allocated than logical pages written")
        if trace.new_segments_allocated > DERIVED_TRANSACTION_SEGMENT_CAP:
            raise AssertionError("v0.30 transaction exceeded derived constant segment cap")
        if trace.physical_pages_appended > (
            PER_FRESH_SEGMENT_MAX_PAGES * trace.new_segments_allocated
        ):
            raise AssertionError("v0.30 fresh-segment append exceeded radix path bound")
        if trace.radix_node_pwrites > 8 * trace.new_segments_allocated:
            raise AssertionError("v0.30 radix metadata writes exceeded eight per fresh segment")
        if int(post_meta["uncommitted_tail_bytes"]) != 0:
            raise AssertionError("successful v0.30 commit left uncommitted file tail")
        if int(post_meta["current_base_page"]) <= int(pre_meta["current_base_page"]):
            raise AssertionError("v0.30 logical generation base did not advance")

        target_lookup = store.lookup(target)
        missing_lookup = store.lookup(f"v030-missing-{old_capacity}")
        if not target_lookup.found or not store.lookup(seed).found:
            raise AssertionError("v0.30 committed lookup lost fixture membership")
        if target_lookup.total_physical_preads > SINGLE_GENERATION_LOOKUP_PREAD_BOUND:
            raise AssertionError(
                f"v0.30 current-generation lookup exceeded fixed radix bound: {target_lookup}"
            )
        if missing_lookup.total_physical_preads > TWO_GENERATION_LOOKUP_PREAD_BOUND:
            raise AssertionError(
                f"v0.30 two-generation miss exceeded fixed radix bound: {missing_lookup}"
            )

        target_segments = list(target_lookup.logical_segment_ids)
        if not target_segments:
            raise AssertionError("v0.30 target lookup exposed no logical segment id")
        return {
            "old_capacity": old_capacity,
            "new_capacity": 2 * old_capacity,
            "logical_generation_base_page": int(post_meta["current_base_page"]),
            "target_logical_segment_id_max": max(target_segments),
            "new_segments_allocated": trace.new_segments_allocated,
            "physical_pages_appended": trace.physical_pages_appended,
            "physical_bytes_appended": trace.physical_bytes_appended,
            "radix_node_pwrites": trace.radix_node_pwrites,
            "metadata_physical_pwrites": trace.metadata_physical_pwrites,
            "migration_source_slots_scanned": trace.migration_source_slots_scanned,
            "migration_rows_moved": trace.migration_rows_moved,
            "lookup_total_preads": target_lookup.total_physical_preads,
            "missing_total_preads": missing_lookup.total_physical_preads,
            "committed_physical_frontier_bytes": int(
                post_meta["committed_physical_frontier_bytes"]
            ),
            "file_size_bytes": int(post_meta["file_size_bytes"]),
        }


def run_capacity_sweep(
    capacities: tuple[int, ...] = CAPACITIES,
) -> dict[str, Any]:
    rows = [run_capacity_case(capacity) for capacity in capacities]
    return {
        "capacities": list(capacities),
        "rows": rows,
        "per_fresh_segment_max_pages": PER_FRESH_SEGMENT_MAX_PAGES,
        "per_fresh_segment_max_bytes": PER_FRESH_SEGMENT_MAX_PAGES * PAGE_SIZE,
        "derived_transaction_segment_cap": DERIVED_TRANSACTION_SEGMENT_CAP,
        "derived_transaction_tail_cap_bytes": (
            DERIVED_TRANSACTION_SEGMENT_CAP * PER_FRESH_SEGMENT_MAX_PAGES * PAGE_SIZE
        ),
        "single_generation_lookup_pread_bound": SINGLE_GENERATION_LOOKUP_PREAD_BOUND,
        "two_generation_lookup_pread_bound": TWO_GENERATION_LOOKUP_PREAD_BOUND,
        "max_observed_physical_bytes_appended": max(
            int(row["physical_bytes_appended"]) for row in rows
        ),
        "max_observed_lookup_total_preads": max(
            int(row["lookup_total_preads"]) for row in rows
        ),
        "max_observed_missing_total_preads": max(
            int(row["missing_total_preads"]) for row in rows
        ),
    }


def run_crash_case(old_capacity: int, failpoint: str) -> IntegratedCrashCase:
    if failpoint not in FAILPOINTS:
        raise ValueError(failpoint)
    with tempfile.TemporaryDirectory(prefix="dic-v030-crash-") as tmp:
        root = Path(tmp)
        crash_path = root / "crash.pages"
        control_path = root / "control.pages"
        crash_store, seed, target = prepare_capacity_fixture(crash_path, old_capacity)
        control_store, control_seed, control_target = prepare_capacity_fixture(
            control_path, old_capacity
        )
        if (seed, target) != (control_seed, control_target):
            raise AssertionError("v0.30 control and crash fixtures diverged")

        keys = [seed, target]
        pre_state = crash_store.committed_state(keys)
        control_trace = control_store.insert(target)
        post_state = control_store.committed_state(keys)

        crashed = _worker(
            crash_path,
            "crash",
            "--key",
            target,
            "--failpoint",
            failpoint,
            check=False,
        )
        if crashed.returncode != -signal.SIGKILL:
            raise AssertionError(
                f"v0.30 C={old_capacity}/{failpoint} did not SIGKILL: "
                f"returncode={crashed.returncode}, stderr={crashed.stderr!r}"
            )

        expected_committed = failpoint == "committed"
        expected_state = post_state if expected_committed else pre_state
        reopened = SegmentedFixedPagePrimaryStore(crash_path)
        observed_state = reopened.committed_state(keys)
        if observed_state != expected_state:
            raise AssertionError(
                f"v0.30 committed state mismatch C={old_capacity}/{failpoint}: "
                f"expected={expected_state}, observed={observed_state}"
            )
        before_meta = reopened.meta_snapshot()
        tail_before = int(before_meta["uncommitted_tail_bytes"])
        if expected_committed:
            if tail_before != 0:
                raise AssertionError("committed v0.30 crash left an uncommitted tail")
        elif failpoint in {"pages_written", "data_synced"}:
            if tail_before != control_trace.physical_bytes_appended:
                raise AssertionError(
                    "v0.30 pre-commit tail diverged from deterministic control append"
                )
        elif failpoint == "allocated":
            if not (0 < tail_before <= PER_FRESH_SEGMENT_MAX_PAGES * PAGE_SIZE):
                raise AssertionError("v0.30 allocation failpoint exceeded one fresh-segment tail")

        target_lookup = reopened.lookup(target)
        seed_lookup = reopened.lookup(seed)
        if target_lookup.found != expected_committed or not seed_lookup.found:
            raise AssertionError("v0.30 crash visibility contract drifted")

        recovery_one = _json_stdout(_worker(crash_path, "recover"))
        state_after_recovery_one = SegmentedFixedPagePrimaryStore(crash_path).committed_state(keys)
        recovery_two = _json_stdout(_worker(crash_path, "recover"))
        recovered = SegmentedFixedPagePrimaryStore(crash_path)
        state_after_recovery_two = recovered.committed_state(keys)
        after_meta = recovered.meta_snapshot()
        tail_after = int(after_meta["uncommitted_tail_bytes"])
        if tail_after != 0:
            raise AssertionError("v0.30 recovery did not converge file length to committed frontier")
        if state_after_recovery_one != expected_state or state_after_recovery_two != expected_state:
            raise AssertionError("v0.30 recovery changed committed logical state")
        for recovery in (recovery_one, recovery_two):
            if int(recovery["logical_work"]) != 0:
                raise AssertionError("v0.30 recovery required logical redo")
            if int(recovery["generation_pages_scanned"]) != 0:
                raise AssertionError("v0.30 recovery scanned generation pages")
            if int(recovery["mapping_nodes_scanned"]) != 0:
                raise AssertionError("v0.30 recovery scanned mapping nodes")
            if int(recovery["frontier_superblock_preads"]) != 2:
                raise AssertionError("v0.30 recovery frontier discovery drifted")
        if int(recovery_two["physical_truncated_bytes"]) != 0:
            raise AssertionError("v0.30 second recovery pass was not physically idempotent")

        return IntegratedCrashCase(
            old_capacity=old_capacity,
            failpoint=failpoint,
            expected_committed=expected_committed,
            target_key=target,
            target_visible_after_crash=target_lookup.found,
            existing_seed_visible_after_crash=seed_lookup.found,
            exact_committed_state_match=True,
            pre_epoch=int(pre_state["epoch"]),
            post_epoch=int(post_state["epoch"]),
            crash_epoch=int(observed_state["epoch"]),
            uncommitted_tail_before_recovery_bytes=tail_before,
            uncommitted_tail_after_recovery_bytes=tail_after,
            control_physical_bytes_appended=control_trace.physical_bytes_appended,
            control_new_segments_allocated=control_trace.new_segments_allocated,
            recovery_one=recovery_one,
            recovery_two=recovery_two,
            recovery_state_unchanged=True,
        )


def run_crash_matrix(
    capacities: tuple[int, ...] = CAPACITIES,
    failpoints: tuple[str, ...] = FAILPOINTS,
) -> dict[str, Any]:
    rows = [
        run_crash_case(capacity, failpoint).to_dict()
        for capacity in capacities
        for failpoint in failpoints
    ]
    return {
        "capacities": list(capacities),
        "failpoints": list(failpoints),
        "rows": rows,
        "case_count": len(rows),
        "all_exact_committed_state_match": all(
            bool(row["exact_committed_state_match"]) for row in rows
        ),
        "all_existing_seed_visible": all(
            bool(row["existing_seed_visible_after_crash"]) for row in rows
        ),
        "all_recovery_state_unchanged": all(
            bool(row["recovery_state_unchanged"]) for row in rows
        ),
        "max_uncommitted_tail_before_recovery_bytes": max(
            int(row["uncommitted_tail_before_recovery_bytes"]) for row in rows
        ),
        "max_uncommitted_tail_after_recovery_bytes": max(
            int(row["uncommitted_tail_after_recovery_bytes"]) for row in rows
        ),
    }
