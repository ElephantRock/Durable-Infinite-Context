from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from simulator.deep_middle_live_tail_evacuation import (
    run_deep_middle_live_tail_evacuation_experiment,
)

RESULTS_PATH = Path(__file__).resolve().parent / "deep_middle_live_tail_evacuation_results.json"
DIAGNOSTIC_PATH = Path(__file__).resolve().parent / "deep_middle_live_tail_evacuation_diagnostic.json"


def _topology(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "queue": [int(item["descriptor_page"]) for item in row["descriptors"]],
        "queue_incarnations": [
            int(item["descriptor_incarnation"]) for item in row["descriptors"]
        ],
        "free": [int(item["descriptor_page"]) for item in row["free_descriptors"]],
        "free_incarnations": [
            int(item["descriptor_incarnation"]) for item in row["free_descriptors"]
        ],
        "arena_pages": int(row["descriptor_arena_pages"]),
        "head": row.get("head_page"),
        "head_incarnation": row.get("head_incarnation"),
        "tail": row.get("tail_page"),
        "tail_incarnation": row.get("tail_incarnation"),
        "tail_predecessor": row.get("tail_predecessor_page"),
        "tail_predecessor_incarnation": row.get("tail_predecessor_incarnation"),
        "free_head": row.get("descriptor_free_head_page"),
        "free_head_incarnation": row.get("descriptor_free_head_incarnation"),
        "physical_tail": row.get("physical_tail_page"),
        "physical_tail_incarnation": row.get("physical_tail_incarnation"),
        "physical_tail_predecessor": row.get("physical_tail_predecessor_page"),
        "physical_tail_predecessor_incarnation": row.get(
            "physical_tail_predecessor_incarnation"
        ),
        "physical_tail_predecessor_predecessor": row.get(
            "physical_tail_predecessor_predecessor_page"
        ),
        "physical_tail_predecessor_predecessor_incarnation": row.get(
            "physical_tail_predecessor_predecessor_incarnation"
        ),
    }


def _compact_trace(row: dict[str, Any]) -> dict[str, Any]:
    names = (
        "released",
        "retirement_arena_pages_before",
        "retirement_arena_pages_after",
        "retirement_arena_pages_released",
        "retirement_arena_bytes_released",
        "retirement_descriptor_free_count_before",
        "retirement_descriptor_free_count_after",
        "retirement_descriptor_preads",
        "retirement_descriptor_pwrites",
        "retirement_descriptors_scanned",
        "live_descriptor_relocations",
        "physical_tail_page",
        "physical_tail_incarnation",
        "predecessor_page",
        "predecessor_incarnation",
        "predecessor_predecessor_page",
        "predecessor_predecessor_incarnation",
        "successor_page",
        "successor_incarnation",
        "destination_page",
        "destination_old_incarnation",
        "destination_new_incarnation",
        "new_physical_tail_page",
        "new_physical_tail_incarnation",
        "new_physical_tail_predecessor_page",
        "new_physical_tail_predecessor_incarnation",
        "retirement_arena_fsyncs",
    )
    return {name: row[name] for name in names if name in row}


def _crash_summary(matrix: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_count": int(matrix["case_count"]),
        "failpoints": list(matrix["failpoints"]),
        "all_exact_committed_state_match": bool(
            matrix["all_exact_committed_state_match"]
        ),
        "all_recovery_scan_free": bool(matrix["all_recovery_scan_free"]),
        "all_second_recovery_idempotent": bool(
            matrix["all_second_recovery_idempotent"]
        ),
        "cases": [
            {
                "failpoint": row["failpoint"],
                "expected_state": row["expected_state"],
                "arena_file_bytes_before_recovery": int(
                    row["arena_before_recovery"]["arena_file_bytes"]
                ),
                "committed_arena_bytes_before_recovery": int(
                    row["arena_before_recovery"]["committed_arena_bytes"]
                ),
                "first_primary_truncated_bytes": int(
                    row["first_recovery"]["physical_truncated_bytes"]
                ),
                "first_arena_truncated_bytes": int(
                    row["first_recovery"][
                        "retirement_descriptor_arena_truncated_bytes"
                    ]
                ),
                "exact": bool(row["exact_committed_state_match"]),
                "scan_free": bool(row["recovery_scan_free"]),
                "second_idempotent": bool(row["second_recovery_idempotent"]),
            }
            for row in matrix["cases"]
        ],
    }


def _authority_summary(matrix: dict[str, Any]) -> dict[str, Any]:
    row = _crash_summary(matrix)
    row.update(
        {
            "mode": matrix["mode"],
            "trigger_key": matrix["trigger_key"],
            "trigger_index": int(matrix["trigger_index"]),
            "pre_topology": _topology(matrix["pre_queue"]),
            "post_topology": _topology(matrix["post_queue"]),
            "clean_trace": dict(matrix["clean_trace"]),
        }
    )
    return row


def _canonicalize(full: dict[str, Any]) -> dict[str, Any]:
    control = full["v042_control"]
    candidate = full["deep_tail_evacuation"]
    refusal = full["deeper_prefix_refusal"]
    return {
        "experiment": full["experiment"],
        "survived": bool(full["survived"]),
        "claim_boundary": dict(full["claim_boundary"]),
        "v042_control": {
            "trigger_key": control["trigger_key"],
            "before": _topology(control["queue_before"]),
            "after": _topology(control["queue_after"]),
            "trace": _compact_trace(control["trace"]),
            "exact_state_unchanged": bool(control["exact_state_unchanged"]),
        },
        "deep_tail_evacuation": {
            "trigger_key": candidate["trigger_key"],
            "before": _topology(candidate["queue_before"]),
            "after": _topology(candidate["queue_after"]),
            "trace": _compact_trace(candidate["trace"]),
            "released_arena_pages": int(candidate["released_arena_pages"]),
            "released_arena_bytes": int(candidate["released_arena_bytes"]),
            "stale_tail_rejected": bool(candidate["stale_tail_rejected"]),
            "stale_destination_rejected": bool(
                candidate["stale_destination_rejected"]
            ),
            "successor_identity_preserved": bool(
                candidate["successor_identity_preserved"]
            ),
            "relocated_payload_preserved": bool(
                candidate["relocated_payload_preserved"]
            ),
        },
        "relocation_crash_matrix": _crash_summary(
            full["relocation_crash_matrix"]
        ),
        "append_authority_crashes": _authority_summary(
            full["append_authority_crashes"]
        ),
        "reuse_authority_crashes": _authority_summary(
            full["reuse_authority_crashes"]
        ),
        "deeper_prefix_refusal": {
            "trigger_key": refusal["trigger_key"],
            "trigger_index": int(refusal["trigger_index"]),
            "before": _topology(refusal["queue_before"]),
            "after": _topology(refusal["queue_after"]),
            "trace": _compact_trace(refusal["trace"]),
            "exact_state_unchanged": bool(refusal["exact_state_unchanged"]),
        },
    }


def run() -> dict:
    result = _canonicalize(run_deep_middle_live_tail_evacuation_experiment())
    RESULTS_PATH.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    try:
        run()
    except BaseException as exc:
        DIAGNOSTIC_PATH.write_text(
            json.dumps(
                {"error_type": type(exc).__name__, "error": str(exc)},
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        raise


if __name__ == "__main__":
    main()
