from __future__ import annotations

import json
from pathlib import Path

from simulator.normalized_membership import run_v016_normalized_case
from storage.bounded_escape import run_bounded_escape_envelope


ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "bounded_escape_results.json"


def _require_semantic_guard(row: dict) -> None:
    if not row["membership_equal"]:
        raise AssertionError("v0.16 membership drifted before v0.21 experiment")
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

    escape = run_bounded_escape_envelope(
        checkpoints=(1_000, 4_000, 16_000, 64_000, 256_000),
        ordinary_domain_count=4,
        stress_domain_counts=(1, 2, 4, 8),
        bucket_size=4,
        max_kicks=32,
        stash_capacity=8,
        slots_per_page=64,
        sample_count=512,
    )

    ordinary = escape["ordinary_rows"]
    if any(row["escape_insert_failures"] != 0 for row in ordinary):
        raise AssertionError("bounded escape failed ordinary growth fixture")
    if any(row["escape_max_domains_attempted"] != 1 for row in ordinary):
        raise AssertionError("ordinary fixture unexpectedly required an escape domain")
    if any(row["escape_stash_entries"] != 0 for row in ordinary):
        raise AssertionError("ordinary fixture unexpectedly consumed a stash")
    if any(row["escape_lookup_domain_max"] != 1 for row in ordinary):
        raise AssertionError("ordinary lookup unexpectedly escalated across domains")
    if any(row["escape_lookup_page_max"] > 2 for row in ordinary):
        raise AssertionError("ordinary lookup exceeded first-domain two-page envelope")
    if any(
        row["escape_max_mutation_slot_work"]
        > row["escape_theoretical_mutation_slot_work_cap"]
        for row in ordinary
    ):
        raise AssertionError("ordinary escape insertion exceeded explicit cap")

    linear_maxima = [row["linear_max_insert_slot_probes"] for row in ordinary]
    if linear_maxima != [11, 19, 21, 31, 34]:
        raise AssertionError(f"v0.20 linear control drifted: {linear_maxima}")

    stress = escape["collision_stress_rows"]
    for row in stress:
        d = row["domain_count"]
        expected_capacity = 16 * d
        if row["concentrated_capacity"] != expected_capacity:
            raise AssertionError(f"unexpected concentrated capacity at D={d}: {row}")
        if row["successes"] != expected_capacity:
            raise AssertionError(f"escape did not shift success threshold at D={d}: {row}")
        if row["first_failure_at"] != expected_capacity + 1:
            raise AssertionError(f"unexpected first failure threshold at D={d}: {row}")
        if row["failures"] != expected_capacity:
            raise AssertionError(f"stress width did not expose finite-domain failure at D={d}")
        if row["max_mutation_slot_work"] != 200 * d:
            raise AssertionError(f"stress failure did not hit explicit total cap at D={d}")
        if row["theoretical_mutation_slot_work_cap"] != 200 * d:
            raise AssertionError(f"theoretical mutation cap drifted at D={d}")
        if row["missing_lookup_domains_checked"] != d:
            raise AssertionError(f"missing lookup did not check all fixed domains at D={d}")
        if row["missing_lookup_page_probes"] != 3 * d:
            raise AssertionError(f"missing lookup page fan-out drifted at D={d}")
        if row["theoretical_lookup_page_cap"] != 3 * d:
            raise AssertionError(f"theoretical lookup cap drifted at D={d}")
        if row["reserved_capacity_amplification_vs_single_domain"] != d:
            raise AssertionError(f"reserved-space amplification drifted at D={d}")
        if not row["all_admitted_found_after_failures"]:
            raise AssertionError(f"failed escalation corrupted admitted keys at D={d}")

    for row in ordinary:
        print("BOUNDED_ESCAPE_N", row)
    for row in stress:
        print("BOUNDED_ESCAPE_STRESS", row)

    out = {
        "experiment": "v0.21_bounded_placement_escape",
        "semantic_guard": {
            "membership_equal": semantic_guard["membership_equal"],
            "materialization_equal": semantic_guard["materialization_equal"],
            "head_index_equal": semantic_guard["head_index_equal"],
            "all_derived_fresh": semantic_guard["all_derived_fresh"],
            "full_assembly_equal": semantic_guard["full_assembly_equal"],
            "partial_assembly_equal": semantic_guard["partial_assembly_equal"],
        },
        "escape": escape,
        "observed_linear_maxima": linear_maxima,
        "hypothesis_under_test": (
            "a fixed finite sequence of independent bounded placement domains can provide a "
            "bounded escape path after local v0.20 placement failure while retaining explicit "
            "mutation and lookup bounds and avoiding global rehash or overflow scans"
        ),
        "prediction": (
            "ordinary keyed-hash growth should remain on the first domain; under concentrated "
            "collisions D domains should admit 16D keys and first fail at 16D+1, while total "
            "mutation work remains at or below 200D, worst missing-key lookup touches at most "
            "3D logical pages, and reserved capacity amplifies by D"
        ),
        "result": (
            "a fixed finite domain sequence survives as a bounded escalation mechanism if the "
            "predicted linear trade-off holds. It cannot establish unlimited adversarial "
            "admission: for every fixed D the concentrated capacity remains finite at 16D. "
            "Increasing D shifts the failure threshold only by paying proportional mutation, "
            "lookup, and reserved-space bounds"
        ),
        "revision": (
            "do not grow the number of bounded domains without limit, because that would erase "
            "the fixed locality contract. The next candidate should make exceptional overflow "
            "explicit, likely via a hybrid rare-path index whose logarithmic cost is isolated "
            "from the bounded common path rather than hidden behind a false zero-failure claim"
        ),
        "measurement_scope": escape["measurement_scope"],
    }
    RESULTS_PATH.write_text(json.dumps(out, indent=2))
    print("BOUNDED_ESCAPE_RESULTS_JSON")
    print(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    run()
