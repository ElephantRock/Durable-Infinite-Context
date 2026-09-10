from __future__ import annotations

import hashlib
import json
from pathlib import Path

import run_fixed_width_radix_node_experiment

ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "fixed_width_radix_node_results.json"
EXPECTED_RESULT_SHA256 = "b208fa84c4bd1eec0a5e77f24cdfe23794e09c1fa3cf41ba667808dcb3888e88"
EXPECTED_ARTIFACT_SHA256 = "df78992947a07cb6a4eb54a25a6b2135e311238795299446c001b7598a8fa9f1"
EXPECTED_POINTER_BASES = [
    1000,
    1000000,
    1000000000,
    1000000000000,
    1000000000000000,
    1000000000000000000,
    18446744073709551360,
]
EXPECTED_JSON_EFFECTIVE_FANOUT = [256, 256, 244, 207, 180, 159, 154]
EXPECTED_JSON_FULL_PAYLOAD_BYTES = [2729, 3497, 4265, 5033, 5801, 6569, 6825]
EXPECTED_FAILPOINTS = [
    "allocated",
    "children_written",
    "parent_written",
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
        raise AssertionError("v0.31 semantic guard failed")


def _require_encoding(out: dict) -> None:
    encoding = out["encoding_sweep"]
    if encoding["pointer_bases"] != EXPECTED_POINTER_BASES:
        raise AssertionError("v0.31 pointer-magnitude sweep drifted")
    if int(encoding["max_json_payload_bytes"]) != 4072:
        raise AssertionError("v0.31 JSON payload envelope drifted")
    if int(encoding["radix_fanout"]) != 256:
        raise AssertionError("v0.31 radix fanout drifted")
    if int(encoding["fixed_effective_fanout"]) != 256:
        raise AssertionError("v0.31 fixed-width fanout drifted")
    if int(encoding["fixed_node_used_bytes"]) != 2103:
        raise AssertionError("v0.31 fixed node layout size drifted")
    if int(encoding["fixed_node_padding_bytes"]) != 1993:
        raise AssertionError("v0.31 fixed node padding drifted")

    json_rows = encoding["json_control"]
    fixed_rows = encoding["fixed_width"]
    if len(json_rows) != len(EXPECTED_POINTER_BASES) or len(fixed_rows) != len(
        EXPECTED_POINTER_BASES
    ):
        raise AssertionError("v0.31 encoding sweep cardinality drifted")
    for row, pointer_base, effective_fanout, full_payload in zip(
        json_rows,
        EXPECTED_POINTER_BASES,
        EXPECTED_JSON_EFFECTIVE_FANOUT,
        EXPECTED_JSON_FULL_PAYLOAD_BYTES,
    ):
        if int(row["pointer_base"]) != pointer_base:
            raise AssertionError("v0.31 JSON pointer row ordering drifted")
        if int(row["max_entries_that_fit"]) != effective_fanout:
            raise AssertionError("v0.31 JSON effective fanout ledger drifted")
        if int(row["full_fanout_payload_bytes"]) != full_payload:
            raise AssertionError("v0.31 JSON full-fanout payload ledger drifted")
        if bool(row["full_fanout_fits"]) != (effective_fanout == 256):
            raise AssertionError("v0.31 JSON full-fanout fit classification drifted")
    if int(encoding["json_min_effective_fanout"]) != 154:
        raise AssertionError("v0.31 JSON minimum effective fanout drifted")

    for row, pointer_base in zip(fixed_rows, EXPECTED_POINTER_BASES):
        if int(row["pointer_base"]) != pointer_base:
            raise AssertionError("v0.31 fixed pointer row ordering drifted")
        if int(row["entry_count"]) != 256:
            raise AssertionError("v0.31 fixed codec lost semantic fanout")
        if int(row["page_bytes"]) != 4096 or int(row["used_bytes"]) != 2103:
            raise AssertionError("v0.31 fixed codec page geometry drifted")
        if not bool(row["round_trip_exact"]):
            raise AssertionError("v0.31 fixed codec round-trip failed")


def _require_dense_crash(out: dict) -> None:
    dense = out["dense_root_crash_matrix"]
    if dense["failpoints"] != EXPECTED_FAILPOINTS:
        raise AssertionError("v0.31 crash failpoint set drifted")
    if int(dense["case_count"]) != 5 or len(dense["rows"]) != 5:
        raise AssertionError("v0.31 crash matrix cardinality drifted")
    if int(dense["root_entry_count_before"]) != 255:
        raise AssertionError("v0.31 dense-root precondition drifted")
    if int(dense["root_entry_count_after"]) != 256:
        raise AssertionError("v0.31 full-fanout root result drifted")
    if int(dense["node_splits"]) != 0 or int(dense["recursive_split_depth"]) != 0:
        raise AssertionError("v0.31 fixed-width node introduced a split cascade")
    if int(dense["expected_fresh_path_pages"]) != 46:
        raise AssertionError("v0.31 fresh-path page envelope drifted")
    if int(dense["expected_fresh_path_bytes"]) != 188416:
        raise AssertionError("v0.31 fresh-path byte envelope drifted")

    trace = dense["final_mapping_trace"]
    if int(trace["new_segments_allocated"]) != 1:
        raise AssertionError("v0.31 final mapping no longer allocates one segment")
    if int(trace["physical_pages_appended"]) != 46:
        raise AssertionError("v0.31 final mapping append ledger drifted")
    if int(trace["physical_bytes_appended"]) != 188416:
        raise AssertionError("v0.31 final mapping byte ledger drifted")
    if int(trace["radix_node_pwrites"]) != 8:
        raise AssertionError("v0.31 final radix write count drifted")
    if int(trace["fsyncs"]) != 2:
        raise AssertionError("v0.31 publication barrier count drifted")

    fixture = dense["dense_root_fixture"]
    if int(fixture["trace"]["mappings_requested"]) != 255:
        raise AssertionError("v0.31 dense-root fixture mapping count drifted")
    if int(fixture["trace"]["new_segments_allocated"]) != 255:
        raise AssertionError("v0.31 dense-root fixture allocation count drifted")
    if int(fixture["snapshot"]["root_entry_count"]) != 255:
        raise AssertionError("v0.31 dense-root fixture root count drifted")

    if not bool(dense["all_exact_committed_state_match"]):
        raise AssertionError("v0.31 crash matrix exposed mixed committed state")
    if not bool(dense["all_recovery_scan_free"]):
        raise AssertionError("v0.31 recovery introduced scan-based repair")
    if not bool(dense["all_second_recovery_idempotent"]):
        raise AssertionError("v0.31 recovery lost idempotence")
    if int(dense["max_uncommitted_tail_before_recovery_bytes"]) != 188416:
        raise AssertionError("v0.31 max pre-recovery tail drifted")
    if int(dense["max_uncommitted_tail_after_recovery_bytes"]) != 0:
        raise AssertionError("v0.31 recovery left file-length residue")

    seen: set[str] = set()
    for row in dense["rows"]:
        failpoint = str(row["failpoint"])
        seen.add(failpoint)
        expected_committed = failpoint == "committed"
        if bool(row["expected_committed"]) != expected_committed:
            raise AssertionError("v0.31 expected-commit classification drifted")
        if not bool(row["exact_committed_state_match"]):
            raise AssertionError("v0.31 crash row lost exact committed-state match")
        if bool(row["target_mapping_visible"]) != expected_committed:
            raise AssertionError("v0.31 target mapping crash visibility drifted")
        expected_root = 256 if expected_committed else 255
        if int(row["crash_root_entry_count"]) != expected_root:
            raise AssertionError("v0.31 crash root visibility drifted")
        expected_tail = 0 if expected_committed else 188416
        if int(row["uncommitted_tail_before_recovery_bytes"]) != expected_tail:
            raise AssertionError("v0.31 crash tail ledger drifted")
        if int(row["uncommitted_tail_after_recovery_bytes"]) != 0:
            raise AssertionError("v0.31 crash row retained tail after recovery")
        for recovery in (row["recovery_one"], row["recovery_two"]):
            if int(recovery["logical_work"]) != 0:
                raise AssertionError("v0.31 recovery required logical redo")
            if int(recovery["generation_pages_scanned"]) != 0:
                raise AssertionError("v0.31 recovery scanned generation pages")
            if int(recovery["mapping_nodes_scanned"]) != 0:
                raise AssertionError("v0.31 recovery scanned mapping nodes")
            if int(recovery["frontier_superblock_preads"]) != 2:
                raise AssertionError("v0.31 frontier discovery read count drifted")
        if int(row["recovery_two"]["physical_truncated_bytes"]) != 0:
            raise AssertionError("v0.31 second recovery pass ceased to be idempotent")
    if seen != set(EXPECTED_FAILPOINTS):
        raise AssertionError("v0.31 crash matrix coverage drifted")


def main() -> None:
    recorded_text = RESULTS_PATH.read_text()
    recorded = json.loads(recorded_text)
    try:
        out = run_fixed_width_radix_node_experiment.run()
        _require_semantic_guard(out)
        _require_encoding(out)
        _require_dense_crash(out)

        actual_sha = _sha256(RESULTS_PATH)
        if actual_sha != EXPECTED_RESULT_SHA256:
            raise AssertionError(
                "v0.31 executable evidence drifted from initial successful CI artifact: "
                f"expected {EXPECTED_RESULT_SHA256}, observed {actual_sha}"
            )
        if recorded != out:
            raise AssertionError("committed v0.31 ledger differs from executable experiment object")

        print("RECORDED_FIXED_WIDTH_RADIX_NODE_RESULTS_MATCH_EXECUTABLE_EXPERIMENT")
        print(f"RESULT_SHA256={actual_sha}")
        print(f"INITIAL_ARTIFACT_SHA256={EXPECTED_ARTIFACT_SHA256}")
    finally:
        RESULTS_PATH.write_text(recorded_text)


if __name__ == "__main__":
    main()
