from __future__ import annotations

import hashlib
import json
from pathlib import Path

import run_durable_hybrid_experiment


ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "durable_hybrid_results.json"
EXPECTED_RESULT_SHA256 = "52bb6cc67910cec599af81dd0d262c69435f43c62570dd3170a3e2dcb44b4f5b"
EXPECTED_ARTIFACT_SHA256 = "2863530eb237237c343403a323f511c636db15af399467adc0be0cea6b4b5f57"


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
        raise AssertionError("v0.23 semantic guard failed")


def _require_crash_matrix(out: dict) -> None:
    scenarios = out["scenarios"]
    failpoints = out["failpoints"]
    rows = out["crash_rows"]
    if len(rows) != len(scenarios) * len(failpoints):
        raise AssertionError("v0.23 crash-matrix cardinality drifted")
    expected_pairs = {(scenario, failpoint) for scenario in scenarios for failpoint in failpoints}
    observed_pairs = {(row["scenario"], row["failpoint"]) for row in rows}
    if observed_pairs != expected_pairs:
        raise AssertionError("v0.23 crash-matrix coverage drifted")

    for row in rows:
        committed = row["failpoint"] == "committed"
        if bool(row["expected_committed"]) != committed:
            raise AssertionError("commit expectation disagrees with failpoint")
        if bool(row["target_visible_after_crash"]) != committed:
            raise AssertionError("SIGKILL target visibility disagrees with commit boundary")
        if not row["exact_snapshot_match"]:
            raise AssertionError("SIGKILL exposed a torn hybrid logical snapshot")
        if not row["audit_valid"] or not row["existing_keys_found"]:
            raise AssertionError("SIGKILL lost or duplicated durable membership")
        if not row["recovery_idempotent"]:
            raise AssertionError("hybrid recovery was not idempotent")
        for pass_name in ("recovery_one", "recovery_two"):
            recovery = row[pass_name]
            if int(recovery["logical_work"]) != 0:
                raise AssertionError("SQLite transaction recovery required application redo")
            if int(recovery["metadata_repairs"]) != 0 or int(recovery["row_repairs"]) != 0:
                raise AssertionError("SQLite transaction recovery required application repair")
            if not recovery["audit_valid"]:
                raise AssertionError("post-reopen hybrid audit failed")
        if row["journal_mode"] != "wal" or int(row["synchronous"]) != 2:
            raise AssertionError("crash case did not run under WAL + synchronous=FULL")
        if not row["primary_bucket_lookup_uses_index"] or not row["overflow_lookup_uses_index"]:
            raise AssertionError("persistent lookup plan lost its declared indexes")


def _require_common_envelope(out: dict) -> None:
    common = out["common_envelope"]
    if int(common["global_max_source_slots_scanned"]) > 8:
        raise AssertionError("source migration exceeded eight-slot budget")
    if int(common["global_max_rows_moved"]) > 8:
        raise AssertionError("source migration moved more than eight rows")
    if int(common["global_max_primary_mutation_work"]) > int(common["primary_mutation_work_bound"]):
        raise AssertionError("modeled primary mutation work exceeded derived bound")
    if int(common["migration_starts"]) <= 0 or int(common["migration_completions"]) <= 0:
        raise AssertionError("ordinary envelope failed to exercise completed migration")
    if common["settings"]["journal_mode"] != "wal" or int(common["settings"]["synchronous"]) != 2:
        raise AssertionError("ordinary envelope did not use WAL + FULL")
    if not common["primary_bucket_lookup_uses_index"] or not common["overflow_lookup_uses_index"]:
        raise AssertionError("ordinary envelope lookup plan lost an index")

    for row in common["rows"]:
        if int(row["overflow_rows"]) != 0:
            raise AssertionError("ordinary workload contaminated exceptional overflow")
        if int(row["lookup_overflow_checks"]) != 0:
            raise AssertionError("successful ordinary lookup queried overflow")
        if int(row["lookup_metadata_rows"]) != 1:
            raise AssertionError("ordinary lookup read more than singleton routing metadata")
        if int(row["interval_max_source_slots_scanned"]) > 8:
            raise AssertionError("interval source migration exceeded budget")
        if int(row["interval_max_rows_moved"]) > 8:
            raise AssertionError("interval migrated rows exceeded budget")
        if not row["audit_valid"]:
            raise AssertionError("ordinary envelope hybrid audit failed")


def _require_overflow_envelope(out: dict) -> None:
    overflow = out["overflow_envelope"]
    rows = overflow["rows"]
    heights = [int(row["overflow_btree_height"]) for row in rows]
    if heights != sorted(heights):
        raise AssertionError("overflow B-tree height is not monotone")
    if max(heights) <= min(heights):
        raise AssertionError("overflow envelope failed to expose growing B-tree depth")
    if heights != [int(value) for value in out["observed_overflow_btree_heights"]]:
        raise AssertionError("recorded overflow-height summary drifted")
    if int(overflow["max_primary_mutation_work"]) > 200:
        raise AssertionError("non-migrating overflow admission exceeded bounded primary cap")
    if int(overflow["overflow_logical_row_write_per_admission"]) != 1:
        raise AssertionError("overflow logical-row write count drifted")
    for row in rows:
        height = int(row["overflow_btree_height"])
        if int(row["overflow_hit_modeled_pages"]) != int(row["overflow_hit_primary_pages"]) + height:
            raise AssertionError("overflow-hit modeled-page accounting drifted")
        if int(row["missing_modeled_pages"]) != int(row["missing_primary_pages"]) + height:
            raise AssertionError("missing-key modeled-page accounting drifted")
        if not row["audit_valid"]:
            raise AssertionError("overflow envelope hybrid audit failed")


def main() -> None:
    out = run_durable_hybrid_experiment.run()
    _require_semantic_guard(out)
    _require_crash_matrix(out)
    _require_common_envelope(out)
    _require_overflow_envelope(out)

    actual_sha = _sha256(RESULTS_PATH)
    if actual_sha != EXPECTED_RESULT_SHA256:
        raise AssertionError(
            "v0.23 executable evidence drifted from initial successful CI artifact: "
            f"expected {EXPECTED_RESULT_SHA256}, observed {actual_sha}"
        )

    parsed = json.loads(RESULTS_PATH.read_text())
    if parsed != out:
        raise AssertionError("serialized v0.23 result differs from returned experiment object")

    print("RECORDED_DURABLE_HYBRID_RESULTS_MATCH_EXECUTABLE_EXPERIMENT")
    print(f"RESULT_SHA256={actual_sha}")
    print(f"INITIAL_ARTIFACT_SHA256={EXPECTED_ARTIFACT_SHA256}")


if __name__ == "__main__":
    main()
