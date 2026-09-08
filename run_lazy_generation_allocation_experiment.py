from __future__ import annotations

import json
from pathlib import Path

from simulator.normalized_membership import run_v016_normalized_case
from storage.lazy_generation_allocation import (
    PAGE_SIZE,
    eager_generation_residue_bytes,
    lazy_bucket_residue_bytes,
    lazy_stash_residue_bytes,
    new_bucket_pages,
    run_lazy_generation_sweep,
)

ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "lazy_generation_allocation_results.json"


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
        raise AssertionError("v0.16 semantic guard failed before v0.28 experiment")


def run() -> dict:
    semantic_guard = run_v016_normalized_case(
        entity_count=128,
        predicate_count=16,
        history_depth=8,
        changed_count=1,
    )
    _require_semantic_guard(semantic_guard)

    sweep = run_lazy_generation_sweep()
    capacities = [32, 128, 512, 2048]
    if sweep["capacities"] != capacities:
        raise AssertionError("v0.28 capacity sweep drifted")
    rows = sweep["rows"]
    if len(rows) != len(capacities):
        raise AssertionError("v0.28 sweep cardinality drifted")

    for row, capacity in zip(rows, capacities):
        buckets = new_bucket_pages(capacity)
        if int(row["new_bucket_pages"]) != capacity // 2:
            raise AssertionError("v0.28 new-generation bucket geometry drifted")
        if int(row["eager_extension_bytes"]) != eager_generation_residue_bytes(capacity):
            raise AssertionError("v0.28 eager control drifted")
        if int(row["lazy_first_bucket_bytes"]) != PAGE_SIZE:
            raise AssertionError("v0.28 first-bucket lazy residue drifted")
        if int(row["lazy_last_bucket_bytes"]) != lazy_bucket_residue_bytes(capacity, buckets - 1):
            raise AssertionError("v0.28 last-bucket lazy residue drifted")
        if int(row["lazy_last_bucket_bytes"]) != PAGE_SIZE * (capacity - 1):
            raise AssertionError("v0.28 linear bucket worst-case formula drifted")
        if int(row["lazy_stash_bytes"]) != lazy_stash_residue_bytes(capacity):
            raise AssertionError("v0.28 stash residue drifted")
        if int(row["lazy_stash_bytes"]) != PAGE_SIZE * (capacity + 1):
            raise AssertionError("v0.28 linear stash formula drifted")
        if int(row["pages_materialized_by_first_write"]) != 1:
            raise AssertionError("v0.28 first lazy mutation no longer materializes one logical page")
        if int(row["sampled_p95_bytes"]) < int(row["lazy_last_bucket_bytes"]) * 0.85:
            raise AssertionError("v0.28 deterministic hash sample did not exercise high direct addresses")
        if int(row["sampled_max_bytes"]) > int(row["lazy_last_bucket_bytes"]):
            raise AssertionError("v0.28 sampled bucket residue exceeded algebraic bucket maximum")

    if [int(row["sampled_p95_bytes"]) for row in rows] != sorted(
        int(row["sampled_p95_bytes"]) for row in rows
    ):
        raise AssertionError("v0.28 sampled p95 residue did not grow with capacity")

    out = {
        "experiment": "v0.28_lazy_generation_allocation",
        "semantic_guard": {
            "membership_equal": semantic_guard["membership_equal"],
            "materialization_equal": semantic_guard["materialization_equal"],
            "head_index_equal": semantic_guard["head_index_equal"],
            "all_derived_fresh": semantic_guard["all_derived_fresh"],
            "full_assembly_equal": semantic_guard["full_assembly_equal"],
            "partial_assembly_equal": semantic_guard["partial_assembly_equal"],
        },
        "sweep": sweep,
        "hypothesis_under_test": (
            "removing eager full-generation ftruncate while keeping the v0.24 arithmetic dual-copy "
            "page addresses is sufficient to bound crash residue by a constant because a first lazy "
            "mutation materializes only one logical page"
        ),
        "prediction": (
            "if the hypothesis is false, first writes to high direct-address buckets will extend the "
            "process-visible file-length frontier by Theta(C) even though exactly one logical page is "
            "materialized; the eager and naive-lazy schemes will therefore differ only by constants"
        ),
        "result": (
            "falsified: one-page lazy materialization does not bound file-length residue under the "
            "existing arithmetic layout. For old capacity C and bucket_size=4, the last new bucket "
            "creates 4096(C-1) bytes beyond the committed frontier and the new stash creates "
            "4096(C+1), while eager extension creates 4096(C+2). All remain Theta(C)."
        ),
        "surviving_claim": (
            "naive lazy allocation can reduce eager reservation but does not asymptotically bound the "
            "stale file-length range. Bounding residue requires changing address placement or adding a "
            "bounded mapping/segmentation layer; merely delaying writes is insufficient."
        ),
        "measurement_scope": sweep["measurement_scope"],
        "non_claims": [
            "file-length range is not filesystem allocated-block usage",
            "one logical page materialized is not one storage-device write",
            "the experiment does not measure sparse-file allocation, filesystem journaling, latency, or power-loss hardware behavior",
            "no extent-map or segmented-address replacement is established by v0.28",
        ],
    }
    RESULTS_PATH.write_text(json.dumps(out, indent=2))
    print("LAZY_GENERATION_ALLOCATION_RESULTS_JSON")
    print(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    run()
