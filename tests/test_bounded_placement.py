from __future__ import annotations

import unittest

from storage.bounded_placement import (
    BoundedBucketCuckooIndex,
    FixedLinearProbeIndex,
    run_bounded_placement_envelope,
)


class BoundedPlacementTests(unittest.TestCase):
    def test_ordinary_growth_preserves_membership_with_explicit_work_bound(self) -> None:
        index = BoundedBucketCuckooIndex(16_384)
        maximum = 0
        for position in range(5_000):
            key = f"entity_{position:09d}|deadline"
            trace = index.insert(key)
            self.assertTrue(trace.success)
            maximum = max(maximum, trace.mutation_slot_work)

        self.assertEqual(0, index.stash_size)
        self.assertLessEqual(maximum, index.theoretical_max_mutation_slot_work)
        for position in range(5_000):
            self.assertTrue(index.lookup(f"entity_{position:09d}|deadline").found)

    def test_collision_domain_fails_explicitly_after_finite_bucket_and_stash(self) -> None:
        index = BoundedBucketCuckooIndex(
            2_048,
            bucket_pair_override=lambda _key: (0, 1),
        )
        traces = [index.insert(f"stress_{position:04d}") for position in range(17)]

        self.assertTrue(all(trace.success for trace in traces[:16]))
        self.assertFalse(traces[16].success)
        self.assertEqual(8, index.stash_size)
        self.assertEqual(index.theoretical_max_mutation_slot_work, traces[16].mutation_slot_work)
        self.assertEqual(16, sum(index.lookup(f"stress_{position:04d}").found for position in range(17)))

    def test_linear_collision_control_retains_unbounded_probe_chain(self) -> None:
        index = FixedLinearProbeIndex(2_048, start_slot_override=lambda _key: 0)
        probes = [index.insert(f"stress_{position:04d}").slot_probes for position in range(64)]
        self.assertEqual(1, probes[0])
        self.assertEqual(64, probes[-1])

    def test_fixed_envelope_exposes_bounded_work_availability_tradeoff(self) -> None:
        result = run_bounded_placement_envelope(
            checkpoints=(1_000, 4_000),
            collision_widths=(8, 16, 17, 32),
        )
        for row in result["ordinary_rows"]:
            self.assertEqual(0, row["cuckoo_insert_failures"])
            self.assertLessEqual(
                row["cuckoo_max_mutation_slot_work"],
                row["cuckoo_theoretical_max_mutation_slot_work"],
            )
            self.assertLessEqual(row["cuckoo_lookup_page_max"], 2)

        stress = {row["colliding_keys"]: row for row in result["collision_stress_rows"]}
        self.assertEqual(0, stress[16]["cuckoo_failures"])
        self.assertEqual(1, stress[17]["cuckoo_failures"])
        self.assertEqual(16, stress[32]["cuckoo_successes"])
        self.assertEqual(32, stress[32]["linear_max_insert_slot_probes"])
        self.assertEqual(
            stress[32]["cuckoo_theoretical_max_mutation_slot_work"],
            stress[32]["cuckoo_max_mutation_slot_work"],
        )


if __name__ == "__main__":
    unittest.main()
