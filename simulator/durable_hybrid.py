from __future__ import annotations

import json
import signal
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from storage.durable_hybrid import DurableHybridStore


ROOT = Path(__file__).resolve().parent.parent
WORKER = ROOT / "durable_hybrid_worker.py"

SCENARIOS = (
    "ordinary_insert",
    "migration_start",
    "migration_progress",
    "overflow_admission",
    "migration_to_overflow",
)

FAILPOINTS = (
    "route_uncommitted",
    "final_uncommitted",
    "committed",
)


@dataclass(frozen=True)
class DurableHybridCrashCase:
    scenario: str
    failpoint: str
    expected_committed: bool
    target_key: str
    target_visible_after_crash: bool
    target_path_after_crash: str
    expected_target_path_if_committed: str
    exact_snapshot_match: bool
    audit_valid: bool
    existing_keys_found: bool
    recovery_one: dict[str, Any]
    recovery_two: dict[str, Any]
    recovery_idempotent: bool
    journal_mode: str
    synchronous: int
    primary_bucket_lookup_uses_index: bool
    overflow_lookup_uses_index: bool
    control_insert_trace: dict[str, Any]
    pre_live_size: int
    post_live_size: int
    overflow_rows_after_crash: int
    migration_active_after_crash: bool
    migration_cursor_after_crash: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _worker(db: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(WORKER), "--db", str(db), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=check,
    )


