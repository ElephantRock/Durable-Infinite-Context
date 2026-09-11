from __future__ import annotations

import hashlib
import json
from pathlib import Path

import run_retired_generation_reclamation_experiment

ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "retired_generation_reclamation_results.json"
EXPECTED_RESULT_SHA256 = "79b7f10eb91f4cc80aab8ec30f9155e8a3c49b362bbf3c1b2c0cb865b3ee4e22"
EXPECTED_ARTIFACT_SHA256 = "cffe21ddee73e381beff5ea1cb0b1827e0ca82aaf60cc207e790e56ad4392c31"
EXPECTED_BACKLOGS = [1, 8, 32, 64]
EXPECTED_RECLAIM_FAILPOINTS = [
    "mapping_unlinked",
    "free_header_written",
    "dependencies_synced",
    "committed",
]
EXPECTED_REUSE_FAILPOINTS = [
    "data_scrubbed",
    "mapping_written",
    "owner_header_written",
    "dependencies_synced",
    "committed",
]


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
        raise AssertionError("v0.32 semantic guard failed")


def _require_budget(out: dict) -> None:
    budget = out["budget_sweep"]
    if budget["backlog_counts"] != EXPECTED_BACKLOGS:
        raise AssertionError("v0.32 backlog sweep drifted")
    if int(budget["budget"]) != 3:
        raise AssertionError("v0.32 reclaim budget drifted")
    expected_steps = [1, 3, 11, 22]
    rows = budget["rows"]
    if [int(row["step_count"]) for row in rows] != expected_steps:
        raise AssertionError("v0.32 reclaim step cardinality drifted")
    if int(budget["global_max_reclaimed_per_step"]) != 3:
        raise AssertionError("v0.32 max reclaimed work drifted")
    if int(budget["global_max_lifecycle_preads_per_step"]) != 12:
        raise AssertionError("v0.32 lifecycle pread bound drifted")
    if int(budget["global_max_lifecycle_pwrites_per_step"]) != 3:
        raise AssertionError("v0.32 lifecycle pwrite bound drifted")
    if int(budget["global_max_radix_pwrites_per_step"]) != 3:
        raise AssertionError("v0.32 radix pwrite bound drifted")
    if int(budget["global_max_physical_pages_appended_per_step"]) != 0:
        raise AssertionError("v0.32 reclaim started appending storage")
    for row, backlog in zip(rows, EXPECTED_BACKLOGS):
        if int(row["retired_segments"]) != backlog:
            raise AssertionError("v0.32 backlog row ordering drifted")
        if int(row["final_free_count"]) != backlog:
            raise AssertionError("v0.32 reclaim did not drain retired ownership")
        if int(row["max_physical_pages_appended_per_step"]) != 0:
            raise AssertionError("v0.32 reclaim row appended storage")


def _require_controls(out: dict) -> None:
    controls = out["controls"]
    manifests = controls["manifest_rows"]
    expected_manifest_bytes = [40, 166, 598, 1174]
    if [int(row["flat_manifest_bytes"]) for row in manifests] != expected_manifest_bytes:
        raise AssertionError("v0.32 flat-manifest control drifted")
    capacities = controls["capacity_rows"]
    expected_capacities = [1024, 1048576, 1073741824, 1099511627776]
    if [int(row["logical_generation_capacity"]) for row in capacities] != expected_capacities:
        raise AssertionError("v0.32 capacity control drifted")
    if any(int(row["candidate_reclaim_visits_per_step"]) != 3 for row in capacities):
        raise AssertionError("v0.32 candidate visit budget drifted")


def _require_reuse(out: dict) -> None:
    stale = out["stale_payload_control"]
    if not bool(stale["unsafe_reused_same_extent"]):
        raise AssertionError("v0.32 unsafe control did not reuse the intended extent")
    if not bool(stale["unsafe_stale_payload_visible"]):
        raise AssertionError("v0.32 unsafe control no longer exposes seeded stale payload")
    if not bool(stale["safe_reused_same_extent"]):
        raise AssertionError("v0.32 safe candidate did not reuse the intended extent")
    if bool(stale["safe_stale_payload_visible"]):
        raise AssertionError("v0.32 safe reuse exposed retired payload")
    if bool(stale["safe_old_mapping_visible"]):
        raise AssertionError("v0.32 safe reuse resurrected retired mapping")
    if int(stale["safe_data_page_scrub_pwrites"]) != 32:
        raise AssertionError("v0.32 fixed data scrub footprint drifted")
    if int(stale["safe_radix_node_pwrites"]) != 1:
        raise AssertionError("v0.32 shared-path reuse radix work drifted")
    if int(stale["safe_physical_pages_appended"]) != 0:
        raise AssertionError("v0.32 shared-path reuse appended storage")
    if int(stale["safe_fsyncs"]) != 2:
        raise AssertionError("v0.32 reuse publication barrier count drifted")


