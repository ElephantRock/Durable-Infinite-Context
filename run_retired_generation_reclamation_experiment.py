from __future__ import annotations

import json
from pathlib import Path

from simulator.normalized_membership import run_v016_normalized_case
from simulator.retired_generation_reclamation import (
    BACKLOG_COUNTS,
    BUDGET,
    RECLAIM_FAILPOINTS,
    REUSE_FAILPOINTS,
    run_budget_sweep,
    run_manifest_and_capacity_controls,
    run_reclaim_crash_matrix,
    run_reuse_crash_matrix,
    run_stale_payload_control,
)
from storage.reclaiming_radix_primary import SEGMENT_DATA_PAGES

ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "retired_generation_reclamation_results.json"


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
        raise AssertionError("v0.16 semantic guard failed before v0.32 experiment")


def run() -> dict:
    semantic_guard = run_v016_normalized_case(
        entity_count=128,
        predicate_count=16,
        history_depth=8,
        changed_count=1,
    )
    _require_semantic_guard(semantic_guard)

    controls = run_manifest_and_capacity_controls()
    budget = run_budget_sweep()
    stale = run_stale_payload_control()
    reclaim_crash = run_reclaim_crash_matrix()
    reuse_crash = run_reuse_crash_matrix()

    if budget["backlog_counts"] != list(BACKLOG_COUNTS):
        raise AssertionError("v0.32 retirement backlog sweep drifted")
    if int(budget["budget"]) != BUDGET:
        raise AssertionError("v0.32 cleanup budget drifted")
    if int(budget["global_max_reclaimed_per_step"]) > BUDGET:
        raise AssertionError("v0.32 reclaim exceeded fixed per-step budget")
    if int(budget["global_max_lifecycle_preads_per_step"]) > 4 * BUDGET:
        raise AssertionError("v0.32 lifecycle reads grew beyond fixed per-step budget")
    if int(budget["global_max_lifecycle_pwrites_per_step"]) > BUDGET:
        raise AssertionError("v0.32 lifecycle writes grew beyond fixed per-step budget")
    if int(budget["global_max_radix_pwrites_per_step"]) > BUDGET:
        raise AssertionError("v0.32 radix writes grew beyond fixed per-step budget")
    if int(budget["global_max_physical_pages_appended_per_step"]) != 0:
        raise AssertionError("v0.32 reclaim appended storage")

    manifest_rows = controls["manifest_rows"]
    if not all(
        int(manifest_rows[index]["flat_manifest_bytes"])
        < int(manifest_rows[index + 1]["flat_manifest_bytes"])
        for index in range(len(manifest_rows) - 1)
    ):
        raise AssertionError("v0.32 flat manifest control did not scale with backlog")

    if not bool(stale["unsafe_stale_payload_visible"]):
        raise AssertionError("v0.32 unsafe reuse control did not expose stale payload")
    if bool(stale["safe_stale_payload_visible"]):
        raise AssertionError("v0.32 scrubbed reuse still exposed stale payload")
    if bool(stale["safe_old_mapping_visible"]):
        raise AssertionError("v0.32 scrubbed reuse resurrected retired mapping")
    if int(stale["safe_reuse_trace"]["data_page_scrub_pwrites"]) != SEGMENT_DATA_PAGES:
        raise AssertionError("v0.32 scrub work is not the fixed segment footprint")
    if int(stale["safe_reuse_trace"]["physical_pages_appended"]) != 0:
        raise AssertionError("v0.32 shared-path reuse appended storage")

    if reclaim_crash["failpoints"] != list(RECLAIM_FAILPOINTS):
        raise AssertionError("v0.32 reclaim crash failpoint set drifted")
    if not reclaim_crash["all_exact_committed_state_match"]:
        raise AssertionError("v0.32 reclaim crash exposed ambiguous committed state")
    if not reclaim_crash["all_recovery_scan_free"]:
        raise AssertionError("v0.32 reclaim recovery introduced scan/redo work")
    if not reclaim_crash["all_second_recovery_idempotent"]:
        raise AssertionError("v0.32 reclaim recovery did not converge idempotently")

    if reuse_crash["failpoints"] != list(REUSE_FAILPOINTS):
        raise AssertionError("v0.32 reuse crash failpoint set drifted")
    if not reuse_crash["all_exact_committed_state_match"]:
        raise AssertionError("v0.32 reuse crash exposed ambiguous committed state")
    if not reuse_crash["all_recovery_scan_free"]:
        raise AssertionError("v0.32 reuse recovery introduced scan/redo work")
    if not reuse_crash["all_second_recovery_idempotent"]:
        raise AssertionError("v0.32 reuse recovery did not converge idempotently")
    if not reuse_crash["all_retry_or_committed_reuse_exact"]:
        raise AssertionError("v0.32 reuse did not retry exactly after pre-commit death")

    for row in budget["rows"]:
        print(
            "RECLAIM_BUDGET",
            row["retired_segments"],
            {
                "steps": row["step_count"],
                "max_reclaimed": row["max_reclaimed_per_step"],
                "max_lifecycle_preads": row["max_lifecycle_preads_per_step"],
                "max_radix_pwrites": row["max_radix_pwrites_per_step"],
                "max_appended_pages": row["max_physical_pages_appended_per_step"],
            },
        )
    print(
        "REUSE_STALE_CONTROL",
        {
            "unsafe_visible": stale["unsafe_stale_payload_visible"],
            "safe_visible": stale["safe_stale_payload_visible"],
            "scrub_pwrites": stale["safe_reuse_trace"]["data_page_scrub_pwrites"],
            "appended_pages": stale["safe_reuse_trace"]["physical_pages_appended"],
        },
    )
    for row in reclaim_crash["rows"]:
        print(
            "RECLAIM_CRASH",
            row["failpoint"],
            {
                "committed": row["expected_committed"],
                "exact": row["exact_committed_state_match"],
            },
        )
    for row in reuse_crash["rows"]:
        print(
            "REUSE_CRASH",
            row["failpoint"],
            {
                "committed": row["expected_committed"],
                "exact": row["exact_committed_state_match"],
                "retry_or_committed": row["retry_or_committed_reuse_exact"],
            },
        )

    compact_budget_rows = [
        {
            "retired_segments": int(row["retired_segments"]),
            "step_count": int(row["step_count"]),
            "max_reclaimed_per_step": int(row["max_reclaimed_per_step"]),
            "max_lifecycle_preads_per_step": int(row["max_lifecycle_preads_per_step"]),
            "max_lifecycle_pwrites_per_step": int(row["max_lifecycle_pwrites_per_step"]),
            "max_radix_pwrites_per_step": int(row["max_radix_pwrites_per_step"]),
            "max_physical_pages_appended_per_step": int(
                row["max_physical_pages_appended_per_step"]
            ),
            "final_free_count": int(row["final_free_count"]),
            "materialize_physical_pages_appended": int(
                row["materialize"]["physical_pages_appended"]
            ),
        }
        for row in budget["rows"]
    ]

    out = {
        "experiment": "v0.32_retired_generation_reclamation",
        "semantic_guard": {
            "membership_equal": semantic_guard["membership_equal"],
            "materialization_equal": semantic_guard["materialization_equal"],
            "head_index_equal": semantic_guard["head_index_equal"],
            "all_derived_fresh": semantic_guard["all_derived_fresh"],
            "full_assembly_equal": semantic_guard["full_assembly_equal"],
            "partial_assembly_equal": semantic_guard["partial_assembly_equal"],
        },
        "controls": controls,
        "budget_sweep": {
            "budget": int(budget["budget"]),
            "backlog_counts": list(budget["backlog_counts"]),
            "global_max_reclaimed_per_step": int(
                budget["global_max_reclaimed_per_step"]
            ),
            "global_max_lifecycle_preads_per_step": int(
                budget["global_max_lifecycle_preads_per_step"]
            ),
            "global_max_lifecycle_pwrites_per_step": int(
                budget["global_max_lifecycle_pwrites_per_step"]
            ),
            "global_max_radix_pwrites_per_step": int(
                budget["global_max_radix_pwrites_per_step"]
            ),
            "global_max_physical_pages_appended_per_step": int(
                budget["global_max_physical_pages_appended_per_step"]
            ),
            "rows": compact_budget_rows,
        },
        "stale_payload_control": {
            "old_segment_id": int(stale["old_segment_id"]),
            "new_segment_id": int(stale["new_segment_id"]),
            "unsafe_reused_same_extent": int(stale["unsafe_old_segment_base_page"])
            == int(stale["unsafe_reused_segment_base_page"]),
            "unsafe_stale_payload_visible": bool(stale["unsafe_stale_payload_visible"]),
            "safe_reused_same_extent": int(stale["safe_old_segment_base_page"])
            == int(stale["safe_reused_segment_base_page"]),
            "safe_stale_payload_visible": bool(stale["safe_stale_payload_visible"]),
            "safe_old_mapping_visible": bool(stale["safe_old_mapping_visible"]),
            "safe_data_page_scrub_pwrites": int(
                stale["safe_reuse_trace"]["data_page_scrub_pwrites"]
            ),
            "safe_radix_node_pwrites": int(
                stale["safe_reuse_trace"]["radix_node_pwrites"]
            ),
            "safe_physical_pages_appended": int(
                stale["safe_reuse_trace"]["physical_pages_appended"]
            ),
            "safe_fsyncs": int(stale["safe_reuse_trace"]["fsyncs"]),
        },
        "reclaim_crash_matrix": {
            "failpoints": list(reclaim_crash["failpoints"]),
            "case_count": len(reclaim_crash["rows"]),
            "clean_reclaimed_segments": int(
                reclaim_crash["clean_trace"]["reclaimed_segments"]
            ),
            "clean_physical_pages_appended": int(
                reclaim_crash["clean_trace"]["physical_pages_appended"]
            ),
            "all_exact_committed_state_match": bool(
                reclaim_crash["all_exact_committed_state_match"]
            ),
            "all_recovery_scan_free": bool(reclaim_crash["all_recovery_scan_free"]),
            "all_second_recovery_idempotent": bool(
                reclaim_crash["all_second_recovery_idempotent"]
            ),
        },
        "reuse_crash_matrix": {
            "failpoints": list(reuse_crash["failpoints"]),
            "case_count": len(reuse_crash["rows"]),
            "clean_reused_same_extent": int(
                reuse_crash["clean_reuse_trace"]["reused_segment_base_page"]
            )
            == int(reuse_crash["retired_segment_base_page"]),
            "clean_data_page_scrub_pwrites": int(
                reuse_crash["clean_reuse_trace"]["data_page_scrub_pwrites"]
            ),
            "clean_radix_node_pwrites": int(
                reuse_crash["clean_reuse_trace"]["radix_node_pwrites"]
            ),
            "clean_physical_pages_appended": int(
                reuse_crash["clean_reuse_trace"]["physical_pages_appended"]
            ),
            "all_exact_committed_state_match": bool(
                reuse_crash["all_exact_committed_state_match"]
            ),
            "all_recovery_scan_free": bool(reuse_crash["all_recovery_scan_free"]),
            "all_second_recovery_idempotent": bool(
                reuse_crash["all_second_recovery_idempotent"]
            ),
            "all_retry_or_committed_reuse_exact": bool(
                reuse_crash["all_retry_or_committed_reuse_exact"]
            ),
        },
        "observe": (
            "v0.31 bounds radix-node representation, but committed physical segments remain reachable "
            "until lifecycle cleanup removes their mappings. A flat retired-segment manifest grows with "
            "retired materialization count, while a generation-capacity walk grows with logical capacity. "
            "A second hazard appears when a reclaimed extent is reused: valid old data-page records can "
            "become visible through a different logical mapping unless reuse invalidates the fixed data footprint."
        ),
        "diagnosis": (
            "retirement locality and reuse safety are distinct invariants. Intrusive ownership links can "
            "make cleanup proportional to an explicit fixed budget rather than generation capacity, but "
            "mapping-only reuse is insufficient because data-page validity is independent of the radix edge."
        ),
        "first_principle": (
            "retired implementation history must become unreachable before its physical extent is reused; "
            "foreground cleanup work must be bounded by an explicit budget rather than total history or "
            "generation capacity; and every byte that can become semantically visible under a new owner "
            "must either carry ownership/version identity or be invalidated before publication."
        ),
        "hypothesis_under_test": (
            "dual-copy intrusive lifecycle headers plus a committed retirement cursor can reclaim at most B "
            "segments per step with no capacity/map scan, while a fixed 32-page scrub before reuse can prevent "
            "retired payload resurrection without allocating a new data extent."
        ),
        "prediction": (
            "with B=3, reclaim work must remain at most three ownership visits, twelve lifecycle reads, three "
            "lifecycle writes, three leaf rewrites, zero appended pages, and two publication barriers for every "
            "tested backlog size. The unscrubbed control must expose a seeded valid retired record after reuse; "
            "the scrubbed candidate must expose none, reuse the same physical segment, and survive SIGKILL around "
            "unlink/free and scrub/remap/publication with exact committed lifecycle state and scan-free recovery."
        ),
        "result": (
            "survives only if cleanup locality is independent of backlog/capacity per step, stale-payload reuse is "
            "eliminated by fixed-footprint invalidation, and all reclaim/reuse process-crash cases preserve a single "
            "committed interpretation. This does not claim constant total cleanup work, physical block deallocation, "
            "metadata-node pruning, automatic alignment with every primary generation boundary, hardware power-loss "
            "safety, or arbitrary multi-writer/distributed correctness."
        ),
    }
    RESULTS_PATH.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    return out


if __name__ == "__main__":
    run()
