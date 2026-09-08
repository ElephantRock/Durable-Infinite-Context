from __future__ import annotations

import hashlib
import json
from pathlib import Path

import run_cross_store_hybrid_experiment


ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "cross_store_hybrid_results.json"
EXPECTED_RESULT_SHA256 = "ed6aced30b26306767ae4212abb6b519818235dd4d450a2894b4b392a30e68cf"
EXPECTED_ARTIFACT_SHA256 = "7cd7379000e44a22c9b860524bae13daab185811531a1b8a58a986c74172a435"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_semantic_guard(out: dict) -> None:
    required = (
        "membership_equal",
        "materialization_equal",
        "head_index_equal",
        "all_derived_fresh",
        "full_assembly_equal",
        "partial_assembly_equal",
    )
    if not all(bool(out["semantic_guard"][name]) for name in required):
        raise AssertionError("v0.25 semantic guard failed")


def _require_crash_matrix(out: dict) -> None:
    rows = out["crash_rows"]
    expected_pairs = {
        ("overflow_admission", "overflow_uncommitted"),
        ("overflow_admission", "overflow_committed"),
        ("overflow_admission", "committed"),
        ("migration_start", "primary_pages_written"),
        ("migration_start", "primary_data_synced"),
        ("migration_start", "primary_committed"),
    }
    observed_pairs = {(row["scenario"], row["failpoint"]) for row in rows}
    if len(rows) != 6 or observed_pairs != expected_pairs:
        raise AssertionError("v0.25 crash-matrix coverage drifted")

    for row in rows:
        committed = row["failpoint"] in {"committed", "primary_committed"}
        if bool(row["expected_committed"]) != committed:
            raise AssertionError("v0.25 commit expectation disagrees with failpoint")
        if bool(row["target_visible_after_crash"]) != committed:
            raise AssertionError("v0.25 crash visibility disagrees with commit boundary")
        if not row["exact_snapshot_match_before_recovery"]:
            raise AssertionError("v0.25 SIGKILL exposed a torn cross-store logical snapshot")
        if not row["exact_snapshot_match_after_recovery"]:
            raise AssertionError("v0.25 cleanup changed committed logical state")
        if not row["existing_keys_found"]:
            raise AssertionError("v0.25 SIGKILL lost pre-existing membership")
        if not row["audit_valid_before_recovery"] or not row["audit_valid_after_recovery"]:
            raise AssertionError("v0.25 crash or cleanup broke membership audit")
        if not row["recovery_idempotent"]:
            raise AssertionError("v0.25 recovery was not idempotent")
        for pass_name in ("recovery_one", "recovery_two"):
            recovery = row[pass_name]
            if int(recovery["logical_redo"]) != 0:
                raise AssertionError("v0.25 cleanup required application logical redo")
            if not recovery["cleanup_epoch_index_used"]:
                raise AssertionError("v0.25 cleanup lost indexed future-row discovery")
        second = row["recovery_two"]
        if any(
            int(second[name]) != 0
            for name in (
                "deleted_future_overflow_rows",
                "reclaimed_tail_bytes",
                "truncate_calls",
                "fixed_file_fsyncs",
                "cleanup_sqlite_commits",
            )
        ):
            raise AssertionError("v0.25 second recovery was not a clean no-op")

    hidden = next(
        row for row in rows
        if row["scenario"] == "overflow_admission" and row["failpoint"] == "overflow_committed"
    )
    if int(hidden["pre_recovery_future_overflow_rows"]) != 1:
        raise AssertionError("v0.25 hidden-future-row control drifted")
    if int(hidden["recovery_one"]["deleted_future_overflow_rows"]) != 1:
        raise AssertionError("v0.25 did not delete hidden durable overflow row")
    if int(hidden["recovery_one"]["cleanup_sqlite_commits"]) != 1:
        raise AssertionError("v0.25 hidden-row cleanup commit count drifted")

    for failpoint in ("primary_pages_written", "primary_data_synced"):
        row = next(
            item for item in rows
            if item["scenario"] == "migration_start" and item["failpoint"] == failpoint
        )
        if int(row["pre_recovery_tail_bytes"]) != 139264:
            raise AssertionError("v0.25 migration-start stale-tail fixture drifted")
        if int(row["recovery_one"]["reclaimed_tail_bytes"]) != 139264:
            raise AssertionError("v0.25 migration-start stale tail was not fully reclaimed")
        if int(row["recovery_one"]["truncate_calls"]) != 1:
            raise AssertionError("v0.25 migration-start cleanup truncate count drifted")


def _require_resurrection_control(out: dict) -> None:
    row = out["future_row_resurrection_control"]
    if int(row["future_rows_before_startup_recovery"]) != 1:
        raise AssertionError("v0.25 resurrection control did not begin with one future row")
    if int(row["startup_deleted_future_rows"]) != 1:
        raise AssertionError("v0.25 startup recovery did not delete abandoned future row")
    if row["abandoned_key_visible_after_next_epoch"]:
        raise AssertionError("v0.25 abandoned future row resurrected")
    if not row["replacement_key_visible"] or not row["audit_valid"]:
        raise AssertionError("v0.25 resurrection control lost replacement admission or audit")


