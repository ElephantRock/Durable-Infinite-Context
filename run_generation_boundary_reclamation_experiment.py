from __future__ import annotations

import json
from pathlib import Path

from simulator.generation_boundary_reclamation import (
    FIRST_INSERT_FAILPOINTS,
    RECLAIM_FAILPOINTS,
    REUSE_INSERT_FAILPOINTS,
    run_boundary_control,
    run_insert_crash_matrices,
    run_real_two_generation_cycles,
    run_reclaim_crash_matrix,
)
from simulator.normalized_membership import run_v016_normalized_case
from storage.segmented_fixed_page_primary import SEGMENT_BUCKET_PAGES

ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "generation_boundary_reclamation_results.json"


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
        raise AssertionError("v0.16 semantic guard failed before v0.33 experiment")


def _insert_matrix_summary(matrix: dict) -> dict:
    rows = matrix["rows"]
    return {
        "failpoints": list(matrix["failpoints"]),
        "case_count": int(matrix["case_count"]),
        "all_exact_committed_state_match": bool(
            matrix["all_exact_committed_state_match"]
        ),
        "all_recovery_scan_free": bool(matrix["all_recovery_scan_free"]),
        "all_second_recovery_idempotent": bool(
            matrix["all_second_recovery_idempotent"]
        ),
        "max_tail_before_recovery_bytes": max(
            int(row["recovery_one"]["tail_before_bytes"]) for row in rows
        ),
        "max_tail_after_recovery_bytes": max(
            int(row["recovery_one"]["tail_after_bytes"]) for row in rows
        ),
        "clean_trace": {
            key: matrix["clean_trace"][key]
            for key in (
                "migration_started",
                "migration_completed",
                "migration_source_slots_scanned",
                "migration_rows_moved",
                "generation_alignment_padding_pages",
                "new_segments_allocated",
                "fresh_physical_extents",
                "reused_free_extents",
                "data_page_scrub_pwrites",
                "physical_pages_appended",
                "physical_bytes_appended",
                "radix_node_pwrites",
                "lifecycle_header_preads",
                "lifecycle_header_pwrites",
                "fsyncs",
            )
        },
    }


