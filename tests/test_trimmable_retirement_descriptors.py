from __future__ import annotations

import unittest

from simulator.trimmable_retirement_descriptors import run_tail_release_falsification


class DescriptorTailReleaseControlTests(unittest.TestCase):
    def test_real_free_heads_are_not_physical_file_tail(self) -> None:
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
            self.assertEqual(row["descriptor_history_walks_required_by_control"], 0)
            self.assertEqual(row["physical_pages_released_by_control"], 0)


if __name__ == "__main__":
    unittest.main()
