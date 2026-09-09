from __future__ import annotations

import hashlib
import json
from pathlib import Path

import run_integrated_segmented_primary_experiment

ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "integrated_segmented_primary_results.json"
EXPECTED_RESULT_SHA256 = "448373cde685166dc1c3c10ac23a0ce4d5c7c650f3c6617ac581acc6db700dd8"
EXPECTED_ARTIFACT_SHA256 = "719573b3cf6b4a5c61f179cd3547b19723a2558f430bd42975b9468c7ad8775f"
EXPECTED_CAPACITIES = [32, 2048, 131072, 4194304]
EXPECTED_APPEND_BYTES = [131072, 131072, 139264, 147456]
EXPECTED_TARGET_PREADS = [20, 20, 20, 20]
EXPECTED_MISS_PREADS = [46, 98, 88, 84]
EXPECTED_TARGET_SEGMENTS = [1, 80, 4618, 98773]


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
        raise AssertionError("v0.30 semantic guard failed")


def _require_capacity_sweep(out: dict) -> None:
    sweep = out["capacity_sweep"]
    if sweep["capacities"] != EXPECTED_CAPACITIES:
        raise AssertionError("v0.30 capacity sweep drifted")
    if int(sweep["per_fresh_segment_max_pages"]) != 46:
        raise AssertionError("v0.30 per-segment page envelope drifted")
    if int(sweep["per_fresh_segment_max_bytes"]) != 188416:
        raise AssertionError("v0.30 per-segment byte envelope drifted")
    if int(sweep["derived_transaction_segment_cap"]) != 305:
        raise AssertionError("v0.30 derived transaction segment cap drifted")
    if int(sweep["derived_transaction_tail_cap_bytes"]) != 57466880:
        raise AssertionError("v0.30 derived transaction tail cap drifted")
    if int(sweep["single_generation_lookup_pread_bound"]) != 56:
        raise AssertionError("v0.30 single-generation lookup bound drifted")
    if int(sweep["two_generation_lookup_pread_bound"]) != 110:
        raise AssertionError("v0.30 two-generation lookup bound drifted")

    rows = sweep["rows"]
    if len(rows) != len(EXPECTED_CAPACITIES):
        raise AssertionError("v0.30 capacity row count drifted")
    for row, capacity, append_bytes, target_preads, miss_preads, target_segment in zip(
        rows,
        EXPECTED_CAPACITIES,
        EXPECTED_APPEND_BYTES,
        EXPECTED_TARGET_PREADS,
        EXPECTED_MISS_PREADS,
        EXPECTED_TARGET_SEGMENTS,
    ):
        if int(row["old_capacity"]) != capacity:
            raise AssertionError("v0.30 capacity row ordering drifted")
        if int(row["new_capacity"]) != 2 * capacity:
            raise AssertionError("v0.30 doubled capacity drifted")
        if int(row["new_segments_allocated"]) != 1:
            raise AssertionError("v0.30 fixture no longer forces exactly one fresh segment")
        if int(row["physical_bytes_appended"]) != append_bytes:
            raise AssertionError("v0.30 observed append ledger drifted")
        if int(row["physical_bytes_appended"]) > int(sweep["per_fresh_segment_max_bytes"]):
            raise AssertionError("v0.30 append exceeded one-fresh-segment envelope")
        if int(row["lookup_total_preads"]) != target_preads:
            raise AssertionError("v0.30 target lookup ledger drifted")
        if int(row["missing_total_preads"]) != miss_preads:
            raise AssertionError("v0.30 active-migration miss ledger drifted")
        if int(row["lookup_total_preads"]) > int(sweep["single_generation_lookup_pread_bound"]):
            raise AssertionError("v0.30 target lookup exceeded fixed bound")
        if int(row["missing_total_preads"]) > int(sweep["two_generation_lookup_pread_bound"]):
            raise AssertionError("v0.30 active-migration miss exceeded fixed bound")
        if int(row["target_logical_segment_id_max"]) != target_segment:
            raise AssertionError("v0.30 target logical segment ledger drifted")
        if int(row["file_size_bytes"]) != int(row["committed_physical_frontier_bytes"]):
            raise AssertionError("v0.30 successful commit left file beyond committed frontier")

    bases = [int(row["logical_generation_base_page"]) for row in rows]
    segments = [int(row["target_logical_segment_id_max"]) for row in rows]
    if bases != sorted(bases) or bases[-1] <= bases[0]:
        raise AssertionError("v0.30 logical generation ids no longer grow across the sweep")
    if segments != sorted(segments) or segments[-1] <= segments[0]:
        raise AssertionError("v0.30 target segment ids no longer expose capacity growth")
    if int(sweep["max_observed_physical_bytes_appended"]) != max(EXPECTED_APPEND_BYTES):
        raise AssertionError("v0.30 max append ledger drifted")


