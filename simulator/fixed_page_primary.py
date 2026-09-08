from __future__ import annotations

import json
import signal
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from storage.fixed_page_primary import (
    PAGE_SIZE,
    FixedPagePrimaryStore,
    PrimaryAdmissionExhausted,
)

ROOT = Path(__file__).resolve().parent.parent
WORKER = ROOT / "fixed_page_primary_worker.py"

SCENARIOS = ("ordinary_insert", "migration_start", "migration_progress")
FAILPOINTS = ("pages_written", "data_synced", "committed")


@dataclass(frozen=True)
class FixedPageCrashCase:
    scenario: str
    failpoint: str
    expected_committed: bool
    target_key: str
    target_visible_after_crash: bool
    target_path_after_crash: str
    exact_snapshot_match: bool
    existing_keys_found: bool
    audit_valid: bool
    recovery_one: dict[str, Any]
    recovery_two: dict[str, Any]
    recovery_idempotent: bool
    pre_epoch: int
    post_epoch: int
    crash_epoch: int
    physical_tail_bytes: int
    control_insert_trace: dict[str, Any]

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


def _key(index: int) -> str:
    return f"entity_{index:06d}|deadline"


def _seed(store: FixedPagePrimaryStore, count: int) -> list[str]:
    keys = [_key(index) for index in range(count)]
    for key in keys:
        store.insert(key)
    return keys


def prepare_scenario(path: Path, scenario: str) -> tuple[FixedPagePrimaryStore, list[str], str]:
    store = FixedPagePrimaryStore(path)
    if scenario == "ordinary_insert":
        store.initialize(initial_capacity=128, max_load=0.50)
        keys = _seed(store, 20)
        target = _key(20)
    elif scenario == "migration_start":
        store.initialize(initial_capacity=32, max_load=0.50)
        keys = _seed(store, 16)
        meta = store.meta_snapshot()
        if meta["old_generation"] is not None or int(meta["current_rows"]) != 16:
            raise AssertionError(f"migration-start fixture drifted: {meta}")
        target = _key(16)
    elif scenario == "migration_progress":
        store.initialize(initial_capacity=32, max_load=0.50)
        keys = _seed(store, 16)
        for index in (16, 17):
            store.insert(_key(index))
            keys.append(_key(index))
        meta = store.meta_snapshot()
        if meta["old_generation"] is None or int(meta["migration_cursor"]) != 16:
            raise AssertionError(f"migration-progress fixture drifted: {meta}")
        target = _key(18)
    else:
        raise ValueError(scenario)
    if not store.audit()["valid"]:
        raise AssertionError("scenario seed failed audit")
    return store, keys, target


def run_crash_case(scenario: str, failpoint: str) -> FixedPageCrashCase:
    if scenario not in SCENARIOS:
        raise ValueError(scenario)
    if failpoint not in FAILPOINTS:
        raise ValueError(failpoint)

    with tempfile.TemporaryDirectory(prefix="dic-v024-crash-") as tmp:
        root = Path(tmp)
        crash_path = root / "crash.pages"
        control_path = root / "control.pages"
        crash_store, pre_keys, target = prepare_scenario(crash_path, scenario)
        control_store, control_keys, control_target = prepare_scenario(control_path, scenario)
        if pre_keys != control_keys or target != control_target:
            raise AssertionError("control and crash fixtures diverged")

        pre_snapshot = crash_store.logical_snapshot()
        control_trace = control_store.insert(target)
        post_snapshot = control_store.logical_snapshot()

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
                f"{scenario}/{failpoint} did not terminate by SIGKILL: "
                f"returncode={crashed.returncode}, stderr={crashed.stderr!r}"
            )

        expected_committed = failpoint == "committed"
        expected_snapshot = post_snapshot if expected_committed else pre_snapshot
        reopened = FixedPagePrimaryStore(crash_path)
        after_snapshot = reopened.logical_snapshot()
        if after_snapshot != expected_snapshot:
            raise AssertionError(f"snapshot mismatch for {scenario}/{failpoint}")
        lookup = reopened.lookup(target)
        if lookup.found != expected_committed:
            raise AssertionError(f"visibility mismatch for {scenario}/{failpoint}")
        if not all(reopened.lookup(key).found for key in pre_keys):
            raise AssertionError("SIGKILL lost a pre-existing primary key")

        recovery_one = _json_stdout(_worker(crash_path, "recover"))
        recovery_two = _json_stdout(_worker(crash_path, "recover"))
        audit = reopened.audit()
        if int(recovery_one["logical_work"]) != 0 or recovery_one != recovery_two:
            raise AssertionError("fixed-page recovery required application redo or was non-idempotent")
        if not audit["valid"]:
            raise AssertionError(f"fixed-page audit failed: {audit}")

        meta = reopened.meta_snapshot()
        reachable_bytes = (2 + 2 * int(meta["next_page_id"])) * PAGE_SIZE
        tail_bytes = max(0, int(meta["file_size_bytes"]) - reachable_bytes)
        return FixedPageCrashCase(
            scenario=scenario,
            failpoint=failpoint,
            expected_committed=expected_committed,
            target_key=target,
            target_visible_after_crash=lookup.found,
            target_path_after_crash=lookup.path,
            exact_snapshot_match=True,
            existing_keys_found=True,
            audit_valid=True,
            recovery_one=recovery_one,
            recovery_two=recovery_two,
            recovery_idempotent=True,
            pre_epoch=int(pre_snapshot["epoch"]),
            post_epoch=int(post_snapshot["epoch"]),
            crash_epoch=int(after_snapshot["epoch"]),
            physical_tail_bytes=tail_bytes,
            control_insert_trace=control_trace.to_dict(),
        )


