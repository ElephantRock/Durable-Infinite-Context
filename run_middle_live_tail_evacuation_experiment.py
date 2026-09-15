from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from simulator.middle_live_tail_evacuation import (
    run_middle_live_tail_evacuation_experiment,
)

RESULTS_PATH = Path(__file__).resolve().parent / "middle_live_tail_evacuation_results.json"
DIAGNOSTIC_PATH = Path(__file__).resolve().parent / "middle_live_tail_evacuation_diagnostic.json"


def _queue_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "queue_count": int(row["queue_count"]),
        "queue_pages": [int(item["descriptor_page"]) for item in row["descriptors"]],
        "queue_incarnations": [
            int(item["descriptor_incarnation"]) for item in row["descriptors"]
        ],
        "free_count": int(row["descriptor_free_count"]),
        "free_pages": [int(item["descriptor_page"]) for item in row["free_descriptors"]],
        "free_incarnations": [
            int(item["descriptor_incarnation"]) for item in row["free_descriptors"]
        ],
        "head_page": row.get("head_page"),
        "head_incarnation": row.get("head_incarnation"),
        "tail_page": row.get("tail_page"),
        "tail_incarnation": row.get("tail_incarnation"),
        "tail_predecessor_page": row.get("tail_predecessor_page"),
        "tail_predecessor_incarnation": row.get("tail_predecessor_incarnation"),
        "free_head_page": row.get("descriptor_free_head_page"),
        "free_head_incarnation": row.get("descriptor_free_head_incarnation"),
        "arena_pages": int(row["descriptor_arena_pages"]),
        "physical_tail_page": row.get("physical_tail_page"),
        "physical_tail_incarnation": row.get("physical_tail_incarnation"),
        "physical_tail_predecessor_page": row.get("physical_tail_predecessor_page"),
        "physical_tail_predecessor_incarnation": row.get(
            "physical_tail_predecessor_incarnation"
        ),
    }


def _arena_summary(row: dict[str, Any]) -> dict[str, int]:
    return {
        "arena_file_bytes": int(row["arena_file_bytes"]),
        "committed_arena_bytes": int(row["committed_arena_bytes"]),
        "uncommitted_arena_tail_bytes": int(row["uncommitted_arena_tail_bytes"]),
    }


def _recovery_summary(row: dict[str, Any]) -> dict[str, int]:
    return {
        "logical_work": int(row["logical_work"]),
        "generation_pages_scanned": int(row["generation_pages_scanned"]),
        "mapping_nodes_scanned": int(row["mapping_nodes_scanned"]),
        "retirement_descriptors_scanned": int(row.get("retirement_descriptors_scanned", 0)),
        "physical_truncated_bytes": int(row["physical_truncated_bytes"]),
        "retirement_descriptor_arena_truncated_bytes": int(
            row["retirement_descriptor_arena_truncated_bytes"]
        ),
    }


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
                "arena_before_recovery": _arena_summary(row["arena_before_recovery"]),
                "first_recovery": _recovery_summary(row["first_recovery"]),
                "second_recovery": _recovery_summary(row["second_recovery"]),
                "exact_committed_state_match": bool(row["exact_committed_state_match"]),
                "recovery_scan_free": bool(row["recovery_scan_free"]),
                "second_recovery_idempotent": bool(row["second_recovery_idempotent"]),
            }
            for row in matrix["cases"]
        ],
    }


def _canonicalize(full: dict[str, Any]) -> dict[str, Any]:
    control = full["v041_control"]
    candidate = full["middle_tail_evacuation"]
    authority = full["physical_tail_authority_crashes"]

    relocation_crashes = _crash_summary(full["relocation_crash_matrix"])
    authority_crashes = _crash_summary(authority)
    authority_crashes.update(
        {
            "trigger_key": authority["trigger_key"],
            "trigger_index": int(authority["trigger_index"]),
            "clean_trace": dict(authority["clean_trace"]),
            "pre_queue": _queue_summary(authority["pre_state"]["queue"]),
            "post_queue": _queue_summary(authority["clean_post_state"]["queue"]),
        }
    )

    return {
        "experiment": full["experiment"],
        "survived": bool(full["survived"]),
        "claim_boundary": dict(full["claim_boundary"]),
        "v041_control": {
            "trigger_key": control["trigger_key"],
            "queue_before": _queue_summary(control["queue_before"]),
            "arena_before": _arena_summary(control["arena_before"]),
            "trace": dict(control["trace"]),
            "queue_after": _queue_summary(control["queue_after"]),
            "arena_after": _arena_summary(control["arena_after"]),
            "exact_state_unchanged": bool(control["exact_state_unchanged"]),
        },
        "middle_tail_evacuation": {
            "trigger_key": candidate["trigger_key"],
            "queue_before": _queue_summary(candidate["queue_before"]),
            "arena_before": _arena_summary(candidate["arena_before"]),
            "trace": dict(candidate["trace"]),
            "queue_after": _queue_summary(candidate["queue_after"]),
            "arena_after": _arena_summary(candidate["arena_after"]),
            "released_arena_pages": int(candidate["released_arena_pages"]),
            "released_arena_bytes": int(candidate["released_arena_bytes"]),
            "stale_tail_rejected": bool(candidate["stale_tail_rejected"]),
            "stale_destination_rejected": bool(candidate["stale_destination_rejected"]),
            "successor_identity_preserved": bool(candidate["successor_identity_preserved"]),
            "relocated_payload_preserved": bool(candidate["relocated_payload_preserved"]),
        },
        "relocation_crash_matrix": relocation_crashes,
        "physical_tail_authority_crashes": authority_crashes,
    }


def run() -> dict:
    result = _canonicalize(run_middle_live_tail_evacuation_experiment())
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
                {
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        raise


if __name__ == "__main__":
    main()
