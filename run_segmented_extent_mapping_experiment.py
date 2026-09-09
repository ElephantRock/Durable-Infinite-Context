from __future__ import annotations

import json
from pathlib import Path

from simulator.normalized_membership import run_v016_normalized_case
from storage.segmented_extent_mapping import (
    PAGE_SIZE,
    RADIX_LEVELS,
    SEGMENT_BUCKET_PAGES,
    flat_descriptor_residue_bytes,
    logical_segments,
    persistence_ordering_experiment,
    run_segmented_extent_sweep,
    sparse_radix_allocation_tail_bytes,
)

ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "segmented_extent_mapping_results.json"


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
        raise AssertionError("v0.16 semantic guard failed before v0.29 experiment")


def build_results() -> dict:
    semantic_guard = run_v016_normalized_case(
        entity_count=128,
        predicate_count=16,
        history_depth=8,
        changed_count=1,
    )
    _require_semantic_guard(semantic_guard)

    sweep = run_segmented_extent_sweep()
    capacities = [1024, 16384, 262144, 4194304]
    if sweep["capacities"] != capacities:
        raise AssertionError("v0.29 capacity sweep drifted")
    if int(sweep["page_size"]) != PAGE_SIZE:
        raise AssertionError("v0.29 page size drifted")
    if int(sweep["segment_bucket_pages"]) != SEGMENT_BUCKET_PAGES:
        raise AssertionError("v0.29 segment geometry drifted")
    if int(sweep["radix_levels"]) != RADIX_LEVELS:
        raise AssertionError("v0.29 radix depth drifted")

    rows = sweep["rows"]
    if len(rows) != len(capacities):
        raise AssertionError("v0.29 sweep cardinality drifted")
    bounded_tail = None
    for row, capacity in zip(rows, capacities):
        segments = logical_segments(capacity)
        last_segment = segments - 1
        if int(row["logical_segments"]) != segments:
            raise AssertionError("v0.29 logical segment count drifted")
        if int(row["flat_last_descriptor_residue_bytes"]) != flat_descriptor_residue_bytes(last_segment):
            raise AssertionError("v0.29 flat descriptor control drifted")
        expected_tail = sparse_radix_allocation_tail_bytes(last_segment)
        if int(row["radix_sparse_allocation_tail_bytes"]) != expected_tail:
            raise AssertionError("v0.29 bounded segment tail formula drifted")
        if bounded_tail is None:
            bounded_tail = expected_tail
        elif expected_tail != bounded_tail:
            raise AssertionError("v0.29 radix sparse allocation tail grew with capacity")
        if int(row["radix_sparse_new_descriptor_nodes"]) != RADIX_LEVELS - 1:
            raise AssertionError("v0.29 fresh radix path node count drifted")
        if int(row["radix_total_physical_preads"]) != 20:
            raise AssertionError("v0.29 lookup fan-out drifted")
        if int(row["radix_metadata_pwrites_new_path"]) != RADIX_LEVELS:
            raise AssertionError("v0.29 metadata write bound drifted")
        if int(row["radix_fsyncs"]) != 2:
            raise AssertionError("v0.29 publication barrier count drifted")
        if int(row["flat_sampled_max_bytes"]) > int(row["flat_last_descriptor_residue_bytes"]):
            raise AssertionError("v0.29 sampled flat descriptor residue exceeded algebraic maximum")

    flat_last = [int(row["flat_last_descriptor_residue_bytes"]) for row in rows]
    if flat_last != sorted(flat_last) or flat_last[-1] <= flat_last[0]:
        raise AssertionError("v0.29 flat sparse descriptor control did not grow with capacity")
    if len({int(row["radix_sparse_allocation_tail_bytes"]) for row in rows}) != 1:
        raise AssertionError("v0.29 radix allocation tail is not capacity-independent")
    dense_radix = [int(row["dense_radix_physical_descriptor_pages"]) for row in rows]
    if dense_radix != sorted(dense_radix) or dense_radix[-1] <= dense_radix[0]:
        raise AssertionError("v0.29 failed to expose dense descriptor growth")

    persistence = persistence_ordering_experiment()
    if int(persistence["safe_invalid_case_count"]) != 0:
        raise AssertionError("v0.29 dependency-before-commit protocol is crash-unsafe in the fault model")
    if int(persistence["control_invalid_case_count"]) <= 0:
        raise AssertionError("v0.29 one-barrier control failed to produce a persistence-ordering counterexample")

    return {
        "experiment": "v0.29_segmented_extent_mapping",
        "semantic_guard": {
            "membership_equal": semantic_guard["membership_equal"],
            "materialization_equal": semantic_guard["materialization_equal"],
            "head_index_equal": semantic_guard["head_index_equal"],
            "all_derived_fresh": semantic_guard["all_derived_fresh"],
            "full_assembly_equal": semantic_guard["full_assembly_equal"],
            "partial_assembly_equal": semantic_guard["partial_assembly_equal"],
        },
        "sweep": sweep,
        "persistence_ordering": persistence,
        "hypothesis_under_test": (
            "a bounded physical segment plus append-allocated fixed-depth radix extent map can decouple a "
            "high logical bucket identity from a capacity-scaled physical offset without reintroducing "
            "capacity-dependent sparse tail residue, lookup fan-out, or per-allocation metadata writes"
        ),
        "prediction": (
            "the packed flat-descriptor control will retain Theta(C) sparse file-length span for high logical "
            "segment ids, while the segmented radix candidate will hold first-allocation tail span, lookup "
            "preads, metadata pwrites, and publication barriers constant across the capacity sweep; dense "
            "descriptor storage should still grow with the number of materialized segments K"
        ),
        "result": (
            "survived the modeled capacity-scaling falsification: sparse high-id allocation is bounded at "
            "188416 bytes (46 pages), lookup at 20 modeled user-space preads, fresh-path metadata at 8 "
            "pwrites plus one superblock pwrite, and two fsync barriers across all tested capacities. The "
            "packed flat descriptor grows with capacity. The safe dependency-before-commit ordering has zero "
            "invalid recovery states in the enumerated fault model, while the one-barrier control has explicit "
            "committed-pointer-before-dependency counterexamples."
        ),
        "surviving_claim": (
            "within a fixed 64-bit logical-segment namespace and the stated persistence model, append-local "
            "segmentation plus a fixed-depth dual-copy radix extent map removes the v0.28 capacity-scaled "
            "address-span mechanism for a first sparse high-id write. This earns a bounded-address candidate, "
            "not a production replacement: total descriptor storage still grows with materialized segments."
        ),
        "measurement_scope": sweep["measurement_scope"],
        "non_claims": [
            "the fixed eight-level radix is not a proof of mathematically unbounded logical identifiers; extending beyond 64-bit segment ids would require another mechanism or more depth",
            "dense descriptor storage is not constant; it grows with the number of materialized segments K",
            "modeled user-space pread/pwrite counts are not filesystem or storage-device I/O counts",
            "the persistence-ordering model is not a hardware power-loss, torn-sector, drive-cache, or filesystem-journal experiment",
            "the experiment does not integrate the extent map into the v0.24/v0.25 primary or prove end-to-end production latency, throughput, reclamation, or concurrency behavior",
        ],
    }


def run() -> dict:
    out = build_results()
    RESULTS_PATH.write_text(json.dumps(out, indent=2))
    print("SEGMENTED_EXTENT_MAPPING_RESULTS_JSON")
    print(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    run()
