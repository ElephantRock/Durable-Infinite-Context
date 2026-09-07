from __future__ import annotations

import unittest

from storage.bounded_escape import (
    BoundedDomainEscalationIndex,
    run_bounded_escape_envelope,
)


class BoundedEscapeTests(unittest.TestCase):
    def test_single_domain_contract_is_v020_bound(self) -> None:
        index = BoundedDomainEscalationIndex(1024, domain_count=1)
        self.assertEqual(index.per_domain_mutation_slot_work_cap, 200)
        self.assertEqual(index.theoretical_total_mutation_slot_work_cap, 200)
        self.assertEqual(index.theoretical_lookup_page_cap, 3)

    def test_two_collision_domains_shift_failure_threshold_but_keep_finite_cap(self) -> None:
        index = BoundedDomainEscalationIndex(
            1024,
            domain_count=2,
            force_same_pair=True,
        )
        admitted: list[str] = []
        traces = []
        for number in range(64):
            key = f"collision_{number:03d}"
            trace = index.insert(key)
            traces.append(trace)
            if trace.success:
                admitted.append(key)

        self.assertEqual(len(admitted), 32)
        self.assertTrue(all(trace.success for trace in traces[:32]))
        self.assertFalse(traces[32].success)
        self.assertEqual(traces[32].domains_attempted, 2)
        self.assertEqual(traces[32].total_mutation_slot_work, 400)
        self.assertLessEqual(
            max(trace.total_mutation_slot_work for trace in traces),
            index.theoretical_total_mutation_slot_work_cap,
        )
        self.assertTrue(all(index.lookup(key).found for key in admitted))

    def test_ordinary_envelope_uses_first_domain_without_failure(self) -> None:
        result = run_bounded_escape_envelope(
            checkpoints=(1000,),
            ordinary_domain_count=4,
            stress_domain_counts=(1, 2),
            sample_count=128,
        )
        ordinary = result["ordinary_rows"][0]
        self.assertEqual(ordinary["escape_insert_failures"], 0)
        self.assertEqual(ordinary["escape_max_domains_attempted"], 1)
        self.assertEqual(
            ordinary["reserved_capacity_amplification_vs_single_domain"], 4
        )
        self.assertLessEqual(ordinary["escape_lookup_page_max"], 2)
        self.assertLessEqual(
            ordinary["escape_max_mutation_slot_work"],
            ordinary["escape_theoretical_mutation_slot_work_cap"],
        )

        stress = {row["domain_count"]: row for row in result["collision_stress_rows"]}
        self.assertEqual(stress[1]["concentrated_capacity"], 16)
        self.assertEqual(stress[1]["first_failure_at"], 17)
        self.assertEqual(stress[2]["concentrated_capacity"], 32)
        self.assertEqual(stress[2]["first_failure_at"], 33)
        self.assertTrue(stress[2]["all_admitted_found_after_failures"])


if __name__ == "__main__":
    unittest.main()
