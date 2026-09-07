from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import run_hash_resize_experiment as experiment


ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "hash_resize_results.json"


def require_equal(label: str, expected: Any, observed: Any) -> None:
    if expected != observed:
        raise AssertionError(f"{label}: expected {expected!r}, observed {observed!r}")


def require_result_invariants(observed: dict[str, Any]) -> None:
    for key, value in observed["semantic_guard"].items():
        require_equal(f"semantic_guard.{key}", True, value)

    envelope = observed["hash_envelope"]
    require_equal("slots per page", 64, envelope["slots_per_page"])
    require_equal("max load", 0.5, envelope["max_load"])
    rows = envelope["rows"]
    require_equal("row count", len(envelope["checkpoints"]), len(rows))

    if any(row["lookup_page_p95"] > 2 for row in rows):
        raise AssertionError("lookup p95 escaped the tested page envelope")
    if any(row["lookup_page_max"] > 4 for row in rows):
        raise AssertionError("lookup max escaped the tested page envelope")

    spikes = [row["largest_single_resize_rows"] for row in rows]
    if spikes != sorted(spikes) or spikes[-1] <= spikes[0]:
        raise AssertionError(f"resize spike did not grow: {spikes}")

    for event in envelope["resize_events"]:
        require_equal(
            f"resize at {event['table_size_before']} rehashes live rows",
            event["table_size_before"],
            event["rehashed_rows"],
        )
        require_equal(
            f"resize at {event['table_size_before']} doubles capacity",
            event["capacity_before"] * 2,
            event["capacity_after"],
        )


def main() -> None:
    recorded_text = RESULTS_PATH.read_text()
    recorded = json.loads(recorded_text)
    try:
        observed = experiment.run()
        require_result_invariants(observed)
        require_equal("committed v0.18 evidence", recorded, observed)
        print("RECORDED_HASH_RESIZE_RESULTS_MATCH_EXECUTABLE_EXPERIMENT")
    finally:
        RESULTS_PATH.write_text(recorded_text)


if __name__ == "__main__":
    main()