def _json_stdout(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise AssertionError(f"worker produced no JSON output; stderr={result.stderr!r}")
    return json.loads(lines[-1])


def _seed_normal(store: DurableHybridStore, count: int) -> list[str]:
    keys = [f"entity_{index:06d}|deadline" for index in range(count)]
    for key in keys:
        store.insert(key)
    return keys


def _seed_collision(store: DurableHybridStore, count: int, prefix: str = "collision_seed") -> list[str]:
    keys = [f"{prefix}_{index:06d}" for index in range(count)]
    for key in keys:
        store.insert(key)
    return keys


def prepare_scenario(path: Path, scenario: str) -> tuple[DurableHybridStore, list[str], str]:
    if scenario not in SCENARIOS:
        raise ValueError(scenario)
    store = DurableHybridStore(path)

    if scenario == "ordinary_insert":
        store.initialize(initial_capacity=128, max_load=0.50)
        keys = _seed_normal(store, 20)
        target = "entity_000020|deadline"

    elif scenario == "migration_start":
        store.initialize(initial_capacity=32, max_load=0.50)
        keys = _seed_normal(store, 16)
        meta = store.meta_snapshot()
        if meta["old_generation"] is not None or int(meta["current_rows"]) != 16:
            raise AssertionError(f"migration-start fixture drifted: {meta}")
        target = "entity_000016|deadline"

    elif scenario == "migration_progress":
        store.initialize(initial_capacity=32, max_load=0.50)
        keys = _seed_normal(store, 16)
        first = "entity_000016|deadline"
        second = "entity_000017|deadline"
        store.insert(first)
        store.insert(second)
        keys.extend([first, second])
        meta = store.meta_snapshot()
        if meta["old_generation"] is None or int(meta["migration_cursor"]) != 16:
            raise AssertionError(f"migration-progress fixture drifted: {meta}")
        target = "entity_000018|deadline"

    elif scenario == "overflow_admission":
        store.initialize(
            initial_capacity=1024,
            max_load=0.90,
            force_same_pair=True,
        )
        keys = _seed_collision(store, 16)
        if int(store.meta_snapshot()["overflow_rows"]) != 0:
            raise AssertionError("collision primary overflowed before its 16-key envelope")
        target = "collision_seed_000016"

    else:  # migration_to_overflow
        store.initialize(
            initial_capacity=32,
            max_load=0.50,
            migration_slot_budget=8,
            force_same_pair=True,
        )
        keys = _seed_collision(store, 16, prefix="collision_migration")
        progress = [
            "collision_migration_000016",
            "collision_migration_000017",
            "collision_migration_000018",
            "collision_migration_000019",
        ]
        for key in progress:
            store.insert(key)
        keys.extend(progress)
        meta = store.meta_snapshot()
        if meta["old_generation"] is None or int(meta["migration_cursor"]) != 32:
            raise AssertionError(f"migration-to-overflow fixture drifted: {meta}")
        target = "collision_migration_000020"

    audit = store.audit()
    if not audit["valid"]:
        raise AssertionError(f"scenario bootstrap failed audit: {audit}")
    return store, keys, target


def run_crash_case(scenario: str, failpoint: str) -> DurableHybridCrashCase:
    if scenario not in SCENARIOS:
        raise ValueError(scenario)
    if failpoint not in FAILPOINTS:
        raise ValueError(failpoint)

    with tempfile.TemporaryDirectory(prefix="dic-v023-crash-") as tmp:
        root = Path(tmp)
        crash_db = root / "crash.sqlite3"
        control_db = root / "control.sqlite3"

        crash_store, pre_keys, target = prepare_scenario(crash_db, scenario)
        control_store, control_pre_keys, control_target = prepare_scenario(control_db, scenario)
        if pre_keys != control_pre_keys or target != control_target:
            raise AssertionError("control fixture diverged from crash fixture")

        pre_snapshot = crash_store.logical_snapshot()
        control_trace = control_store.insert(target)
        post_snapshot = control_store.logical_snapshot()
        expected_target_path = control_store.lookup(target).path

        if scenario == "overflow_admission" and control_trace.path != "overflow":
            raise AssertionError(f"overflow scenario did not overflow: {control_trace}")
        if scenario == "migration_start" and not control_trace.migration_started:
            raise AssertionError("migration-start scenario did not start migration")
        if scenario == "migration_to_overflow":
            if control_trace.migration_rows_to_overflow <= 0:
                raise AssertionError(
                    f"migration-to-overflow fixture did not move rows to overflow: {control_trace}"
                )
            if not control_trace.migration_completed:
                raise AssertionError("migration-to-overflow fixture did not finish source scan")

        crashed = _worker(
            crash_db,
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

        # The first read in this fresh parent process forces SQLite to recover any WAL
        # state before the application-level recover() method is invoked.
        post_crash_store = DurableHybridStore(crash_db)
        target_lookup = post_crash_store.lookup(target)
        after_snapshot = post_crash_store.logical_snapshot()
        exact_snapshot_match = after_snapshot == expected_snapshot
        if not exact_snapshot_match:
            raise AssertionError(
                f"atomic snapshot mismatch for {scenario}/{failpoint}"
            )

        target_visible = target_lookup.found
        if target_visible != expected_committed:
            raise AssertionError(
                f"target visibility mismatch for {scenario}/{failpoint}: {target_lookup}"
            )
        if expected_committed and target_lookup.path != expected_target_path:
            raise AssertionError(
                f"committed target path mismatch: {target_lookup.path} != {expected_target_path}"
            )

        existing_keys_found = all(post_crash_store.lookup(key).found for key in pre_keys)
        if not existing_keys_found:
            raise AssertionError("SIGKILL lost a pre-existing admitted key")

        recovery_one = _json_stdout(_worker(crash_db, "recover"))
        recovery_two = _json_stdout(_worker(crash_db, "recover"))
        recovery_idempotent = recovery_one == recovery_two
        if not recovery_idempotent:
            raise AssertionError("application-level recover() was not idempotent")
        if int(recovery_one["logical_work"]) != 0:
            raise AssertionError("atomic durable hybrid unexpectedly required application redo")

        inspection = _json_stdout(_worker(crash_db, "inspect"))
        audit = inspection["snapshot"]["audit"]
        if not audit["valid"]:
            raise AssertionError(f"post-recovery audit failed: {audit}")
        settings = inspection["settings"]
        if settings["journal_mode"] != "wal" or int(settings["synchronous"]) != 2:
            raise AssertionError(f"durability settings drifted: {settings}")
        if not inspection["primary_bucket_lookup_uses_index"]:
            raise AssertionError("primary bucket lookup lost its composite primary-key search")
        if not inspection["overflow_lookup_uses_index"]:
            raise AssertionError("overflow point lookup lost its primary-key search")

        meta = inspection["snapshot"]["meta"]
        return DurableHybridCrashCase(
            scenario=scenario,
            failpoint=failpoint,
            expected_committed=expected_committed,
            target_key=target,
            target_visible_after_crash=target_visible,
            target_path_after_crash=target_lookup.path,
            expected_target_path_if_committed=expected_target_path,
            exact_snapshot_match=exact_snapshot_match,
            audit_valid=bool(audit["valid"]),
            existing_keys_found=existing_keys_found,
            recovery_one=recovery_one,
            recovery_two=recovery_two,
            recovery_idempotent=recovery_idempotent,
            journal_mode=str(settings["journal_mode"]),
            synchronous=int(settings["synchronous"]),
            primary_bucket_lookup_uses_index=bool(
                inspection["primary_bucket_lookup_uses_index"]
            ),
            overflow_lookup_uses_index=bool(inspection["overflow_lookup_uses_index"]),
            control_insert_trace=control_trace.to_dict(),
            pre_live_size=int(pre_snapshot["meta"]["live_size"]),
            post_live_size=int(post_snapshot["meta"]["live_size"]),
            overflow_rows_after_crash=int(meta["overflow_rows"]),
            migration_active_after_crash=meta["old_generation"] is not None,
            migration_cursor_after_crash=int(meta["migration_cursor"]),
        )


def _sample_keys(total: int, count: int = 128) -> list[str]:
    count = min(total, count)
    if count <= 1:
        positions = [max(0, total - 1)]
    else:
        positions = [round(i * (total - 1) / (count - 1)) for i in range(count)]
    return [f"entity_{position:06d}|deadline" for position in positions]


def run_persistent_common_envelope(
    checkpoints: tuple[int, ...] = (256, 1_024, 4_096),
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v023-common-") as tmp:
        store = DurableHybridStore(Path(tmp) / "common.sqlite3")
        store.initialize(
            initial_capacity=128,
            max_load=0.50,
            migration_slot_budget=8,
        )
        previous = 0
        rows: list[dict[str, Any]] = []
        global_max_work = 0
        global_max_scan = 0
        global_max_moved = 0
        migration_starts = 0
        migration_completions = 0
        bound = 8 + 9 * 200

        for checkpoint in checkpoints:
            interval_max_work = 0
            interval_max_scan = 0
            interval_max_moved = 0
            interval_overflow_checks = 0
            for position in range(previous, checkpoint):
                trace = store.insert(f"entity_{position:06d}|deadline")
                interval_max_work = max(interval_max_work, trace.primary_mutation_work)
                interval_max_scan = max(
                    interval_max_scan, trace.migration_source_slots_scanned
                )
                interval_max_moved = max(interval_max_moved, trace.migration_rows_moved)
                interval_overflow_checks += int(trace.duplicate_check_overflow)
                migration_starts += int(trace.migration_started)
                migration_completions += int(trace.migration_completed)
                if trace.primary_mutation_work > bound:
                    raise AssertionError(
                        f"primary mutation exceeded fixed v0.23 model bound: {trace}"
                    )
                if trace.migration_source_slots_scanned > 8:
                    raise AssertionError("migration source scan exceeded configured budget")
                if trace.migration_rows_moved > 8:
                    raise AssertionError("migration moved more rows than scanned source slots")

            meta = store.meta_snapshot()
            if int(meta["overflow_rows"]) != 0:
                raise AssertionError(
                    f"ordinary persistent workload unexpectedly overflowed: {meta}"
                )
            if interval_overflow_checks != 0:
                raise AssertionError(
                    "ordinary insertion path queried empty overflow despite metadata discriminator"
                )
            samples = [store.lookup(key) for key in _sample_keys(checkpoint)]
            if not all(item.found for item in samples):
                raise AssertionError("ordinary persistent lookup lost membership")
            if any(item.overflow_checked for item in samples):
                raise AssertionError("ordinary successful lookup touched overflow")
            if any(item.metadata_rows_read != 1 for item in samples):
                raise AssertionError("ordinary lookup metadata work was not one singleton row")

            audit = store.audit()
            if not audit["valid"]:
                raise AssertionError(f"ordinary persistent audit failed: {audit}")
            global_max_work = max(global_max_work, interval_max_work)
            global_max_scan = max(global_max_scan, interval_max_scan)
            global_max_moved = max(global_max_moved, interval_max_moved)
            rows.append(
                {
                    "membership_rows": checkpoint,
                    "current_capacity": int(meta["current_capacity"]),
                    "migration_active": meta["old_generation"] is not None,
                    "migration_cursor": int(meta["migration_cursor"]),
                    "overflow_rows": int(meta["overflow_rows"]),
                    "interval_max_primary_mutation_work": interval_max_work,
                    "interval_max_source_slots_scanned": interval_max_scan,
                    "interval_max_rows_moved": interval_max_moved,
                    "lookup_primary_page_max": max(
                        item.primary_page_probes for item in samples
                    ),
                    "lookup_generations_max": max(
                        item.generations_touched for item in samples
                    ),
                    "lookup_overflow_checks": sum(
                        int(item.overflow_checked) for item in samples
                    ),
                    "lookup_metadata_rows": max(item.metadata_rows_read for item in samples),
                    "audit_valid": bool(audit["valid"]),
                }
            )
            previous = checkpoint

        return {
            "checkpoints": list(checkpoints),
            "rows": rows,
            "primary_mutation_work_bound": bound,
            "global_max_primary_mutation_work": global_max_work,
            "global_max_source_slots_scanned": global_max_scan,
            "global_max_rows_moved": global_max_moved,
            "migration_starts": migration_starts,
            "migration_completions": migration_completions,
            "settings": store.transaction_settings(),
            "primary_bucket_lookup_uses_index": store.primary_bucket_lookup_uses_index(),
            "overflow_lookup_uses_index": store.overflow_lookup_uses_index(),
        }


def run_persistent_overflow_envelope(
    checkpoints: tuple[int, ...] = (1, 16, 64, 256, 1_024),
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v023-overflow-") as tmp:
        store = DurableHybridStore(Path(tmp) / "overflow.sqlite3")
        store.initialize(
            initial_capacity=1024,
            max_load=0.90,
            force_same_pair=True,
        )
        _seed_collision(store, 16, prefix="collision_overflow")
        rows: list[dict[str, Any]] = []
        previous = 0
        max_primary_work = 0
        max_primary_sql_writes = 0

        for checkpoint in checkpoints:
            for position in range(previous, checkpoint):
                key = f"collision_overflow_{position + 16:06d}"
                trace = store.insert(key)
                if trace.path != "overflow":
                    raise AssertionError(f"collision overflow key stayed primary: {trace}")
                if trace.new_key_primary_work != 200:
                    raise AssertionError(
                        f"bounded primary exhaustion drifted from 200-op contract: {trace}"
                    )
                if trace.overflow_row_writes != 1:
                    raise AssertionError("overflow admission did not write exactly one overflow row")
                max_primary_work = max(max_primary_work, trace.primary_mutation_work)
                max_primary_sql_writes = max(
                    max_primary_sql_writes, trace.primary_sql_row_writes
                )

            geometry = store.overflow_geometry()
            target = f"collision_overflow_{checkpoint + 15:06d}"
            hit = store.lookup(target)
            missing = store.lookup(f"collision_missing_{checkpoint:06d}")
            if not hit.found or hit.path != "overflow" or not hit.overflow_checked:
                raise AssertionError("overflow key was not directly resolved after restart-safe admission")
            if missing.found or not missing.overflow_checked:
                raise AssertionError("missing key did not exercise explicit overflow path")
            audit = store.audit()
            if not audit["valid"]:
                raise AssertionError(f"overflow audit failed: {audit}")
            rows.append(
                {
                    "overflow_rows": checkpoint,
                    "overflow_btree_height": int(geometry["height"]),
                    "overflow_btree_total_pages": int(geometry["total_pages"]),
                    "overflow_hit_primary_pages": hit.primary_page_probes,
                    "overflow_hit_modeled_pages": hit.modeled_cold_pages,
                    "missing_primary_pages": missing.primary_page_probes,
                    "missing_modeled_pages": missing.modeled_cold_pages,
                    "audit_valid": bool(audit["valid"]),
                }
            )
            previous = checkpoint

        return {
            "checkpoints": list(checkpoints),
            "rows": rows,
            "max_primary_mutation_work": max_primary_work,
            "max_primary_sql_row_writes": max_primary_sql_writes,
            "overflow_logical_row_write_per_admission": 1,
            "write_measurement_scope": (
                "counts explicit SQL row mutations in the bounded primary algorithm and one "
                "logical overflow-row insertion; SQLite B-tree page rewrites/splits, WAL frames, "
                "filesystem writes, and device write amplification are not directly measured"
            ),
        }
