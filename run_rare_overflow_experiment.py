from __future__ import annotations

import json
from pathlib import Path

from simulator.normalized_membership import run_v016_normalized_case
from storage.rare_overflow import run_rare_overflow_envelope


ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "rare_overflow_results.json"


def _require_semantic_guard(row: dict) -> None:
    if not row["membership_equal"]:
        raise AssertionError("v0.16 membership drifted before v0.22 experiment")
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

    hybrid = run_rare_overflow_envelope(
        checkpoints=(1_000, 4_000, 16_000, 64_000, 256_000),
        overflow_checkpoints=(1, 16, 64, 256, 1_024, 4_096, 16_384),
        bucket_size=4,
        max_kicks=32,
        stash_capacity=8,
        sample_count=512,
        page_size=4096,
    )

    ordinary = hybrid["ordinary_rows"]
    if any(row["overflow_insertions"] != 0 or row["overflow_rows"] != 0 for row in ordinary):
        raise AssertionError("ordinary workload unexpectedly used rare overflow")
    if any(row["primary_lookup_overflow_checks"] != 0 for row in ordinary):
        raise AssertionError("ordinary primary hit queried rare overflow")
    if any(row["primary_lookup_page_max"] > 2 for row in ordinary):
        raise AssertionError("ordinary primary lookup exceeded v0.20 page envelope")
    if any(
        row["primary_max_mutation_slot_work"]
        > row["primary_theoretical_mutation_slot_work_cap"]
        for row in ordinary
    ):
        raise AssertionError("ordinary primary placement exceeded explicit cap")
    if not all(row["overflow_uses_primary_key"] for row in ordinary):
        raise AssertionError("overflow point lookup is not directly indexed")

    stress = hybrid["overflow_stress_rows"]
    if [row["overflow_rows"] for row in stress] != [1, 16, 64, 256, 1024, 4096, 16384]:
        raise AssertionError("overflow-cardinality sweep drifted")
    if any(not row["all_inserted_found"] for row in stress):
        raise AssertionError("hybrid lost admitted membership")
    if any(row["primary_hit_overflow_checked"] for row in stress):
        raise AssertionError("overflow growth contaminated primary-hit lookup")
    if any(row["overflow_hit_primary_pages"] != 3 for row in stress):
        raise AssertionError("overflow hit changed the saturated primary miss envelope")
    if any(row["missing_primary_pages"] != 3 for row in stress):
        raise AssertionError("missing lookup changed the saturated primary miss envelope")
    if any(
        row["overflow_hit_modeled_pages"]
        != row["overflow_hit_primary_pages"] + row["overflow_hit_btree_height"]
        for row in stress
    ):
        raise AssertionError("overflow-hit page accounting is inconsistent")
    if any(
        row["missing_modeled_pages"]
        != row["missing_primary_pages"] + row["missing_btree_height"]
        for row in stress
    ):
        raise AssertionError("missing-key page accounting is inconsistent")

    heights = [row["overflow_btree_height"] for row in stress]
    pages = [row["overflow_btree_total_pages"] for row in stress]
    if heights != sorted(heights) or heights[-1] <= heights[0]:
        raise AssertionError(f"overflow B-tree depth did not expose growth: {heights}")
    if pages[-1] <= pages[0]:
        raise AssertionError(f"overflow B-tree storage did not grow: {pages}")

    control = hybrid["v021_d8_control"]
    expected_control = {
        "attempted": 256,
        "successes": 128,
        "failures": 128,
        "max_mutation_slot_work": 1600,
        "missing_lookup_page_probes": 24,
        "reserved_capacity_slots": 8192,
    }
    if control != expected_control:
        raise AssertionError(f"v0.21 D=8 control drifted: {control}")

    for row in ordinary:
        print("RARE_OVERFLOW_N", row)
    for row in stress:
        print("RARE_OVERFLOW_STRESS", row)
    print("RARE_OVERFLOW_V021_CONTROL", control)

    out = {
        "experiment": "v0.22_explicit_rare_overflow",
        "semantic_guard": {
            "membership_equal": semantic_guard["membership_equal"],
            "materialization_equal": semantic_guard["materialization_equal"],
            "head_index_equal": semantic_guard["head_index_equal"],
            "all_derived_fresh": semantic_guard["all_derived_fresh"],
            "full_assembly_equal": semantic_guard["full_assembly_equal"],
            "partial_assembly_equal": semantic_guard["partial_assembly_equal"],
        },
        "hybrid": hybrid,
        "observed_overflow_btree_heights": heights,
        "hypothesis_under_test": (
            "a fixed bounded common placement path can guarantee admission in the tested "
            "envelope by routing only exhausted placements to an explicit directly indexed "
            "rare overflow path, without making ordinary primary hits pay overflow lookup cost"
        ),
        "prediction": (
            "ordinary keyed-hash growth should remain entirely on the bounded primary path; "
            "after concentrated primary exhaustion all later keys should remain addressable in "
            "overflow, while primary hits never query overflow. Overflow hits and primary misses "
            "must honestly inherit the overflow comparison-index root-to-leaf depth as overflow "
            "cardinality grows"
        ),
        "result": (
            "survives as an explicit common/exceptional-path separation if ordinary primary "
            "locality remains isolated and every overflowed key is directly addressable. It does "
            "not establish universally constant lookup: overflow hits and missing keys must pay "
            "the growing comparison-index depth once the bounded primary path misses"
        ),
        "revision": (
            "accept logarithmic exceptional lookup as the honest admission cost unless a later "
            "bounded discriminator can prove that common misses need not query overflow. Do not "
            "hide overflow growth inside unbounded domain escalation. Persistence/crash testing "
            "still waits until this hybrid admission contract survives"
        ),
        "measurement_scope": hybrid["measurement_scope"],
    }
    RESULTS_PATH.write_text(json.dumps(out, indent=2))
    print("RARE_OVERFLOW_RESULTS_JSON")
    print(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    run()