def _require_crash_matrices(out: dict) -> None:
    reclaim = out["reclaim_crash_matrix"]
    if reclaim["failpoints"] != EXPECTED_RECLAIM_FAILPOINTS:
        raise AssertionError("v0.32 reclaim failpoint set drifted")
    if int(reclaim["case_count"]) != 4:
        raise AssertionError("v0.32 reclaim crash cardinality drifted")
    if int(reclaim["clean_reclaimed_segments"]) != 2:
        raise AssertionError("v0.32 clean reclaim fixture drifted")
    if int(reclaim["clean_physical_pages_appended"]) != 0:
        raise AssertionError("v0.32 clean reclaim appended storage")
    if not bool(reclaim["all_exact_committed_state_match"]):
        raise AssertionError("v0.32 reclaim crash state became ambiguous")
    if not bool(reclaim["all_recovery_scan_free"]):
        raise AssertionError("v0.32 reclaim recovery introduced scan/redo")
    if not bool(reclaim["all_second_recovery_idempotent"]):
        raise AssertionError("v0.32 reclaim recovery lost idempotence")

    reuse = out["reuse_crash_matrix"]
    if reuse["failpoints"] != EXPECTED_REUSE_FAILPOINTS:
        raise AssertionError("v0.32 reuse failpoint set drifted")
    if int(reuse["case_count"]) != 5:
        raise AssertionError("v0.32 reuse crash cardinality drifted")
    if not bool(reuse["clean_reused_same_extent"]):
        raise AssertionError("v0.32 clean reuse stopped reusing the free extent")
    if int(reuse["clean_data_page_scrub_pwrites"]) != 32:
        raise AssertionError("v0.32 clean reuse scrub count drifted")
    if int(reuse["clean_radix_node_pwrites"]) != 1:
        raise AssertionError("v0.32 clean reuse radix work drifted")
    if int(reuse["clean_physical_pages_appended"]) != 0:
        raise AssertionError("v0.32 clean shared-path reuse appended storage")
    if not bool(reuse["all_exact_committed_state_match"]):
        raise AssertionError("v0.32 reuse crash state became ambiguous")
    if not bool(reuse["all_recovery_scan_free"]):
        raise AssertionError("v0.32 reuse recovery introduced scan/redo")
    if not bool(reuse["all_second_recovery_idempotent"]):
        raise AssertionError("v0.32 reuse recovery lost idempotence")
    if not bool(reuse["all_retry_or_committed_reuse_exact"]):
        raise AssertionError("v0.32 reuse retry semantics drifted")


def main() -> None:
    recorded_text = RESULTS_PATH.read_text()
    recorded = json.loads(recorded_text)
    try:
        out = run_retired_generation_reclamation_experiment.run()
        _require_semantic_guard(out)
        _require_controls(out)
        _require_budget(out)
        _require_reuse(out)
        _require_crash_matrices(out)

        actual_sha = _sha256(RESULTS_PATH)
        if actual_sha != EXPECTED_RESULT_SHA256:
            raise AssertionError(
                "v0.32 executable evidence drifted from successful CI artifact: "
                f"expected {EXPECTED_RESULT_SHA256}, observed {actual_sha}"
            )
        if recorded != out:
            raise AssertionError("committed v0.32 ledger differs from executable experiment object")

        print("RECORDED_RETIRED_GENERATION_RECLAMATION_RESULTS_MATCH_EXECUTABLE_EXPERIMENT")
        print(f"RESULT_SHA256={actual_sha}")
        print(f"INITIAL_ARTIFACT_SHA256={EXPECTED_ARTIFACT_SHA256}")
    finally:
        RESULTS_PATH.write_text(recorded_text)


if __name__ == "__main__":
    main()
