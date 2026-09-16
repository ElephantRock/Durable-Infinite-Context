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
        "free": [int(item["descriptor_page"]) for item in row["free_descriptors"]],
        "arena_pages": int(row["descriptor_arena_pages"]),
        "head": row.get("head_page"),
        "tail": row.get("tail_page"),
        "tail_predecessor": row.get("tail_predecessor_page"),
        "physical_tail": row.get("physical_tail_page"),
        "physical_tail_predecessor": row.get("physical_tail_predecessor_page"),
        "physical_tail_predecessor_predecessor": row.get(
            "physical_tail_predecessor_predecessor_page"
        ),
    }


def _work(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "released": bool(row["released"]),
        "preads": int(row["retirement_descriptor_preads"]),
        "pwrites": int(row["retirement_descriptor_pwrites"]),
        "scans": int(row["retirement_descriptors_scanned"]),
        "relocations": int(row["live_descriptor_relocations"]),
        "arena_before": int(row["retirement_arena_pages_before"]),
        "arena_after": int(row["retirement_arena_pages_after"]),
        "bytes_released": int(row["retirement_arena_bytes_released"]),
    }


def _crashes(matrix: dict[str, Any]) -> dict[str, Any]:
    return {
        "count": int(matrix["case_count"]),
        "failpoints": list(matrix["failpoints"]),
        "expected_states": [row["expected_state"] for row in matrix["cases"]],
        "arena_truncated_bytes": [
            int(row["first_recovery"]["retirement_descriptor_arena_truncated_bytes"])
            for row in matrix["cases"]
        ],
        "primary_truncated_bytes": [
            int(row["first_recovery"]["physical_truncated_bytes"])
            for row in matrix["cases"]
        ],
        "all_exact": bool(matrix["all_exact_committed_state_match"]),
        "all_scan_free": bool(matrix["all_recovery_scan_free"]),
        "all_second_idempotent": bool(matrix["all_second_recovery_idempotent"]),
    }


def _authority(matrix: dict[str, Any]) -> dict[str, Any]:
    row = _crashes(matrix)
    row.update(
        {
            "mode": matrix["mode"],
            "trigger_key": matrix["trigger_key"],
            "pre": _topology(matrix["pre_queue"]),
            "post": _topology(matrix["post_queue"]),
        }
    )
    return row


def _canonicalize(full: dict[str, Any]) -> dict[str, Any]:
    control = full["v042_control"]
    candidate = full["deep_tail_evacuation"]
    trace = candidate["trace"]
    refusal = full["deeper_prefix_refusal"]
    return {
        "experiment": full["experiment"],
        "survived": bool(full["survived"]),
        "claim_boundary": dict(full["claim_boundary"]),
        "v042_control": {
            "before": _topology(control["queue_before"]),
            "after": _topology(control["queue_after"]),
            "work": _work(control["trace"]),
            "unchanged": bool(control["exact_state_unchanged"]),
        },
        "candidate": {
            "before": _topology(candidate["queue_before"]),
            "after": _topology(candidate["queue_after"]),
            "work": _work(trace),
            "identity": {
                "physical_tail": [trace["physical_tail_page"], trace["physical_tail_incarnation"]],
                "predecessor": [trace["predecessor_page"], trace["predecessor_incarnation"]],
                "predecessor_predecessor": [
                    trace["predecessor_predecessor_page"],
                    trace["predecessor_predecessor_incarnation"],
                ],
                "successor": [trace["successor_page"], trace["successor_incarnation"]],
                "destination_old": [
                    trace["destination_page"],
                    trace["destination_old_incarnation"],
                ],
                "destination_new": [
                    trace["destination_page"],
                    trace["destination_new_incarnation"],
                ],
                "new_physical_tail": [
                    trace["new_physical_tail_page"],
                    trace["new_physical_tail_incarnation"],
                ],
                "new_physical_tail_predecessor": [
                    trace["new_physical_tail_predecessor_page"],
                    trace["new_physical_tail_predecessor_incarnation"],
                ],
            },
            "stale_tail_rejected": bool(candidate["stale_tail_rejected"]),
            "stale_destination_rejected": bool(candidate["stale_destination_rejected"]),
            "successor_identity_preserved": bool(candidate["successor_identity_preserved"]),
            "payload_preserved": bool(candidate["relocated_payload_preserved"]),
        },
        "relocation_crashes": _crashes(full["relocation_crash_matrix"]),
        "append_authority_crashes": _authority(full["append_authority_crashes"]),
        "reuse_authority_crashes": _authority(full["reuse_authority_crashes"]),
        "deeper_prefix_refusal": {
            "before": _topology(refusal["queue_before"]),
            "after": _topology(refusal["queue_after"]),
            "work": _work(refusal["trace"]),
            "unchanged": bool(refusal["exact_state_unchanged"]),
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
            json.dumps({"error_type": type(exc).__name__, "error": str(exc)}, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        raise


if __name__ == "__main__":
    main()