def _sample_keys(total: int, count: int = 128) -> list[str]:
    count = min(total, count)
    if count <= 1:
        points = [max(0, total - 1)]
    else:
        points = [round(i * (total - 1) / (count - 1)) for i in range(count)]
    return [_key(point) for point in points]


def _validate_lookup_addressing(trace: Any) -> None:
    if trace.primary_physical_preads != 2 * trace.logical_primary_pages:
        raise AssertionError(f"dual-copy primary pread accounting drifted: {trace}")
    if trace.metadata_physical_preads != 2:
        raise AssertionError(f"superblock read count drifted: {trace}")
    expected_offsets: list[int] = []
    for page_id in trace.logical_page_ids:
        expected_offsets.extend(
            [
                PAGE_SIZE * (2 + 2 * page_id),
                PAGE_SIZE * (3 + 2 * page_id),
            ]
        )
    if list(trace.physical_offsets) != expected_offsets:
        raise AssertionError(f"arithmetic page addressing drifted: {trace}")


def run_common_envelope(
    checkpoints: tuple[int, ...] = (256, 1_024, 4_096, 16_384),
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v024-common-") as tmp:
        store = FixedPagePrimaryStore(Path(tmp) / "primary.pages")
        store.initialize(initial_capacity=128, max_load=0.50, migration_slot_budget=8)
        previous = 0
        rows: list[dict[str, Any]] = []
        global_max_scan = 0
        global_max_moved = 0
        global_max_pages_written = 0
        global_max_placement_work = 0
        migration_starts = 0
        migration_completions = 0

        for checkpoint in checkpoints:
            interval_max_scan = 0
            interval_max_moved = 0
            interval_max_pages_written = 0
            interval_max_placement_work = 0
            for index in range(previous, checkpoint):
                trace = store.insert(_key(index))
                if trace.fsyncs != 2 or trace.metadata_physical_pwrites != 1:
                    raise AssertionError(f"commit protocol drifted: {trace}")
                if trace.migration_source_slots_scanned > 8 or trace.migration_rows_moved > 8:
                    raise AssertionError(f"migration budget violated: {trace}")
                interval_max_scan = max(interval_max_scan, trace.migration_source_slots_scanned)
                interval_max_moved = max(interval_max_moved, trace.migration_rows_moved)
                interval_max_pages_written = max(interval_max_pages_written, trace.logical_pages_written)
                interval_max_placement_work = max(interval_max_placement_work, trace.placement_work)
                migration_starts += int(trace.migration_started)
                migration_completions += int(trace.migration_completed)

            meta = store.meta_snapshot()
            if meta["old_generation"] is not None:
                raise AssertionError(f"checkpoint unexpectedly landed mid-migration: {meta}")
            samples = [store.lookup(key) for key in _sample_keys(checkpoint)]
            if not all(trace.found for trace in samples):
                raise AssertionError("fixed-page primary lost an ordinary key")
            for trace in samples:
                _validate_lookup_addressing(trace)
                if trace.total_physical_preads > 8:
                    raise AssertionError(f"single-generation lookup exceeded fixed envelope: {trace}")
            missing = store.lookup(f"missing_{checkpoint}")
            _validate_lookup_addressing(missing)
            if missing.total_physical_preads != 8:
                raise AssertionError(f"single-generation miss should read 2 super + 3 dual pages: {missing}")
            audit = store.audit()
            if not audit["valid"]:
                raise AssertionError(f"common envelope audit failed: {audit}")

            global_max_scan = max(global_max_scan, interval_max_scan)
            global_max_moved = max(global_max_moved, interval_max_moved)
            global_max_pages_written = max(global_max_pages_written, interval_max_pages_written)
            global_max_placement_work = max(global_max_placement_work, interval_max_placement_work)
            rows.append(
                {
                    "membership_rows": checkpoint,
                    "current_capacity": int(meta["current_capacity"]),
                    "committed_epoch": int(meta["committed_epoch"]),
                    "file_size_bytes": int(meta["file_size_bytes"]),
                    "allocated_bytes": int(meta["allocated_bytes"]),
                    "interval_max_source_slots_scanned": interval_max_scan,
                    "interval_max_rows_moved": interval_max_moved,
                    "interval_max_logical_pages_written": interval_max_pages_written,
                    "interval_max_placement_work": interval_max_placement_work,
                    "lookup_total_pread_max": max(trace.total_physical_preads for trace in samples),
                    "lookup_primary_logical_page_max": max(trace.logical_primary_pages for trace in samples),
                    "missing_total_preads": missing.total_physical_preads,
                    "audit_valid": bool(audit["valid"]),
                }
            )
            previous = checkpoint

        return {
            "rows": rows,
            "global_max_source_slots_scanned": global_max_scan,
            "global_max_rows_moved": global_max_moved,
            "global_max_logical_pages_written": global_max_pages_written,
            "global_max_placement_work": global_max_placement_work,
            "migration_starts": migration_starts,
            "migration_completions": migration_completions,
            "address_formula": store.address_formula(),
        }


def run_active_migration_lookup_envelope() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v024-migration-") as tmp:
        store = FixedPagePrimaryStore(Path(tmp) / "primary.pages")
        store.initialize(initial_capacity=128, max_load=0.50, migration_slot_budget=8)
        _seed(store, 64)
        start_trace = store.insert(_key(64))
        meta = store.meta_snapshot()
        if not start_trace.migration_started or meta["old_generation"] is None:
            raise AssertionError("active-migration fixture did not start migration")
        lookups = [store.lookup(_key(index)) for index in range(65)]
        if not all(trace.found for trace in lookups):
            raise AssertionError("active migration lost a key")
        for trace in lookups:
            _validate_lookup_addressing(trace)
            if trace.total_physical_preads > 14:
                raise AssertionError(f"two-generation lookup exceeded fixed envelope: {trace}")
        missing = store.lookup("migration_missing")
        _validate_lookup_addressing(missing)
        if missing.total_physical_preads != 14:
            raise AssertionError(f"two-generation miss should read 2 super + 6 dual pages: {missing}")
        return {
            "migration_cursor": int(meta["migration_cursor"]),
            "source_slots_scanned_on_start": start_trace.migration_source_slots_scanned,
            "rows_moved_on_start": start_trace.migration_rows_moved,
            "successful_lookup_total_pread_max": max(trace.total_physical_preads for trace in lookups),
            "successful_lookup_generations_max": max(trace.generations_touched for trace in lookups),
            "missing_total_preads": missing.total_physical_preads,
            "audit_valid": bool(store.audit()["valid"]),
        }


def run_collision_rejection() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v024-collision-") as tmp:
        store = FixedPagePrimaryStore(Path(tmp) / "primary.pages")
        store.initialize(initial_capacity=1_024, max_load=0.90, force_same_pair=True)
        for index in range(16):
            store.insert(f"collision_{index:06d}")
        before = store.logical_snapshot()
        rejected = False
        try:
            store.insert("collision_000016")
        except PrimaryAdmissionExhausted:
            rejected = True
        after = store.logical_snapshot()
        if not rejected or before != after or not store.audit()["valid"]:
            raise AssertionError("bounded collision rejection was not state-preserving")
        return {
            "admitted_keys": 16,
            "first_rejected_ordinal": 17,
            "rejected": rejected,
            "snapshot_unchanged": before == after,
            "audit_valid": bool(store.audit()["valid"]),
        }
