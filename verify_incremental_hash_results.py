from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import run_incremental_hash_experiment as experiment


ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "incremental_hash_results.json"


def require_equal(label: str, expected: Any, observed: Any) -> None:
    if expected != observed:
        raise AssertionError(f"{label}: expected {expected!r}, observed {observed!r}")


def require_result_invariants(observed: dict[str, Any]) -> None:
    guard = observed["semantic_guard"]
    for key, value in guard.items():
        require_equal(f"semantic_guard.{key}", True, value)

    control = observed["stop_the_world_control"]
    candidate = observed["incremental_candidate"]
    require_equal("checkpoints", control["checkpoints"], candidate["checkpoints"])
    require_equal("migration budget", 8, candidate["migration_slot_budget"])

    control_largest = [row["largest_single_resize_rows"] for row in control["rows"]]
    if control_largest != sorted(control_largest) or control_largest[-1] <= control_largest[0]:
        raise AssertionError(f"control resize spike did not grow: {control_largest}")

    budget = candidate["migration_slot_budget"]
    require_equal(
        "global source scan bound",
        budget,
        candidate["global_max_source_slots_scanned_per_insert"],
    )
    if candidate["global_max_rows_copied_per_insert"] > budget:
        raise AssertionError("copied rows exceeded fixed source-slot budget")
    require_equal(
        "capacity amplification",
        1.5,
        candidate["global_max_capacity_amplification_vs_current"],
    )

    events = candidate["migration_events"]
    if not events:
        raise AssertionError("no migration events recorded")
    for event in events:
        if event["completion_live_size"] is None:
            raise AssertionError(f"migration failed to complete: {event}")
        require_equal(
            f"migration {event['migration_id']} deterministic scan span",
            event["source_capacity_slots"] // budget,
            event["insertions_spanned"],
        )

    snapshots = candidate["migration_snapshots"]
    require_equal("migration snapshot count", len(events), len(snapshots))
    for snapshot in snapshots:
        if snapshot["lookup_generations_max"] > 2:
            raise AssertionError(f"lookup exceeded two generations: {snapshot}")
        if snapshot["lookup_page_max"] > 3:
            raise AssertionError(f"fixed migration lookup page envelope changed: {snapshot}")
        require_equal(
            f"migration {snapshot['migration_id']} temporary capacity amplification",
            1.5,
            snapshot["capacity_amplification_vs_current"],
        )

    require_equal(
        "total row copies preserved",
        control["rows"][-1]["cumulative_rehashed_rows"],
        candidate["cumulative_rows_copied"],
    )
    require_equal(
        "source slots scanned",
        sum(event["source_capacity_slots"] for event in events),
        candidate["cumulative_source_slots_scanned"],
    )

    mutation_maxima = [
        row["interval_max_mutation_slot_work"] for row in candidate["rows"]
    ]
    require_equal(
        "recorded mutation-slot maxima",
        mutation_maxima,
        observed["observed_mutation_slot_work_maxima"],
    )
    if mutation_maxima[-1] <= mutation_maxima[0]:
        raise AssertionError(
            "destination linear-probe caveat disappeared; re-evaluate v0.19 conclusion"
        )


def main() -> None:
    recorded_text = RESULTS_PATH.read_text()
    recorded = json.loads(recorded_text)
    try:
        observed = experiment.run()
        require_result_invariants(observed)
        require_equal("committed v0.19 evidence", recorded, observed)
        print("RECORDED_INCREMENTAL_HASH_RESULTS_MATCH_EXECUTABLE_EXPERIMENT")
    finally:
        RESULTS_PATH.write_text(recorded_text)


if __name__ == "__main__":
    main()