def _require_crash_matrix(out: dict) -> None:
    crash = out["crash_matrix"]
    expected_failpoints = ["allocated", "pages_written", "data_synced", "committed"]
    if crash["capacities"] != EXPECTED_CAPACITIES:
        raise AssertionError("v0.30 crash capacities drifted")
    if crash["failpoints"] != expected_failpoints:
        raise AssertionError("v0.30 crash failpoints drifted")
    if int(crash["case_count"]) != 16 or len(crash["rows"]) != 16:
        raise AssertionError("v0.30 crash matrix cardinality drifted")
    if not bool(crash["all_exact_committed_state_match"]):
        raise AssertionError("v0.30 crash matrix exposed mixed committed state")
    if not bool(crash["all_existing_seed_visible"]):
        raise AssertionError("v0.30 crash matrix lost pre-existing membership")
    if not bool(crash["all_recovery_state_unchanged"]):
        raise AssertionError("v0.30 recovery changed committed logical state")
    if int(crash["max_uncommitted_tail_before_recovery_bytes"]) != max(EXPECTED_APPEND_BYTES):
        raise AssertionError("v0.30 max pre-recovery tail drifted")
    if int(crash["max_uncommitted_tail_after_recovery_bytes"]) != 0:
        raise AssertionError("v0.30 recovery left file-length residue")

    append_by_capacity = dict(zip(EXPECTED_CAPACITIES, EXPECTED_APPEND_BYTES))
    seen: set[tuple[int, str]] = set()
    for row in crash["rows"]:
        capacity = int(row["old_capacity"])
        failpoint = str(row["failpoint"])
        seen.add((capacity, failpoint))
        expected_committed = failpoint == "committed"
        if bool(row["expected_committed"]) != expected_committed:
            raise AssertionError("v0.30 expected-commit classification drifted")
        if bool(row["target_visible_after_crash"]) != expected_committed:
            raise AssertionError("v0.30 target crash visibility drifted")
        if not bool(row["existing_seed_visible_after_crash"]):
            raise AssertionError("v0.30 existing seed disappeared after crash")
        if not bool(row["exact_committed_state_match"]):
            raise AssertionError("v0.30 crash row no longer matches exact committed state")
        tail_before = int(row["uncommitted_tail_before_recovery_bytes"])
        expected_tail = 0 if expected_committed else append_by_capacity[capacity]
        if tail_before != expected_tail:
            raise AssertionError("v0.30 pre-recovery crash tail drifted")
        if int(row["uncommitted_tail_after_recovery_bytes"]) != 0:
            raise AssertionError("v0.30 crash row retained tail after recovery")
        if int(row["control_physical_bytes_appended"]) != append_by_capacity[capacity]:
            raise AssertionError("v0.30 deterministic control append drifted")
        if int(row["control_new_segments_allocated"]) != 1:
            raise AssertionError("v0.30 crash control no longer allocates one fresh segment")
        for recovery in (row["recovery_one"], row["recovery_two"]):
            if int(recovery["logical_work"]) != 0:
                raise AssertionError("v0.30 recovery required logical redo")
            if int(recovery["generation_pages_scanned"]) != 0:
                raise AssertionError("v0.30 recovery scanned generation pages")
            if int(recovery["mapping_nodes_scanned"]) != 0:
                raise AssertionError("v0.30 recovery scanned mapping nodes")
            if int(recovery["frontier_superblock_preads"]) != 2:
                raise AssertionError("v0.30 recovery frontier discovery drifted")
        if int(row["recovery_two"]["physical_truncated_bytes"]) != 0:
            raise AssertionError("v0.30 second recovery pass lost physical idempotence")
        if not bool(row["recovery_state_unchanged"]):
            raise AssertionError("v0.30 recovery state equality drifted")
    expected_seen = {(c, f) for c in EXPECTED_CAPACITIES for f in expected_failpoints}
    if seen != expected_seen:
        raise AssertionError("v0.30 crash matrix coverage drifted")


def main() -> None:
    recorded_text = RESULTS_PATH.read_text()
    recorded = json.loads(recorded_text)
    try:
        out = run_integrated_segmented_primary_experiment.run()
        _require_semantic_guard(out)
        _require_capacity_sweep(out)
        _require_crash_matrix(out)

        actual_sha = _sha256(RESULTS_PATH)
        if actual_sha != EXPECTED_RESULT_SHA256:
            raise AssertionError(
                "v0.30 executable evidence drifted from initial successful full CI artifact: "
                f"expected {EXPECTED_RESULT_SHA256}, observed {actual_sha}"
            )
        if recorded != out:
            raise AssertionError("committed v0.30 ledger differs from executable experiment object")

        print("RECORDED_INTEGRATED_SEGMENTED_PRIMARY_RESULTS_MATCH_EXECUTABLE_EXPERIMENT")
        print(f"RESULT_SHA256={actual_sha}")
        print(f"INITIAL_ARTIFACT_SHA256={EXPECTED_ARTIFACT_SHA256}")
    finally:
        RESULTS_PATH.write_text(recorded_text)


if __name__ == "__main__":
    main()