def run() -> dict:
    semantic_guard = run_v016_normalized_case(
        entity_count=128,
        predicate_count=16,
        history_depth=8,
        changed_count=1,
    )
    _require_semantic_guard(semantic_guard)

    control = run_boundary_control()
    cycles = run_real_two_generation_cycles()
    inserts = run_insert_crash_matrices()
    reclaim = run_reclaim_crash_matrix()

    if int(control["segment_pages"]) != SEGMENT_BUCKET_PAGES:
        raise AssertionError("v0.33 segment width drifted")
    if int(control["max_alignment_padding_pages"]) >= SEGMENT_BUCKET_PAGES:
        raise AssertionError("v0.33 alignment overhead exceeded fixed bound")
    if not all(bool(row["unaligned_shares_boundary_segment"]) for row in control["rows"]):
        raise AssertionError("v0.33 unaligned control lost its shared-segment defect")
    if any(bool(row["aligned_shares_boundary_segment"]) for row in control["rows"]):
        raise AssertionError("v0.33 aligned candidate retained a shared boundary")

    if not cycles["all_34_keys_visible"]:
        raise AssertionError("v0.33 real generation cleanup lost live keys")
    if int(cycles["max_reclaim_pages_appended"]) != 0:
        raise AssertionError("v0.33 integrated reclamation appended pages")
    if int(cycles["max_reclaim_segments_per_step"]) > 3:
        raise AssertionError("v0.33 integrated reclamation escaped fixed budget")
    if int(cycles["second_reused_free_extents"]) <= 0:
        raise AssertionError("v0.33 real migration did not reuse retired storage")
    if int(cycles["second_scrub_pwrites"]) < 32:
        raise AssertionError("v0.33 real reuse omitted fixed-footprint scrub")

    first = inserts["first_migration"]
    reuse = inserts["reuse_migration"]
    if first["failpoints"] != list(FIRST_INSERT_FAILPOINTS):
        raise AssertionError("v0.33 first-migration crash set drifted")
    if reuse["failpoints"] != list(REUSE_INSERT_FAILPOINTS):
        raise AssertionError("v0.33 reuse-migration crash set drifted")
    for matrix in (first, reuse):
        if not matrix["all_exact_committed_state_match"]:
            raise AssertionError("v0.33 insert crash exposed ambiguous state")
        if not matrix["all_recovery_scan_free"]:
            raise AssertionError("v0.33 insert recovery introduced scan-based repair")
        if not matrix["all_second_recovery_idempotent"]:
            raise AssertionError("v0.33 second recovery lost idempotence")

    if reclaim["failpoints"] != list(RECLAIM_FAILPOINTS):
        raise AssertionError("v0.33 reclaim crash set drifted")
    if not reclaim["all_exact_committed_state_match"]:
        raise AssertionError("v0.33 reclaim crash exposed ambiguous state")
    if not reclaim["all_live_keys_visible"]:
        raise AssertionError("v0.33 reclaim crash hid current-generation data")
    if not reclaim["all_recovery_scan_free"]:
        raise AssertionError("v0.33 reclaim recovery introduced scan-based repair")

    out = {
        "experiment": "v0.33_generation_boundary_reclamation",
        "semantic_guard": {
            "membership_equal": semantic_guard["membership_equal"],
            "materialization_equal": semantic_guard["materialization_equal"],
            "head_index_equal": semantic_guard["head_index_equal"],
            "all_derived_fresh": semantic_guard["all_derived_fresh"],
            "full_assembly_equal": semantic_guard["full_assembly_equal"],
            "partial_assembly_equal": semantic_guard["partial_assembly_equal"],
        },
        "boundary_control": control,
        "real_generation_cycles": {
            "first_alignment_padding_pages": int(
                cycles["first_trigger"]["generation_alignment_padding_pages"]
            ),
            "first_physical_pages_appended": int(
                cycles["first_trigger"]["physical_pages_appended"]
            ),
            "first_radix_node_pwrites": int(
                cycles["first_trigger"]["radix_node_pwrites"]
            ),
            "first_reclaim_step_count": len(cycles["first_reclaim_steps"]),
            "free_after_first_reclaim": int(cycles["free_after_first_reclaim"]),
            "second_alignment_padding_pages": int(
                cycles["second_trigger"]["generation_alignment_padding_pages"]
            ),
            "second_active_old_last_segment": int(
                cycles["second_active_layout"]["old"]["last_segment"]
            ),
            "second_active_current_first_segment": int(
                cycles["second_active_layout"]["current"]["first_segment"]
            ),
            "second_trigger_physical_pages_appended": int(
                cycles["second_trigger"]["physical_pages_appended"]
            ),
            "second_completion_physical_pages_appended": int(
                cycles["second_completion"]["physical_pages_appended"]
            ),
            "second_reused_free_extents": int(cycles["second_reused_free_extents"]),
            "second_scrub_pwrites": int(cycles["second_scrub_pwrites"]),
            "second_reclaim_step_count": len(cycles["second_reclaim_steps"]),
            "max_reclaim_segments_per_step": int(
                cycles["max_reclaim_segments_per_step"]
            ),
            "max_reclaim_pages_appended": int(cycles["max_reclaim_pages_appended"]),
            "all_34_keys_visible": bool(cycles["all_34_keys_visible"]),
        },
        "insert_crash_matrices": {
            "first_migration": _insert_matrix_summary(first),
            "reuse_migration": _insert_matrix_summary(reuse),
        },
        "reclaim_crash_matrix": {
            "failpoints": list(reclaim["failpoints"]),
            "case_count": int(reclaim["case_count"]),
            "all_exact_committed_state_match": bool(
                reclaim["all_exact_committed_state_match"]
            ),
            "all_live_keys_visible": bool(reclaim["all_live_keys_visible"]),
            "all_recovery_scan_free": bool(reclaim["all_recovery_scan_free"]),
            "clean_trace": {
                key: reclaim["clean_trace"][key]
                for key in (
                    "requested_budget",
                    "reclaimed_segments",
                    "remaining_segments",
                    "free_count",
                    "radix_node_pwrites",
                    "lifecycle_header_preads",
                    "lifecycle_header_pwrites",
                    "physical_pages_appended",
                    "fsyncs",
                    "generation_pages_scanned",
                    "mapping_nodes_scanned",
                    "logical_redo",
                )
            },
        },
        "observe": (
            "v0.32's whole-segment reclamation is not directly composable with the real primary "
            "because back-to-back generation allocation can place the next generation inside the "
            "predecessor's final 16-page mapping segment. Reclaiming by generation would then risk "
            "freeing a physical segment that still contains logical pages reserved for a live generation."
        ),
        "first_principle": (
            "a physical segment may be reclaimed as one ownership unit only when logical-generation "
            "ownership is disjoint at segment granularity. The disjointness mechanism must not require "
            "a capacity-sized ownership walk or an O(K) serialized page manifest."
        ),
        "hypothesis_under_test": (
            "aligning every new logical generation base to the next 16-page mapping boundary will "
            "eliminate mixed-generation segments with at most 15 unused logical page ids per generation. "
            "Because physical segments remain lazily append-mapped, those logical gaps should not create "
            "capacity-scaled physical allocation. The v0.32 owner chain can then be driven directly by "
            "real migration completion and safely reuse reclaimed extents after the fixed 32-page scrub."
        ),
        "prediction": (
            "the unaligned control must share a boundary segment at every tested capacity, while the "
            "aligned candidate must share none and use fewer than 16 padding page ids. Two real migration "
            "cycles must preserve all keys, publish retired ownership without a generation scan, reclaim "
            "with zero appended pages under fixed budgets, and reuse a retired extent only after a fixed "
            "32-page scrub. Process death before committed-superblock publication must expose the exact "
            "pre-state; death after publication must expose the exact post-state; recovery must remain "
            "frontier-derived and scan-free."
        ),
        "result": (
            "survives only if address-space alignment removes the mixed-generation ownership hazard in "
            "the real primary while retaining bounded per-step reclamation and exact process-crash "
            "visibility. The current candidate intentionally supports one retirement backlog at a time "
            "and does not claim hardware power-loss safety, metadata-node pruning, filesystem block "
            "deallocation, or arbitrary multi-writer/distributed correctness."
        ),
    }
    RESULTS_PATH.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    return out


if __name__ == "__main__":
    run()