def _require_common_envelope(out: dict) -> None:
    common = out["common_envelope"]
    rows = common["rows"]
    if [int(row["membership_rows"]) for row in rows] != [256, 1024, 4096]:
        raise AssertionError("v0.25 common-envelope checkpoints drifted")
    if int(common["global_max_source_slots_scanned"]) != 8:
        raise AssertionError("v0.25 source migration envelope drifted")
    if int(common["global_max_rows_moved"]) != 8:
        raise AssertionError("v0.25 moved-row envelope drifted")
    if int(common["migration_starts"]) != 6 or int(common["migration_completions"]) != 6:
        raise AssertionError("v0.25 common envelope did not complete all migrations")
    for row in rows:
        if int(row["interval_max_source_slots_scanned"]) > 8:
            raise AssertionError("v0.25 interval migration scan exceeded budget")
        if int(row["interval_max_rows_moved"]) > 8:
            raise AssertionError("v0.25 interval migration moved more than eight rows")
        if int(row["successful_lookup_pread_max"]) != 6:
            raise AssertionError("v0.25 ordinary fixed-file pread envelope drifted")
        if int(row["successful_lookup_overflow_checks"]) != 0:
            raise AssertionError("v0.25 successful primary lookup queried overflow")
        if int(row["visible_overflow_rows"]) != 0:
            raise AssertionError("v0.25 ordinary workload contaminated overflow")
        if not row["cleanup_epoch_index_used"] or not row["audit_valid"]:
            raise AssertionError("v0.25 ordinary envelope lost indexed cleanup or audit")


def _require_overflow_envelope(out: dict) -> None:
    rows = out["overflow_envelope"]["rows"]
    if [int(row["overflow_rows"]) for row in rows] != [1, 16, 64, 256, 1024]:
        raise AssertionError("v0.25 overflow checkpoints drifted")
    if [int(row["overflow_btree_height"]) for row in rows] != [1, 1, 1, 2, 2]:
        raise AssertionError("v0.25 overflow B-tree geometry drifted")
    for row in rows:
        if row["primary_hit_overflow_checked"]:
            raise AssertionError("v0.25 primary hit queried exceptional overflow")
        if int(row["primary_hit_preads"]) != 8:
            raise AssertionError("v0.25 collision-stress primary-hit pread envelope drifted")
        if int(row["overflow_hit_primary_preads"]) != 8:
            raise AssertionError("v0.25 overflow-hit primary miss pread envelope drifted")
        if int(row["overflow_hit_coordinator_extra_preads"]) != 2:
            raise AssertionError("v0.25 overflow coordinator read count drifted")
        if int(row["missing_primary_preads"]) != 8 or int(row["missing_coordinator_extra_preads"]) != 2:
            raise AssertionError("v0.25 exceptional missing lookup envelope drifted")
        if int(row["coordinator_superblock_pwrites_per_admission"]) != 1:
            raise AssertionError("v0.25 coordinator superblock write count drifted")
        if int(row["coordinator_explicit_fsyncs_per_admission"]) != 1:
            raise AssertionError("v0.25 coordinator fixed-file fsync count drifted")
        if int(row["sqlite_commits_per_admission"]) != 1:
            raise AssertionError("v0.25 SQLite logical commit count drifted")
        if not row["cleanup_epoch_index_used"] or not row["audit_valid"]:
            raise AssertionError("v0.25 overflow envelope lost indexed cleanup or audit")


def _require_reclamation_scaling(out: dict) -> None:
    rows = out["reclamation_scaling"]["rows"]
    capacities = [32, 128, 512, 2048]
    expected_bytes = [4096 * (capacity + 2) for capacity in capacities]
    if [int(row["initial_capacity"]) for row in rows] != capacities:
        raise AssertionError("v0.25 reclamation capacities drifted")
    observed = [int(row["reclaimed_tail_bytes"]) for row in rows]
    if observed != expected_bytes:
        raise AssertionError("v0.25 stale file-length formula drifted")
    for row, expected in zip(rows, expected_bytes):
        if int(row["stale_tail_bytes_before_recovery"]) != expected:
            raise AssertionError("v0.25 stale-tail pre-recovery range drifted")
        if int(row["truncate_calls"]) != 1 or int(row["fixed_file_fsyncs"]) != 1:
            raise AssertionError("v0.25 fixed-tail cleanup syscall envelope drifted")
        if int(row["cleanup_sqlite_commits"]) != 0:
            raise AssertionError("v0.25 tail-only recovery performed SQLite cleanup commit")
        if int(row["tail_bytes_after_recovery"]) != 0:
            raise AssertionError("v0.25 cleanup left stale fixed-page tail")
        if int(row["logical_redo"]) != 0 or not row["logical_snapshot_unchanged"]:
            raise AssertionError("v0.25 tail cleanup changed logical state")
        if not row["cleanup_epoch_index_used"] or not row["audit_valid"]:
            raise AssertionError("v0.25 reclamation lost indexed cleanup or audit")


def main() -> None:
    recorded_text = RESULTS_PATH.read_text()
    try:
        out = run_cross_store_hybrid_experiment.run()
        _require_semantic_guard(out)
        _require_crash_matrix(out)
        _require_resurrection_control(out)
        _require_common_envelope(out)
        _require_overflow_envelope(out)
        _require_reclamation_scaling(out)

        actual_sha = _sha256(RESULTS_PATH)
        if actual_sha != EXPECTED_RESULT_SHA256:
            raise AssertionError(
                "v0.25 executable evidence drifted from initial successful CI artifact: "
                f"expected {EXPECTED_RESULT_SHA256}, observed {actual_sha}"
            )

        parsed = json.loads(RESULTS_PATH.read_text())
        if parsed != out:
            raise AssertionError("serialized v0.25 result differs from returned experiment object")

        print("RECORDED_CROSS_STORE_HYBRID_RESULTS_MATCH_EXECUTABLE_EXPERIMENT")
        print(f"RESULT_SHA256={actual_sha}")
        print(f"INITIAL_ARTIFACT_SHA256={EXPECTED_ARTIFACT_SHA256}")
    finally:
        RESULTS_PATH.write_text(recorded_text)


if __name__ == "__main__":
    main()
