from __future__ import annotations

import unittest

from simulator.bidirectional_topology_crash import run_bidirectional_topology_crash_experiment


class BidirectionalTopologyCrashTests(unittest.TestCase):
    def test_predecessor_push_and_head_reuse_publish_crash_exactly(self) -> None:
        result = run_bidirectional_topology_crash_experiment()
        self.assertEqual(result["case_count"], 8)
        self.assertTrue(result["all_exact_committed_state_match"])
        self.assertTrue(result["all_recovery_scan_free"])
        self.assertTrue(result["all_second_recovery_idempotent"])

        push = result["predecessor_push"]
        self.assertEqual(push["case_count"], 4)
        self.assertTrue(push["all_exact_committed_state_match"])
        self.assertEqual(push["clean_post_state"]["queue"]["descriptor_free_head_page"], 2)
        self.assertEqual(
            [row["descriptor_page"] for row in push["clean_post_state"]["queue"]["free_descriptors"]],
            [2, 4],
        )

        pop = result["free_head_reuse"]
        self.assertEqual(pop["case_count"], 4)
        self.assertTrue(pop["all_exact_committed_state_match"])
        self.assertEqual(pop["clean_post_state"]["queue"]["descriptor_free_head_page"], 4)
        self.assertNotIn(
            "prev_descriptor_page",
            pop["clean_post_state"]["queue"]["free_descriptors"][0],
        )


if __name__ == "__main__":
    unittest.main()
