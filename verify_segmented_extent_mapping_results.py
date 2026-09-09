from __future__ import annotations

import hashlib
import json
from pathlib import Path

import run_segmented_extent_mapping_experiment

ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "segmented_extent_mapping_results.json"
EXPECTED_RESULT_SHA256 = "af728a264be667eb23de0292a4c40a42011ec412133939d5b32f49da53431702"
EXPECTED_ARTIFACT_SHA256 = "14320ed2f11fce149a735da64d442cf4d594eec73d664fb13da842a0de285034"


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
        raise AssertionError("v0.29 semantic guard failed")


def _require_sweep(out: dict) -> None:
    sweep = out["sweep"]
    capacities = [1024, 16384, 262144, 4194304]
    if sweep["capacities"] != capacities:
        raise AssertionError("v0.29 capacity sweep drifted")
    if int(sweep["page_size"]) != 4096 or int(sweep["bucket_size"]) != 4:
        raise AssertionError("v0.29 page/bucket geometry drifted")
    if int(sweep["segment_bucket_pages"]) != 16:
        raise AssertionError("v0.29 segment geometry drifted")
    if int(sweep["radix_levels"]) != 8 or int(sweep["logical_segment_bits"]) != 64:
        raise AssertionError("v0.29 radix namespace drifted")
    if int(sweep["sample_count"]) != 4096:
        raise AssertionError("v0.29 sample count drifted")

    rows = sweep["rows"]
    if len(rows) != len(capacities):
        raise AssertionError("v0.29 row count drifted")

    expected_flat_last = [4096, 8192, 131072, 2097152]
    expected_dense_radix = [16, 18, 78, 1040]
    for row, flat_last, dense_pages in zip(rows, expected_flat_last, expected_dense_radix):
        if int(row["flat_last_descriptor_residue_bytes"]) != flat_last:
            raise AssertionError("v0.29 flat descriptor control drifted")
        if int(row["radix_sparse_allocation_tail_bytes"]) != 188416:
            raise AssertionError("v0.29 sparse allocation tail drifted")
        if int(row["radix_sparse_new_descriptor_nodes"]) != 7:
            raise AssertionError("v0.29 fresh radix node count drifted")
        if int(row["radix_sparse_descriptor_reserved_pages"]) != 14:
            raise AssertionError("v0.29 reserved descriptor pages drifted")
        if int(row["radix_metadata_physical_preads"]) != 18:
            raise AssertionError("v0.29 metadata lookup fan-out drifted")
        if int(row["radix_data_physical_preads"]) != 2:
            raise AssertionError("v0.29 data lookup fan-out drifted")
        if int(row["radix_total_physical_preads"]) != 20:
            raise AssertionError("v0.29 total lookup fan-out drifted")
        if int(row["radix_metadata_pwrites_new_path"]) != 8:
            raise AssertionError("v0.29 fresh-path metadata write bound drifted")
        if int(row["radix_superblock_pwrites"]) != 1:
            raise AssertionError("v0.29 superblock publication write drifted")
        if int(row["radix_fsyncs"]) != 2:
            raise AssertionError("v0.29 publication barrier count drifted")
        if int(row["dense_radix_physical_descriptor_pages"]) != dense_pages:
            raise AssertionError("v0.29 dense descriptor accounting drifted")
        if int(row["flat_sampled_max_bytes"]) > int(row["flat_last_descriptor_residue_bytes"]):
            raise AssertionError("v0.29 sampled flat descriptor residue exceeded algebraic maximum")

    flat_last = [int(row["flat_last_descriptor_residue_bytes"]) for row in rows]
    if flat_last != sorted(flat_last) or flat_last[-1] <= flat_last[0]:
        raise AssertionError("v0.29 flat descriptor control no longer grows with capacity")
    if len({int(row["radix_sparse_allocation_tail_bytes"]) for row in rows}) != 1:
        raise AssertionError("v0.29 sparse allocation tail now grows with capacity")
    dense_radix = [int(row["dense_radix_physical_descriptor_pages"]) for row in rows]
    if dense_radix != sorted(dense_radix) or dense_radix[-1] <= dense_radix[0]:
        raise AssertionError("v0.29 dense descriptor growth is no longer exposed")


def _require_persistence(out: dict) -> None:
    persistence = out["persistence_ordering"]
    if int(persistence["safe_case_count"]) != 12:
        raise AssertionError("v0.29 safe persistence case count drifted")
    if int(persistence["safe_invalid_case_count"]) != 0:
        raise AssertionError("v0.29 dependency-before-commit protocol became unsafe")
    if int(persistence["control_case_count"]) != 17:
        raise AssertionError("v0.29 one-barrier control case count drifted")
    if int(persistence["control_invalid_case_count"]) != 7:
        raise AssertionError("v0.29 one-barrier control counterexample count drifted")
    first = persistence["control_first_counterexample"]
    if first != {"phase": "before_single_fsync", "durable": ["superblock"], "valid": False}:
        raise AssertionError("v0.29 first persistence-ordering counterexample drifted")


def main() -> None:
    recorded_text = RESULTS_PATH.read_text()
    recorded = json.loads(recorded_text)
    try:
        out = run_segmented_extent_mapping_experiment.run()
        _require_semantic_guard(out)
        _require_sweep(out)
        _require_persistence(out)

        actual_sha = _sha256(RESULTS_PATH)
        if actual_sha != EXPECTED_RESULT_SHA256:
            raise AssertionError(
                "v0.29 executable evidence drifted from initial successful CI artifact: "
                f"expected {EXPECTED_RESULT_SHA256}, observed {actual_sha}"
            )
        if recorded != out:
            raise AssertionError("committed v0.29 ledger differs from executable experiment object")

        print("RECORDED_SEGMENTED_EXTENT_MAPPING_RESULTS_MATCH_EXECUTABLE_EXPERIMENT")
        print(f"RESULT_SHA256={actual_sha}")
        print(f"INITIAL_ARTIFACT_SHA256={EXPECTED_ARTIFACT_SHA256}")
    finally:
        RESULTS_PATH.write_text(recorded_text)


if __name__ == "__main__":
    main()
