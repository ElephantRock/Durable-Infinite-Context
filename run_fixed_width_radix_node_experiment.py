from __future__ import annotations

import json
from pathlib import Path

from simulator.fixed_width_radix_nodes import (
    FAILPOINTS,
    POINTER_BASES,
    run_dense_root_and_crash_matrix,
    run_encoding_sweep,
)
from simulator.normalized_membership import run_v016_normalized_case

ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "fixed_width_radix_node_results.json"


def _require_semantic_guard(row: dict) -> None:
    required = (
        "membership_equal",
        "materialization_equal",
        "head_index_equal",
        "all_derived_fresh",
        "full_assembly_equal",
        "partial_assembly_equal",
    )
    if not all(bool(row[name]) for name in required):
        raise AssertionError("v0.16 semantic guard failed before v0.31 experiment")


def run() -> dict:
    semantic_guard = run_v016_normalized_case(
        entity_count=128,
        predicate_count=16,
        history_depth=8,
        changed_count=1,
    )
    _require_semantic_guard(semantic_guard)

    encoding = run_encoding_sweep()
    dense = run_dense_root_and_crash_matrix()

    if encoding["pointer_bases"] != list(POINTER_BASES):
        raise AssertionError("v0.31 pointer-magnitude sweep drifted")
    if dense["failpoints"] != list(FAILPOINTS):
        raise AssertionError("v0.31 crash failpoint set drifted")
    if int(encoding["fixed_effective_fanout"]) != 256:
        raise AssertionError("v0.31 fixed-width fanout drifted")
    if int(encoding["json_min_effective_fanout"]) >= 256:
        raise AssertionError("v0.31 JSON control no longer falsifies fixed physical fanout")
    if int(dense["root_entry_count_after"]) != 256:
        raise AssertionError("v0.31 dense root did not reach full fanout")
    if int(dense["node_splits"]) != 0 or int(dense["recursive_split_depth"]) != 0:
        raise AssertionError("v0.31 fixed-width node unexpectedly split")
    if int(dense["final_mapping_trace"]["radix_node_pwrites"]) != 8:
        raise AssertionError("v0.31 full-root final mapping exceeded fixed radix path writes")
    if int(dense["final_mapping_trace"]["physical_pages_appended"]) != 46:
        raise AssertionError("v0.31 full-root final mapping exceeded one fresh-path append")
    if not dense["all_exact_committed_state_match"]:
        raise AssertionError("v0.31 SIGKILL exposed ambiguous committed radix state")
    if not dense["all_recovery_scan_free"]:
        raise AssertionError("v0.31 recovery introduced a generation/mapping scan or logical redo")
    if not dense["all_second_recovery_idempotent"]:
        raise AssertionError("v0.31 recovery did not converge idempotently")
    if int(dense["max_uncommitted_tail_after_recovery_bytes"]) != 0:
        raise AssertionError("v0.31 recovery left file-length residue")
    if int(dense["max_uncommitted_tail_before_recovery_bytes"]) > int(
        dense["expected_fresh_path_bytes"]
    ):
        raise AssertionError("v0.31 pre-recovery tail exceeded fresh-path bound")

    for row in encoding["json_control"]:
        print(
            "RADIX_JSON_CONTROL",
            row["pointer_base"],
            {
                "max_entries": row["max_entries_that_fit"],
                "full_payload": row["full_fanout_payload_bytes"],
                "full_fits": row["full_fanout_fits"],
            },
        )
    for row in dense["rows"]:
        print(
            "RADIX_DENSE_CRASH",
            row["failpoint"],
            {
                "committed": row["expected_committed"],
                "root_entries": row["crash_root_entry_count"],
                "tail_before": row["uncommitted_tail_before_recovery_bytes"],
                "tail_after": row["uncommitted_tail_after_recovery_bytes"],
            },
        )

    out = {
        "experiment": "v0.31_fixed_width_radix_nodes",
        "semantic_guard": {
            "membership_equal": semantic_guard["membership_equal"],
            "materialization_equal": semantic_guard["materialization_equal"],
            "head_index_equal": semantic_guard["head_index_equal"],
            "all_derived_fresh": semantic_guard["all_derived_fresh"],
            "full_assembly_equal": semantic_guard["full_assembly_equal"],
            "partial_assembly_equal": semantic_guard["partial_assembly_equal"],
        },
        "encoding_sweep": encoding,
        "dense_root_crash_matrix": dense,
        "observe": (
            "v0.30's radix has a semantic fanout of exactly 256, but its JSON node encoding is "
            "variable-width. The number of legal edges that fit one 4096-byte page therefore falls "
            "as physical pointer values gain decimal digits, creating a latent representation-driven "
            "overflow before semantic fanout is exhausted."
        ),
        "first_principle": (
            "a fixed-radix node should have physical capacity determined only by its radix fanout, not "
            "by textual serialization length or pointer magnitude. Because a node has exactly 256 "
            "possible next-byte edges, a 256-bit occupancy bitmap plus 256 fixed 64-bit pointer slots "
            "eliminates the need for node splitting altogether."
        ),
        "hypothesis_under_test": (
            "replacing variable-width JSON radix nodes with one-page fixed-width 256-slot nodes will "
            "preserve the integrated v0.30 publication/recovery protocol while making full fanout fit "
            "independently of uint64 pointer magnitude and preventing recursive split cascades."
        ),
        "prediction": (
            "the JSON control must lose effective fanout below 256 at sufficiently large pointer "
            "magnitudes, while the fixed-width codec must round-trip all 256 entries at every tested "
            "magnitude. A real root transition from 255 to 256 entries must require zero splits, one "
            "bounded eight-node radix path update, at most 46 appended pages, two fsync barriers, and "
            "exact pre/post committed images under SIGKILL after allocation, child writes, parent "
            "write, dependency fsync, and committed superblock publication. Recovery must remain "
            "frontier-derived with zero generation scan, zero mapping scan, and zero logical redo."
        ),
        "result": (
            "survives only if the fixed-width node retains true 256-way physical fanout and the dense "
            "real-file crash matrix remains exact without splits or scan-based repair. This is not a "
            "claim of hardware power-loss safety, arbitrary multi-writer correctness, device-I/O "
            "bounds, production performance, or an address space beyond fixed uint64 pointers."
        ),
    }
    RESULTS_PATH.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    return out


if __name__ == "__main__":
    run()
