from __future__ import annotations

import unittest

from simulator.trimmable_retirement_descriptors import run_tail_release_falsification


class DescriptorTailReleaseControlTests(unittest.TestCase):
    def test_real_free_descriptors_are_buried_below_physical_file_tail(self) -> None:
        result = run_tail_release_falsification()
        self.assertTrue(result["falsified"])
        self.assertEqual(result["candidate_history_walks"], 0)
        self.assertEqual(result["candidate_physical_pages_released"], 0)
        for name, expected_pool in (
            ("single_descriptor_case", 1),
            ("three_descriptor_peak_case", 3),
        ):
            row = result[name]
            self.assertEqual(row["expected_descriptor_pool"], expected_pool)
            self.assertFalse(row["free_head_is_physical_tail"])
            self.assertFalse(row["head_only_tail_release_possible"])
            self.assertGreater(row["committed_suffix_pages_after_free_head"], 0)
            self.assertTrue(row["all_free_descriptors_buried_below_tail"])
            self.assertEqual(len(row["free_descriptor_layout_diagnostic"]), expected_pool)
            self.assertGreater(row["minimum_committed_suffix_pages_after_free_descriptor"], 0)
            self.assertTrue(row["diagnostic_free_chain_traversal_is_not_candidate_work"])
            for descriptor in row["free_descriptor_layout_diagnostic"]:
                self.assertFalse(descriptor["is_physical_tail"])
                self.assertGreater(descriptor["committed_suffix_pages"], 0)
            self.assertEqual(row["descriptor_history_walks_required_by_control"], 0)
            self.assertEqual(row["physical_pages_released_by_control"], 0)


if __name__ == "__main__":
    unittest.main()
