from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import run_bounded_placement_experiment as experiment


ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "bounded_placement_results.json"


def require_equal(label: str, expected: Any, observed: Any) -> None:
    if expected != observed:
        raise AssertionError(f"{label}: expected {expected!r}, observed {observed!r}")


def require_result_invariants(observed: dict[str, Any]) -> None:
    guard = observed["semantic_guard"]
    for key, value in guard.items():
        require_equal(f"semantic_guard.{key}", True, value)

    placement = observed["placement"]
    require_equal("checkpoints", [1000, 4000, 16000, 64000, 256000], placement["checkpoints"])
    require_equal("bucket size", 4, placement["bucket_size"])
    require_equal("max kicks", 32, placement["max_kicks"])
    require_equal("stash capacity", 8, placement["stash_capacity"])
    require_equal("collision widths", [8, 16, 17, 32, 64, 128], placement["collision_widths"])

    ordinary = placement["ordinary_rows"]
    theoretical = 2 * placement["bucket_size"] + placement["max_kicks"] * placement["bucket_size"] + 2 * placement["max_kicks"]
    require_equal("theoretical mutation-work cap", 200, theoretical)

    for row in ordinary:
        require_equal(f"ordinary failures N={row['membership_rows']}", 0, row["cuckoo_insert_failures"])
        require_equal(f"ordinary stash N={row['membership_rows']}", 0, row["cuckoo_stash_peak"])
        require_equal(
            f"ordinary theoretical cap N={row['membership_rows']}",
            theoretical,
            row["cuckoo_theoretical_max_mutation_slot_work"],
        )
        if row["cuckoo_max_mutation_slot_work"] > theoretical:
            raise AssertionError(f"ordinary mutation work exceeded cap: {row}")
        if row["cuckoo_lookup_page_max"] > 2:
            raise AssertionError(f"ordinary lookup exceeded two candidate bucket pages: {row}")
        if row["cuckoo_lookup_slot_max"] > 2 * placement["bucket_size"]:
            raise AssertionError(f"ordinary lookup inspected more than two buckets: {row}")

    linear_maxima = [row["linear_max_insert_slot_probes"] for row in ordinary]
    require_equal("recorded linear maxima", linear_maxima, observed["observed_linear_maxima"])
    require_equal("fixed ordinary linear maxima", [11, 19, 21, 31, 34], linear_maxima)
    if linear_maxima[-1] <= linear_maxima[0]:
        raise AssertionError("linear-probe maximum tail disappeared")

    stress = {row["colliding_keys"]: row for row in placement["collision_stress_rows"]}
    for width in placement["collision_widths"]:
        row = stress[width]
        require_equal(f"linear collision chain width={width}", width, row["linear_max_insert_slot_probes"])
        require_equal(f"stress theoretical cap width={width}", theoretical, row["cuckoo_theoretical_max_mutation_slot_work"])
        if row["cuckoo_max_mutation_slot_work"] > theoretical:
            raise AssertionError(f"stress mutation work exceeded cap: {row}")
        require_equal(
            f"prior admitted keys preserved width={width}",
            row["cuckoo_successes"],
            row["cuckoo_found_keys_after_failures"],
        )

    require_equal("8-key concentrated success", (8, 0, 0), (stress[8]["cuckoo_successes"], stress[8]["cuckoo_failures"], stress[8]["cuckoo_stash_size"]))
    require_equal("16-key concentrated capacity", (16, 0, 8), (stress[16]["cuckoo_successes"], stress[16]["cuckoo_failures"], stress[16]["cuckoo_stash_size"]))
    require_equal("17th concentrated key fails", (16, 1, 8), (stress[17]["cuckoo_successes"], stress[17]["cuckoo_failures"], stress[17]["cuckoo_stash_size"]))

    for width in (17, 32, 64, 128):
        require_equal(f"finite concentrated success capacity width={width}", 16, stress[width]["cuckoo_successes"])
        require_equal(f"stress reaches explicit work cap width={width}", theoretical, stress[width]["cuckoo_max_mutation_slot_work"])

    require_equal("stress failures at 32", 16, stress[32]["cuckoo_failures"])
    require_equal("stress failures at 64", 48, stress[64]["cuckoo_failures"])
    require_equal("stress failures at 128", 112, stress[128]["cuckoo_failures"])


def main() -> None:
    recorded_text = RESULTS_PATH.read_text()
    recorded = json.loads(recorded_text)
    try:
        observed = experiment.run()
        require_result_invariants(observed)
        require_equal("committed v0.20 evidence", recorded, observed)
        print("RECORDED_BOUNDED_PLACEMENT_RESULTS_MATCH_EXECUTABLE_EXPERIMENT")
    finally:
        RESULTS_PATH.write_text(recorded_text)


if __name__ == "__main__":
    main()
