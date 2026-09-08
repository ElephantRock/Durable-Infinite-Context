from __future__ import annotations

import json
import signal
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from storage.cross_store_hybrid import CrossStoreHybridStore
from storage.fixed_page_primary import PAGE_SIZE

ROOT = Path(__file__).resolve().parent.parent
WORKER = ROOT / "cross_store_hybrid_worker.py"

SCENARIO_FAILPOINTS = {
    "overflow_admission": ("overflow_uncommitted", "overflow_committed", "committed"),
    "migration_start": ("primary_pages_written", "primary_data_synced", "primary_committed"),
}


@dataclass(frozen=True)
class CrossStoreCrashCase:
    scenario: str
    failpoint: str
    expected_committed: bool
    target_key: str
    target_visible_after_crash: bool
    exact_snapshot_match_before_recovery: bool
    exact_snapshot_match_after_recovery: bool
    existing_keys_found: bool
    audit_valid_before_recovery: bool
    audit_valid_after_recovery: bool
    pre_recovery_future_overflow_rows: int
    pre_recovery_tail_bytes: int
    recovery_one: dict[str, Any]
    recovery_two: dict[str, Any]
    recovery_idempotent: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _worker(
    primary: Path,
    overflow: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(WORKER),
            "--primary",
            str(primary),
            "--overflow",
            str(overflow),
            *args,
        ],
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


def _physical_tail_bytes(store: CrossStoreHybridStore) -> int:
    meta = store.primary.meta_snapshot()
    reachable = (2 + 2 * int(meta["next_page_id"])) * PAGE_SIZE
    return max(0, int(meta["file_size_bytes"]) - reachable)


def prepare_scenario(
    primary: Path, overflow: Path, scenario: str
) -> tuple[CrossStoreHybridStore, list[str], str]:
    store = CrossStoreHybridStore(primary, overflow)
    if scenario == "overflow_admission":
        store.initialize(
            initial_capacity=1024,
            max_load=0.90,
            force_same_pair=True,
            migration_slot_budget=8,
        )
        keys = [f"collision_{index:06d}" for index in range(16)]
        for key in keys:
            trace = store.insert(key, recovery_first=False)
            if trace.path != "primary":
                raise AssertionError("overflow fixture filled primary too early")
        target = "collision_000016"
    elif scenario == "migration_start":
        store.initialize(
            initial_capacity=32,
            max_load=0.50,
            migration_slot_budget=8,
        )
        keys = [_key(index) for index in range(16)]
        for key in keys:
            trace = store.insert(key, recovery_first=False)
            if trace.path != "primary":
                raise AssertionError("migration fixture unexpectedly overflowed")
        meta = store.primary.meta_snapshot()
        if meta["old_generation"] is not None or int(meta["current_rows"]) != 16:
            raise AssertionError(f"migration-start fixture drifted: {meta}")
        target = _key(16)
    else:
        raise ValueError(scenario)
    if not store.audit()["valid"]:
        raise AssertionError("scenario seed failed audit")
    return store, keys, target


