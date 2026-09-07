from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import run_page_locality_experiment as experiment


ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "page_locality_results.json"


def require_equal(label: str, expected: Any, observed: Any) -> None:
    if expected != observed:
        raise AssertionError(f"{label}: expected {expected!r}, observed {observed!r}")


def require_result_invariants(observed: dict[str, Any]) -> None:
    guard = observed["semantic_guard"]
    for key, value in guard.items():
        require_equal(f"semantic_guard.{key}", True, value)

    geometry = observed["page_geometry"]
    require_equal("page size", 4096, geometry["page_size"])
    require_equal("fixed shard count", 64, geometry["shard_count"])
    rows = geometry["rows"]
    require_equal("row count", len(geometry["checkpoints"]), len(rows))

    global_heights = [row["global_height"] for row in rows]
    shard_heights = [row["max_shard_height"] for row in rows]
    if global_heights != sorted(global_heights):
        raise AssertionError(f"global height decreased: {global_heights}")
    if shard_heights != sorted(shard_heights):
        raise AssertionError(f"shard height decreased: {shard_heights}")
    if max(global_heights) == min(global_heights):
        raise AssertionError("global B-tree height did not grow")
    if max(shard_heights) == min(shard_heights):
        raise AssertionError("fixed-shard B-tree height did not grow")
    if not any(
        row["max_shard_height"] < row["global_height"]
        for row in rows
    ):
        raise AssertionError("fixed sharding never delayed a B-tree height transition")

    for row in rows:
        require_equal(
            f"N={row['membership_rows']}.global page path",
            row["global_height"],
            row["global_cold_index_pages"],
        )
        require_equal(
            f"N={row['membership_rows']}.target shard page path",
            row["target_shard_height"],
            row["target_shard_cold_index_pages"],
        )


def main() -> None:
    recorded_text = RESULTS_PATH.read_text()
    recorded = json.loads(recorded_text)
    try:
        observed = experiment.run()
        require_result_invariants(observed)
        require_equal("committed v0.17 evidence", recorded, observed)
        print("RECORDED_PAGE_LOCALITY_RESULTS_MATCH_EXECUTABLE_EXPERIMENT")
    finally:
        RESULTS_PATH.write_text(recorded_text)


if __name__ == "__main__":
    main()
