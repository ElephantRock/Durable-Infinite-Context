from __future__ import annotations

import json
from pathlib import Path

from simulator.normalized_membership import run_v016_normalized_case
from storage.page_locality import run_fixed_shard_geometry


ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "page_locality_results.json"


def _require_semantic_guard(row: dict) -> None:
    if not row["membership_equal"]:
        raise AssertionError("v0.16 membership drifted before page-locality experiment")
    if not row["materialization_equal"] or not row["all_derived_fresh"]:
        raise AssertionError("v0.16 semantic guard failed clean-rebuild parity")
    if not row["head_index_equal"]:
        raise AssertionError("v0.16 semantic guard lost current-head parity")
    if not row["full_assembly_equal"] or not row["partial_assembly_equal"]:
        raise AssertionError("v0.16 semantic guard changed logical profile semantics")


def run() -> dict:
    semantic_guard = run_v016_normalized_case(
        entity_count=128,
        predicate_count=16,
        history_depth=8,
        changed_count=1,
    )
    _require_semantic_guard(semantic_guard)

    geometry = run_fixed_shard_geometry(
        checkpoints=(1_000, 10_000, 50_000, 250_000, 1_000_000),
        shard_count=64,
        page_size=4096,
    )
    rows = geometry["rows"]
    global_heights = [row["global_height"] for row in rows]
    sharded_heights = [row["max_shard_height"] for row in rows]

    if global_heights != sorted(global_heights):
        raise AssertionError(f"global B-tree height decreased: {global_heights}")
    if sharded_heights != sorted(sharded_heights):
        raise AssertionError(f"fixed-shard B-tree height decreased: {sharded_heights}")
    if max(global_heights) == min(global_heights):
        raise AssertionError("global B-tree page path did not expose N-dependence")
    if max(sharded_heights) == min(sharded_heights):
        raise AssertionError(
            "fixed sharding did not reach a large enough N to test its asymptotic failure"
        )
    if not any(
        row["max_shard_height"] < row["global_height"]
        for row in rows
    ):
        raise AssertionError("fixed sharding did not delay any B-tree height transition")
    if rows[-1]["max_shard_height"] <= rows[0]["max_shard_height"]:
        raise AssertionError("fixed-shard lookup page bound falsely appeared constant")

    for row in rows:
        if row["global_cold_index_pages"] != row["global_height"]:
            raise AssertionError("global page-path accounting is inconsistent")
        if row["target_shard_cold_index_pages"] != row["target_shard_height"]:
            raise AssertionError("sharded page-path accounting is inconsistent")
        print("PAGE_LOCALITY_N", row)

    out = {
        "experiment": "v0.17_btree_page_locality",
        "semantic_guard": {
            "membership_equal": semantic_guard["membership_equal"],
            "materialization_equal": semantic_guard["materialization_equal"],
            "head_index_equal": semantic_guard["head_index_equal"],
            "all_derived_fresh": semantic_guard["all_derived_fresh"],
            "full_assembly_equal": semantic_guard["full_assembly_equal"],
            "partial_assembly_equal": semantic_guard["partial_assembly_equal"],
        },
        "page_geometry": geometry,
        "hypothesis_under_test": (
            "a fixed number of hash-partitioned B-tree membership indexes can make "
            "cold point-lookup index-page traversal independent of global cardinality N"
        ),
        "prediction": (
            "if fixed partitioning is sufficient, max root-to-leaf shard height must remain "
            "constant as N grows while exact point lookup remains index-backed"
        ),
        "result": (
            "falsified: 64-way partitioning delays the height transition but the maximum "
            "shard B-tree height still grows at sufficiently large N"
        ),
        "derived_asymptotic": (
            "for any fixed shard count S, each shard still contains Theta(N/S) rows, so a "
            "comparison B-tree lookup remains Theta(log_B(N/S)) = Theta(log_B N) page levels"
        ),
        "revision": (
            "strict global-N-independent page locality is not a valid claim for the current "
            "single global B-tree or for any fixed finite B-tree partition count; fixed sharding "
            "only shifts page-height thresholds by a constant factor"
        ),
        "engineering_consequence": (
            "do not merge fixed B-tree sharding as a purported asymptotic fix. Either accept "
            "logarithmic addressability as the durable-memory lookup contract or test a growing "
            "direct-address/hash structure whose bucket/page occupancy is itself kept bounded"
        ),
        "measurement_scope": geometry["measurement_scope"],
    }
    RESULTS_PATH.write_text(json.dumps(out, indent=2))
    print("PAGE_LOCALITY_RESULTS_JSON")
    print(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    run()