def run_crash_case(scenario: str, failpoint: str) -> CrossStoreCrashCase:
    if scenario not in SCENARIO_FAILPOINTS:
        raise ValueError(scenario)
    if failpoint not in SCENARIO_FAILPOINTS[scenario]:
        raise ValueError(failpoint)

    with tempfile.TemporaryDirectory(prefix="dic-v025-crash-") as tmp:
        root = Path(tmp)
        crash_primary = root / "crash.pages"
        crash_overflow = root / "crash.sqlite"
        control_primary = root / "control.pages"
        control_overflow = root / "control.sqlite"

        crash_store, pre_keys, target = prepare_scenario(
            crash_primary, crash_overflow, scenario
        )
        control_store, control_keys, control_target = prepare_scenario(
            control_primary, control_overflow, scenario
        )
        if pre_keys != control_keys or target != control_target:
            raise AssertionError("crash/control fixture drifted")

        pre_snapshot = crash_store.logical_snapshot()
        control_store.insert(target, recovery_first=False)
        post_snapshot = control_store.logical_snapshot()

        crashed = _worker(
            crash_primary,
            crash_overflow,
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

        expected_committed = failpoint in {"committed", "primary_committed"}
        expected_snapshot = post_snapshot if expected_committed else pre_snapshot
        reopened = CrossStoreHybridStore(crash_primary, crash_overflow)
        before_recovery = reopened.logical_snapshot()
        audit_before = reopened.audit()
        target_lookup = reopened.lookup(target)
        existing_found = all(reopened.lookup(key).found for key in pre_keys)
        pre_tail = _physical_tail_bytes(reopened)

        if before_recovery != expected_snapshot:
            raise AssertionError(f"cross-store snapshot mismatch: {scenario}/{failpoint}")
        if bool(target_lookup.found) != expected_committed:
            raise AssertionError(f"target visibility mismatch: {scenario}/{failpoint}")
        if not existing_found or not audit_before["valid"]:
            raise AssertionError(f"crash image lost/duplicated membership: {audit_before}")

        recovery_one = _json_stdout(_worker(crash_primary, crash_overflow, "recover"))
        recovery_two = _json_stdout(_worker(crash_primary, crash_overflow, "recover"))
        after_recovery = reopened.logical_snapshot()
        audit_after = reopened.audit()
        if after_recovery != expected_snapshot:
            raise AssertionError("reclamation changed committed logical state")
        if not audit_after["valid"]:
            raise AssertionError(f"post-recovery audit failed: {audit_after}")
        if int(recovery_one["logical_redo"]) != 0 or int(recovery_two["logical_redo"]) != 0:
            raise AssertionError("cross-store recovery required logical redo")
        if not recovery_one["cleanup_epoch_index_used"] or not recovery_two["cleanup_epoch_index_used"]:
            raise AssertionError("recovery lost indexed future-row discovery")
        if int(recovery_two["deleted_future_overflow_rows"]) != 0:
            raise AssertionError("second recovery still had future overflow cleanup")
        if int(recovery_two["reclaimed_tail_bytes"]) != 0:
            raise AssertionError("second recovery still had fixed-tail cleanup")
        if int(recovery_two["cleanup_sqlite_commits"]) != 0:
            raise AssertionError("clean second recovery committed an unnecessary SQLite transaction")

        return CrossStoreCrashCase(
            scenario=scenario,
            failpoint=failpoint,
            expected_committed=expected_committed,
            target_key=target,
            target_visible_after_crash=bool(target_lookup.found),
            exact_snapshot_match_before_recovery=True,
            exact_snapshot_match_after_recovery=True,
            existing_keys_found=existing_found,
            audit_valid_before_recovery=bool(audit_before["valid"]),
            audit_valid_after_recovery=bool(audit_after["valid"]),
            pre_recovery_future_overflow_rows=int(audit_before["future_overflow_rows"]),
            pre_recovery_tail_bytes=pre_tail,
            recovery_one=recovery_one,
            recovery_two=recovery_two,
            recovery_idempotent=True,
        )


def run_future_row_resurrection_control() -> dict[str, Any]:
    """Prove startup cleanup is required before an epoch-advancing next mutation."""
    with tempfile.TemporaryDirectory(prefix="dic-v025-resurrection-") as tmp:
        root = Path(tmp)
        primary = root / "primary.pages"
        overflow = root / "overflow.sqlite"
        _store, _keys, abandoned = prepare_scenario(primary, overflow, "overflow_admission")
        crashed = _worker(
            primary,
            overflow,
            "crash",
            "--key",
            abandoned,
            "--failpoint",
            "overflow_committed",
            check=False,
        )
        if crashed.returncode != -signal.SIGKILL:
            raise AssertionError("future-row resurrection control did not SIGKILL")

        reopened = CrossStoreHybridStore(primary, overflow)
        pre = reopened.audit()
        if pre["future_overflow_rows"] != 1 or reopened.lookup(abandoned).found:
            raise AssertionError("abandoned future row was not hidden before recovery")

        next_key = "collision_000017"
        trace = reopened.insert(next_key)
        if trace.path != "overflow":
            raise AssertionError("resurrection control next admission did not use overflow")
        if trace.pre_admission_deleted_future_overflow_rows != 1:
            raise AssertionError("startup mutation did not clean abandoned future row")
        if reopened.lookup(abandoned).found:
            raise AssertionError("abandoned future row resurrected after later epoch advance")
        if not reopened.lookup(next_key).found:
            raise AssertionError("post-recovery replacement overflow admission was lost")
        audit = reopened.audit()
        if not audit["valid"] or audit["future_overflow_rows"] != 0:
            raise AssertionError(f"resurrection-control audit failed: {audit}")
        return {
            "future_rows_before_startup_recovery": int(pre["future_overflow_rows"]),
            "startup_deleted_future_rows": int(trace.pre_admission_deleted_future_overflow_rows),
            "abandoned_key_visible_after_next_epoch": bool(reopened.lookup(abandoned).found),
            "replacement_key_visible": bool(reopened.lookup(next_key).found),
            "audit_valid": bool(audit["valid"]),
        }


def _sample_keys(total: int, count: int = 128) -> list[str]:
    count = min(total, count)
    if count <= 1:
        points = [max(0, total - 1)]
    else:
        points = [round(i * (total - 1) / (count - 1)) for i in range(count)]
    return [_key(point) for point in points]


def run_common_envelope(
    checkpoints: tuple[int, ...] = (256, 1_024, 4_096),
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v025-common-") as tmp:
        root = Path(tmp)
        store = CrossStoreHybridStore(root / "primary.pages", root / "overflow.sqlite")
        store.initialize(initial_capacity=128, max_load=0.50, migration_slot_budget=8)
        previous = 0
        rows: list[dict[str, Any]] = []
        max_scan = 0
        max_moved = 0
        migration_starts = 0
        migration_completions = 0
        for checkpoint in checkpoints:
            interval_scan = 0
            interval_moved = 0
            for index in range(previous, checkpoint):
                trace = store.insert(_key(index), recovery_first=False)
                if trace.path != "primary" or trace.primary_trace is None:
                    raise AssertionError("ordinary cross-store workload entered overflow")
                p = trace.primary_trace
                interval_scan = max(interval_scan, int(p["migration_source_slots_scanned"]))
                interval_moved = max(interval_moved, int(p["migration_rows_moved"]))
                migration_starts += int(bool(p["migration_started"]))
                migration_completions += int(bool(p["migration_completed"]))
            samples = [store.lookup(key) for key in _sample_keys(checkpoint)]
            if not all(row.found and row.path == "primary" for row in samples):
                raise AssertionError("ordinary lookup left fixed-page primary")
            if any(row.overflow_checked for row in samples):
                raise AssertionError("ordinary primary hit queried SQLite overflow")
            if max(row.primary_total_preads for row in samples) > 8:
                raise AssertionError("ordinary primary hit exceeded v0.24 pread envelope")
            audit = store.audit()
            if not audit["valid"] or audit["visible_overflow_rows"] != 0:
                raise AssertionError(f"ordinary hybrid audit failed: {audit}")
            max_scan = max(max_scan, interval_scan)
            max_moved = max(max_moved, interval_moved)
            rows.append(
                {
                    "membership_rows": checkpoint,
                    "interval_max_source_slots_scanned": interval_scan,
                    "interval_max_rows_moved": interval_moved,
                    "successful_lookup_pread_max": max(
                        row.primary_total_preads for row in samples
                    ),
                    "successful_lookup_overflow_checks": sum(
                        int(row.overflow_checked) for row in samples
                    ),
                    "visible_overflow_rows": int(audit["visible_overflow_rows"]),
                    "cleanup_epoch_index_used": bool(audit["cleanup_epoch_index_used"]),
                    "audit_valid": bool(audit["valid"]),
                }
            )
            previous = checkpoint
        return {
            "rows": rows,
            "global_max_source_slots_scanned": max_scan,
            "global_max_rows_moved": max_moved,
            "migration_starts": migration_starts,
            "migration_completions": migration_completions,
        }


def run_overflow_envelope(
    checkpoints: tuple[int, ...] = (1, 16, 64, 256, 1_024),
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v025-overflow-") as tmp:
        root = Path(tmp)
        primary_path = root / "primary.pages"
        overflow_path = root / "overflow.sqlite"
        store = CrossStoreHybridStore(primary_path, overflow_path)
        store.initialize(
            initial_capacity=1024,
            max_load=0.90,
            force_same_pair=True,
            migration_slot_budget=8,
        )
        primary_keys = [f"collision_primary_{index:06d}" for index in range(16)]
        for key in primary_keys:
            trace = store.insert(key, recovery_first=False)
            if trace.path != "primary":
                raise AssertionError("overflow stress filled primary too early")

        rows: list[dict[str, Any]] = []
        previous = 0
        for checkpoint in checkpoints:
            last_trace = None
            for index in range(previous, checkpoint):
                last_trace = store.insert(
                    f"collision_overflow_{index:09d}", recovery_first=False
                )
                if last_trace.path != "overflow":
                    raise AssertionError("post-capacity key did not enter exact overflow")
                if last_trace.sqlite_commits != 1 or last_trace.coordinator_explicit_fsyncs != 1:
                    raise AssertionError("overflow coordinator write protocol drifted")
            assert last_trace is not None
            stats = store.overflow_stats()
            primary_hit = store.lookup(primary_keys[0])
            overflow_key = f"collision_overflow_{checkpoint - 1:09d}"
            overflow_hit = store.lookup(overflow_key)
            missing = store.lookup(f"collision_missing_{checkpoint:09d}")
            audit = store.audit()
            if primary_hit.overflow_checked:
                raise AssertionError("primary hit queried overflow")
            if not overflow_hit.found or overflow_hit.path != "overflow":
                raise AssertionError("overflow hit was not visible")
            if missing.found or missing.path != "miss":
                raise AssertionError("missing lookup did not remain absent")
            if not overflow_hit.overflow_uses_primary_key or not missing.overflow_uses_primary_key:
                raise AssertionError("overflow exact lookup lost primary-key access")
            if not audit["valid"] or audit["visible_overflow_rows"] != checkpoint:
                raise AssertionError(f"overflow audit failed: {audit}")
            rows.append(
                {
                    "overflow_rows": checkpoint,
                    "overflow_btree_height": int(stats["height"]),
                    "overflow_btree_total_pages": int(stats["total_pages"]),
                    "primary_hit_preads": int(primary_hit.primary_total_preads),
                    "primary_hit_overflow_checked": bool(primary_hit.overflow_checked),
                    "overflow_hit_primary_preads": int(overflow_hit.primary_total_preads),
                    "overflow_hit_coordinator_extra_preads": int(overflow_hit.coordinator_extra_preads),
                    "overflow_hit_btree_height": int(overflow_hit.overflow_btree_height),
                    "missing_primary_preads": int(missing.primary_total_preads),
                    "missing_coordinator_extra_preads": int(missing.coordinator_extra_preads),
                    "missing_btree_height": int(missing.overflow_btree_height),
                    "coordinator_superblock_pwrites_per_admission": int(last_trace.coordinator_superblock_pwrites),
                    "coordinator_explicit_fsyncs_per_admission": int(last_trace.coordinator_explicit_fsyncs),
                    "sqlite_commits_per_admission": int(last_trace.sqlite_commits),
                    "overflow_db_bytes": overflow_path.stat().st_size,
                    "cleanup_epoch_index_used": bool(audit["cleanup_epoch_index_used"]),
                    "audit_valid": bool(audit["valid"]),
                }
            )
            previous = checkpoint
        return {"rows": rows}


def run_reclamation_scaling(
    capacities: tuple[int, ...] = (32, 128, 512, 2_048),
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for capacity in capacities:
        with tempfile.TemporaryDirectory(prefix="dic-v025-reclaim-") as tmp:
            root = Path(tmp)
            primary = root / "primary.pages"
            overflow = root / "overflow.sqlite"
            store = CrossStoreHybridStore(primary, overflow)
            store.initialize(
                initial_capacity=capacity,
                max_load=0.50,
                migration_slot_budget=8,
            )
            seed_count = capacity // 2
            for index in range(seed_count):
                trace = store.insert(_key(index), recovery_first=False)
                if trace.path != "primary":
                    raise AssertionError("reclamation fixture unexpectedly overflowed")
            target = _key(seed_count)
            crashed = _worker(
                primary,
                overflow,
                "crash",
                "--key",
                target,
                "--failpoint",
                "primary_data_synced",
                check=False,
            )
            if crashed.returncode != -signal.SIGKILL:
                raise AssertionError("reclamation scaling fixture did not SIGKILL")
            reopened = CrossStoreHybridStore(primary, overflow)
            tail_before = _physical_tail_bytes(reopened)
            logical_before = reopened.logical_snapshot()
            recovery = reopened.recover().to_dict()
            logical_after = reopened.logical_snapshot()
            audit_after = reopened.audit()
            tail_after = _physical_tail_bytes(reopened)
            if tail_before <= 0 or tail_after != 0:
                raise AssertionError("stale primary tail was not deterministically reclaimed")
            if logical_before != logical_after:
                raise AssertionError("tail reclamation changed committed logical state")
            if int(recovery["reclaimed_tail_bytes"]) != tail_before:
                raise AssertionError("reclaimed-byte accounting drifted")
            if int(recovery["truncate_calls"]) != 1 or int(recovery["fixed_file_fsyncs"]) != 1:
                raise AssertionError("fixed-tail reclaim syscall envelope drifted")
            if not recovery["cleanup_epoch_index_used"] or not audit_after["valid"]:
                raise AssertionError("reclamation lost indexed cleanup or audit validity")
            rows.append(
                {
                    "initial_capacity": capacity,
                    "seed_rows": seed_count,
                    "stale_tail_bytes_before_recovery": tail_before,
                    "reclaimed_tail_bytes": int(recovery["reclaimed_tail_bytes"]),
                    "truncate_calls": int(recovery["truncate_calls"]),
                    "fixed_file_fsyncs": int(recovery["fixed_file_fsyncs"]),
                    "cleanup_sqlite_commits": int(recovery["cleanup_sqlite_commits"]),
                    "cleanup_epoch_index_used": bool(recovery["cleanup_epoch_index_used"]),
                    "tail_bytes_after_recovery": tail_after,
                    "logical_redo": int(recovery["logical_redo"]),
                    "logical_snapshot_unchanged": logical_before == logical_after,
                    "audit_valid": bool(audit_after["valid"]),
                }
            )
    return {"rows": rows}
