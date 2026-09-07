from __future__ import annotations

import unittest

from storage.rare_overflow import RareOverflowHybridIndex, run_rare_overflow_envelope


class RareOverflowTests(unittest.TestCase):
    def test_primary_hit_does_not_touch_overflow(self) -> None:
        index = RareOverflowHybridIndex(1024)
        try:
            trace = index.insert("alpha|deadline")
            self.assertEqual(trace.path, "primary")
            lookup = index.lookup("alpha|deadline")
            self.assertTrue(lookup.found)
            self.assertEqual(lookup.path, "primary")
            self.assertFalse(lookup.overflow_checked)
            self.assertEqual(lookup.overflow_btree_height, 0)
        finally:
            index.close()

    def test_concentrated_overflow_is_directly_addressable(self) -> None:
        index = RareOverflowHybridIndex(1024, force_same_pair=True)
        try:
            for number in range(16):
                self.assertEqual(index.insert(f"primary_{number:03d}").path, "primary")
            overflow_trace = index.insert("overflow_000")
            self.assertEqual(overflow_trace.path, "overflow")
            self.assertEqual(overflow_trace.primary_mutation_slot_work, 200)
            self.assertEqual(index.overflow_rows, 1)
            self.assertTrue(index.overflow_uses_primary_key())

            hit = index.lookup("overflow_000")
            self.assertTrue(hit.found)
            self.assertEqual(hit.path, "overflow")
            self.assertTrue(hit.overflow_checked)
            self.assertEqual(hit.primary_page_probes, 3)

            missing = index.lookup("missing")
            self.assertFalse(missing.found)
            self.assertEqual(missing.path, "miss")
            self.assertTrue(missing.overflow_checked)
            self.assertEqual(missing.primary_page_probes, 3)
        finally:
            index.close()

    def test_small_envelope_isolates_common_path_and_exposes_overflow_growth(self) -> None:
        result = run_rare_overflow_envelope(
            checkpoints=(1000,),
            overflow_checkpoints=(1, 64, 1024),
            sample_count=128,
        )
        ordinary = result["ordinary_rows"][0]
        self.assertEqual(ordinary["overflow_insertions"], 0)
        self.assertEqual(ordinary["primary_lookup_overflow_checks"], 0)
        self.assertLessEqual(ordinary["primary_lookup_page_max"], 2)

        stress = result["overflow_stress_rows"]
        self.assertTrue(all(row["all_inserted_found"] for row in stress))
        self.assertTrue(all(not row["primary_hit_overflow_checked"] for row in stress))
        self.assertTrue(all(row["overflow_hit_primary_pages"] == 3 for row in stress))
        self.assertTrue(all(row["missing_primary_pages"] == 3 for row in stress))
        heights = [row["overflow_btree_height"] for row in stress]
        self.assertEqual(heights, sorted(heights))
        self.assertGreaterEqual(heights[-1], heights[0])

        control = result["v021_d8_control"]
        self.assertEqual(control["successes"], 128)
        self.assertEqual(control["failures"], 128)
        self.assertEqual(control["max_mutation_slot_work"], 1600)
        self.assertEqual(control["missing_lookup_page_probes"], 24)


if __name__ == "__main__":
    unittest.main()
