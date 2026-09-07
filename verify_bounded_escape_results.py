from __future__ import annotations

import json
from pathlib import Path

from run_bounded_escape_experiment import run


ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "bounded_escape_results.json"


def main() -> None:
    recorded_text = RESULTS_PATH.read_text()
    recorded = json.loads(recorded_text)
    try:
        actual = run()

        guard = actual["semantic_guard"]
        if not all(guard.values()):
            raise AssertionError(f"semantic guard failed: {guard}")

        ordinary = actual["escape"]["ordinary_rows"]
        if [row["linear_max_insert_slot_probes"] for row in ordinary] != [11, 19, 21, 31, 34]:
            raise AssertionError("v0.20 linear control drifted")
        if any(row["escape_insert_failures"] != 0 for row in ordinary):
            raise AssertionError("ordinary bounded escape fixture has insertion failures")
        if any(row["escape_max_domains_attempted"] != 1 for row in ordinary):
            raise AssertionError("ordinary fixture used an escape domain")
        if any(row["escape_stash_entries"] != 0 for row in ordinary):
            raise AssertionError("ordinary fixture consumed finite stash")
        if any(row["escape_lookup_domain_max"] != 1 for row in ordinary):
            raise AssertionError("ordinary lookup escaped the first domain")
        if any(row["escape_lookup_page_max"] > 2 for row in ordinary):
            raise AssertionError("ordinary lookup exceeded two page probes")
        if any(
            row["escape_max_mutation_slot_work"]
            > row["escape_theoretical_mutation_slot_work_cap"]
            for row in ordinary
        ):
            raise AssertionError("ordinary mutation exceeded explicit cap")

        stress = actual["escape"]["collision_stress_rows"]
        if [row["domain_count"] for row in stress] != [1, 2, 4, 8]:
            raise AssertionError("domain-count stress sweep drifted")
        for row in stress:
            d = row["domain_count"]
            if row["concentrated_capacity"] != 16 * d:
                raise AssertionError(f"concentrated capacity drifted at D={d}")
            if row["successes"] != 16 * d or row["first_failure_at"] != 16 * d + 1:
                raise AssertionError(f"failure threshold drifted at D={d}")
            if row["max_mutation_slot_work"] != 200 * d:
                raise AssertionError(f"mutation cap drifted at D={d}")
            if row["missing_lookup_page_probes"] != 3 * d:
                raise AssertionError(f"missing lookup fan-out drifted at D={d}")
            if row["reserved_capacity_amplification_vs_single_domain"] != d:
                raise AssertionError(f"reserved space amplification drifted at D={d}")
            if not row["all_admitted_found_after_failures"]:
                raise AssertionError(f"failed escalation corrupted prior admissions at D={d}")

        if actual != recorded:
            raise AssertionError("committed v0.21 evidence differs from executable experiment")

        print("RECORDED_BOUNDED_ESCAPE_RESULTS_MATCH_EXECUTABLE_EXPERIMENT")
    finally:
        RESULTS_PATH.write_text(recorded_text)


if __name__ == "__main__":
    main()
