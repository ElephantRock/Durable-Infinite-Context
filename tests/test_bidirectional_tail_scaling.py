from __future__ import annotations

import unittest

from simulator.bidirectional_tail_scaling import run_bidirectional_tail_scaling_experiment


class BidirectionalTailScalingTests(unittest.TestCase):
    def test_shrink_work_is_independent_of_free_chain_length(self) -> None:
        result = run_bidirectional_tail_scaling_experiment()
        self.assertEqual(result["target_descriptor_counts"], [3, 4, 5, 6])
        self.assertTrue(result["all_constant_shrink_work"])
        self.assertEqual(
            [row["free_chain_length_before"] for row in result["rows"]],
            [2, 3, 4, 5],
        )
        for row in result["rows"]:
            self.assertEqual(row["retirement_descriptor_preads"], 4)
            self.assertEqual(row["retirement_descriptor_pwrites"], 1)
            self.assertEqual(row["retirement_descriptors_scanned"], 0)
            self.assertEqual(row["candidate_relocations"], 0)
            self.assertEqual(row["arena_pages_before"] - row["arena_pages_after"], 2)


if __name__ == "__main__":
    unittest.main()
