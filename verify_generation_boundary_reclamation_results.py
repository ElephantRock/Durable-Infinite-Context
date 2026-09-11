from __future__ import annotations

import hashlib
import json
from pathlib import Path

import run_generation_boundary_reclamation_experiment

ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "generation_boundary_reclamation_results.json"
EXPECTED_RESULT_SHA256 = "548b7d9d3432478dcddbb28e8acb6473cfaedff1167374392e6160c83d18fa2d"
EXPECTED_ARTIFACT_SHA256 = "80a25be44e627a0975ff344cbca951e35d48e0f75fb695e8fee478f0661f3249"
EXPECTED_CAPACITIES = [32, 128, 2048, 131072, 4194304]
EXPECTED_PADDINGS = [7, 15, 15, 15, 15]
EXPECTED_FIRST_FAILPOINTS = [
    "allocated",
    "pages_written",
    "data_synced",
    "committed",
]
EXPECTED_REUSE_FAILPOINTS = [
    "reused_extent_scrubbed",
    "pages_written",
    "data_synced",
    "committed",
]
EXPECTED_RECLAIM_FAILPOINTS = [
    "mapping_unlinked",
    "free_header_written",
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
        raise AssertionError("v0.33 semantic guard failed")


def _require_boundary_control(out: dict) -> None:
    control = out["boundary_control"]
    if control["capacities"] != EXPECTED_CAPACITIES:
        raise AssertionError("v0.33 capacity sweep drifted")
    if int(control["segment_pages"]) != 16:
        raise AssertionError("v0.33 segment width drifted")
    if int(control["max_alignment_padding_pages"]) != 15:
        raise AssertionError("v0.33 alignment-padding envelope drifted")
    rows = control["rows"]
    if len(rows) != len(EXPECTED_CAPACITIES):
        raise AssertionError("v0.33 boundary-control cardinality drifted")
    for row, capacity, padding in zip(rows, EXPECTED_CAPACITIES, EXPECTED_PADDINGS):
        if int(row["old_capacity"]) != capacity:
            raise AssertionError("v0.33 boundary-control ordering drifted")
        if not bool(row["unaligned_shares_boundary_segment"]):
            raise AssertionError("v0.33 unaligned control lost shared-segment defect")
        if bool(row["aligned_shares_boundary_segment"]):
            raise AssertionError("v0.33 aligned candidate shares a generation boundary")
        if int(row["alignment_padding_pages"]) != padding:
            raise AssertionError("v0.33 alignment-padding ledger drifted")
        if int(row["aligned_new_first_segment"]) <= int(row["old_last_segment"]):
            raise AssertionError("v0.33 aligned generation did not advance segment ownership")


def _require_real_cycles(out: dict) -> None:
    cycles = out["real_generation_cycles"]
    if not bool(cycles["all_34_keys_visible"]):
        raise AssertionError("v0.33 real generation cleanup lost live keys")
    if int(cycles["first_alignment_padding_pages"]) != 7:
        raise AssertionError("v0.33 first alignment padding drifted")
    if int(cycles["second_alignment_padding_pages"]) != 15:
        raise AssertionError("v0.33 second alignment padding drifted")
    if int(cycles["second_active_old_last_segment"]) != 2:
        raise AssertionError("v0.33 old generation segment boundary drifted")
    if int(cycles["second_active_current_first_segment"]) != 3:
        raise AssertionError("v0.33 current generation segment boundary drifted")
    if int(cycles["second_active_current_first_segment"]) <= int(
        cycles["second_active_old_last_segment"]
    ):
        raise AssertionError("v0.33 real generations overlap one mapping segment")
    if int(cycles["first_physical_pages_appended"]) != 34:
        raise AssertionError("v0.33 first migration append ledger drifted")
    if int(cycles["second_trigger_physical_pages_appended"]) != 34:
        raise AssertionError("v0.33 second trigger append ledger drifted")
    if int(cycles["second_completion_physical_pages_appended"]) != 0:
        raise AssertionError("v0.33 second completion unexpectedly appended storage")
    if int(cycles["second_reused_free_extents"]) != 1:
        raise AssertionError("v0.33 real migration reuse count drifted")
    if int(cycles["second_scrub_pwrites"]) != 32:
        raise AssertionError("v0.33 fixed data scrub ledger drifted")
    if int(cycles["max_reclaim_pages_appended"]) != 0:
        raise AssertionError("v0.33 integrated reclaim appended storage")
    if int(cycles["max_reclaim_segments_per_step"]) > 3:
        raise AssertionError("v0.33 integrated reclaim exceeded fixed budget")


def _require_insert_matrix(matrix: dict, failpoints: list[str], *, reuse: bool) -> None:
    if matrix["failpoints"] != failpoints:
        raise AssertionError("v0.33 insert failpoint ledger drifted")
    if int(matrix["case_count"]) != 4:
        raise AssertionError("v0.33 insert crash matrix cardinality drifted")
    if not bool(matrix["all_exact_committed_state_match"]):
        raise AssertionError("v0.33 insert crash exposed ambiguous committed state")
    if not bool(matrix["all_recovery_scan_free"]):
        raise AssertionError("v0.33 insert recovery introduced scan-based repair")
    if not bool(matrix["all_second_recovery_idempotent"]):
        raise AssertionError("v0.33 insert recovery lost idempotence")
    if int(matrix["max_tail_before_recovery_bytes"]) != 139264:
        raise AssertionError("v0.33 insert crash-tail envelope drifted")
    if int(matrix["max_tail_after_recovery_bytes"]) != 0:
        raise AssertionError("v0.33 recovery left physical tail residue")

    trace = matrix["clean_trace"]
    if int(trace["physical_pages_appended"]) != 34:
        raise AssertionError("v0.33 clean insert append ledger drifted")
    if int(trace["physical_bytes_appended"]) != 139264:
        raise AssertionError("v0.33 clean insert byte ledger drifted")
    if int(trace["fsyncs"]) != 2:
        raise AssertionError("v0.33 publication barrier count drifted")
    if reuse:
        if int(trace["generation_alignment_padding_pages"]) != 15:
            raise AssertionError("v0.33 reuse migration padding drifted")
        if int(trace["reused_free_extents"]) != 1:
            raise AssertionError("v0.33 reuse crash control did not reuse one extent")
        if int(trace["data_page_scrub_pwrites"]) != 32:
            raise AssertionError("v0.33 reuse crash control scrub ledger drifted")
        if int(trace["fresh_physical_extents"]) != 1:
            raise AssertionError("v0.33 reuse migration fresh-extent ledger drifted")
        if int(trace["radix_node_pwrites"]) != 2:
            raise AssertionError("v0.33 reuse migration radix-write ledger drifted")
    else:
        if int(trace["generation_alignment_padding_pages"]) != 7:
            raise AssertionError("v0.33 first migration padding drifted")
        if int(trace["reused_free_extents"]) != 0:
            raise AssertionError("v0.33 first migration unexpectedly reused storage")
        if int(trace["data_page_scrub_pwrites"]) != 0:
            raise AssertionError("v0.33 first migration unexpectedly scrubbed storage")
        if int(trace["fresh_physical_extents"]) != 1:
            raise AssertionError("v0.33 first migration fresh-extent ledger drifted")
        if int(trace["radix_node_pwrites"]) != 1:
            raise AssertionError("v0.33 first migration radix-write ledger drifted")


def _require_crash_matrices(out: dict) -> None:
    matrices = out["insert_crash_matrices"]
    _require_insert_matrix(
        matrices["first_migration"], EXPECTED_FIRST_FAILPOINTS, reuse=False
    )
    _require_insert_matrix(
        matrices["reuse_migration"], EXPECTED_REUSE_FAILPOINTS, reuse=True
    )

    reclaim = out["reclaim_crash_matrix"]
    if reclaim["failpoints"] != EXPECTED_RECLAIM_FAILPOINTS:
        raise AssertionError("v0.33 reclaim failpoint ledger drifted")
    if int(reclaim["case_count"]) != 4:
        raise AssertionError("v0.33 reclaim crash matrix cardinality drifted")
    if not bool(reclaim["all_exact_committed_state_match"]):
        raise AssertionError("v0.33 reclaim crash exposed ambiguous state")
    if not bool(reclaim["all_live_keys_visible"]):
        raise AssertionError("v0.33 reclaim crash hid a live current-generation key")
    if not bool(reclaim["all_recovery_scan_free"]):
        raise AssertionError("v0.33 reclaim recovery introduced scan-based repair")
    trace = reclaim["clean_trace"]
    if int(trace["requested_budget"]) != 1 or int(trace["reclaimed_segments"]) != 1:
        raise AssertionError("v0.33 reclaim budget ledger drifted")
    if int(trace["physical_pages_appended"]) != 0:
        raise AssertionError("v0.33 reclaim appended storage")
    if int(trace["radix_node_pwrites"]) != 1:
        raise AssertionError("v0.33 reclaim radix-write ledger drifted")
    if int(trace["lifecycle_header_preads"]) != 4:
        raise AssertionError("v0.33 reclaim lifecycle-read ledger drifted")
    if int(trace["lifecycle_header_pwrites"]) != 1:
        raise AssertionError("v0.33 reclaim lifecycle-write ledger drifted")
    if int(trace["fsyncs"]) != 2:
        raise AssertionError("v0.33 reclaim publication barrier count drifted")
    if (
        int(trace["generation_pages_scanned"])
        or int(trace["mapping_nodes_scanned"])
        or int(trace["logical_redo"])
    ):
        raise AssertionError("v0.33 reclaim introduced scan/redo work")


def main() -> None:
    recorded_text = RESULTS_PATH.read_text()
    recorded = json.loads(recorded_text)
    try:
        out = run_generation_boundary_reclamation_experiment.run()
        _require_semantic_guard(out)
        _require_boundary_control(out)
        _require_real_cycles(out)
        _require_crash_matrices(out)

        actual_sha = _sha256(RESULTS_PATH)
        if actual_sha != EXPECTED_RESULT_SHA256:
            raise AssertionError(
                "v0.33 executable evidence drifted from initial successful CI artifact: "
                f"expected {EXPECTED_RESULT_SHA256}, observed {actual_sha}"
            )
        if recorded != out:
            raise AssertionError(
                "committed v0.33 ledger differs from executable experiment object"
            )

        print("RECORDED_GENERATION_BOUNDARY_RECLAMATION_RESULTS_MATCH_EXECUTABLE_EXPERIMENT")
        print(f"RESULT_SHA256={actual_sha}")
        print(f"INITIAL_ARTIFACT_SHA256={EXPECTED_ARTIFACT_SHA256}")
    finally:
        RESULTS_PATH.write_text(recorded_text)


if __name__ == "__main__":
    main()
