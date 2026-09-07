from __future__ import annotations

import json
from pathlib import Path

from simulator.normalized_membership import run_v016_normalized_case
from storage.bounded_placement import run_bounded_placement_envelope


ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "bounded_placement_results.json"


def _require_semantic_guard(row: dict) -> None:
    if not row["membership_equal"]:
        raise AssertionError("v0.16 membership drifted before v0.20 experiment")
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

    placement = run_bounded_placement_envelope(
        checkpoints=(1_000, 4_000, 16_000, 64_000, 256_000),
        bucket_size=4,
        max_kicks=32,
        stash_capacity=8,
        slots_per_page=64,
        sample_count=512,
        collision_widths=(8, 16, 17, 32, 64, 128),
    )

    ordinary = placement["ordinary_rows"]
    theoretical_bounds = {
        row["cuckoo_theoretical_max_mutation_slot_work"] for row in ordinary
    }
    if theoretical_bounds != {200}:
        raise AssertionError(f"unexpected v0.20 mutation bound: {theoretical_bounds}")
    if any(row["cuckoo_insert_failures"] != 0 for row in ordinary):
        raise AssertionError("bounded cuckoo candidate failed ordinary growth fixture")
    if any(row["cuckoo_stash_peak"] != 0 for row in ordinary):
        raise AssertionError("ordinary growth unexpectedly consumed the finite stash")
    if any(
        row["cuckoo_max_mutation_slot_work"]
        > row["cuckoo_theoretical_max_mutation_slot_work"]
        for row in ordinary
    ):
        raise AssertionError("ordinary placement exceeded explicit mutation bound")
    if any(row["cuckoo_lookup_page_max"] > 2 for row in ordinary):
        raise AssertionError("ordinary cuckoo lookup exceeded two bucket pages")

    linear_maxima = [row["linear_max_insert_slot_probes"] for row in ordinary]
    if linear_maxima[-1] <= linear_maxima[0]:
        raise AssertionError(f"linear-probe maximum did not expose a growing tail: {linear_maxima}")

    stress = {row["colliding_keys"]: row for row in placement["collision_stress_rows"]}
    for width, row in stress.items():
        if row["linear_max_insert_slot_probes"] != width:
            raise AssertionError(f"collision control at width {width} did not form a full probe chain")
        if row["cuckoo_max_mutation_slot_work"] > row["cuckoo_theoretical_max_mutation_slot_work"]:
            raise AssertionError(f"bounded placement exceeded explicit cap at width {width}")
        if row["cuckoo_found_keys_after_failures"] != row["cuckoo_successes"]:
            raise AssertionError(f"failed insertion corrupted prior successful keys at width {width}")

    if stress[16]["cuckoo_failures"] != 0:
        raise AssertionError("two buckets plus stash should admit the first sixteen stress keys")
    if stress[17]["cuckoo_failures"] != 1:
        raise AssertionError("seventeenth concentrated key should expose finite-domain failure")
    if not all(stress[width]["cuckoo_successes"] == 16 for width in (17, 32, 64, 128)):
        raise AssertionError("collision-domain success capacity changed unexpectedly")
    if not all(stress[width]["cuckoo_max_mutation_slot_work"] == 200 for width in (17, 32, 64, 128)):
        raise AssertionError("stress failure did not hit the explicit rollback work bound")

    for row in ordinary:
        print("BOUNDED_PLACEMENT_N", row)
    for row in placement["collision_stress_rows"]:
        print("BOUNDED_PLACEMENT_STRESS", row)

    out = {
        "experiment": "v0.20_bounded_placement_locality",
        "semantic_guard": {
            "membership_equal": semantic_guard["membership_equal"],
            "materialization_equal": semantic_guard["materialization_equal"],
            "head_index_equal": semantic_guard["head_index_equal"],
            "all_derived_fresh": semantic_guard["all_derived_fresh"],
            "full_assembly_equal": semantic_guard["full_assembly_equal"],
            "partial_assembly_equal": semantic_guard["partial_assembly_equal"],
        },
        "placement": placement,
        "observed_linear_maxima": linear_maxima,
        "hypothesis_under_test": (
            "a two-choice bucketized cuckoo placement primitive with finite kick and stash "
            "budgets can replace v0.19 linear probing with an explicit N-independent "
            "mutation-work cap while retaining shallow lookup; concentrated collisions must "
            "fail explicitly rather than extending an unbounded probe chain"
        ),
        "prediction": (
            "ordinary keyed-hash growth must complete without insertion failures inside the "
            "fixed load envelope, every mutation must remain at or below the explicit slot-work "
            "bound, lookup must touch at most two candidate bucket pages when the stash is empty, "
            "and controlled pair collisions must convert excess demand into bounded insertion "
            "failure without corrupting already admitted keys"
        ),
        "result": (
            "survives as a bounded-work placement contract if the ordinary fixture remains "
            "failure-free and collision stress never exceeds the explicit cap. It is not "
            "sufficient as a durable placement layer if concentrated collisions can exhaust "
            "the finite bucket-plus-stash domain; that availability gap becomes the next target"
        ),
        "revision": (
            "bounded placement is stronger than an unbounded linear-probe tail, but explicit "
            "failure is not yet a complete durable-index policy. The next experiment must give "
            "failed local placement a bounded escape path without recreating unbounded lookup, "
            "global rehash, or hidden overflow scans"
        ),
        "measurement_scope": placement["measurement_scope"],
    }
    RESULTS_PATH.write_text(json.dumps(out, indent=2))
    print("BOUNDED_PLACEMENT_RESULTS_JSON")
    print(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    run()
