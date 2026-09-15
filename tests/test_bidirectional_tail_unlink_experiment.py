from __future__ import annotations

import unittest

from simulator.bidirectional_tail_unlink import run_bidirectional_tail_unlink_experiment


class BidirectionalTailUnlinkExperimentTests(unittest.TestCase):
    def test_buried_physical_tail_unlinks_with_bounded_current_topology(self) -> None:
        result = run_bidirectional_tail_unlink_experiment()
        self.assertTrue(result["survived"])
        self.assertEqual(result["candidate_descriptor_history_walks"], 0)
        self.assertEqual(result["candidate_relocations"], 0)

        control = result["v038_control"]
        self.assertFalse(control["trace"]["released"])
        self.assertEqual(control["trace"]["retirement_descriptor_preads"], 0)
        self.assertEqual(control["retained_arena_pages"], 6)

        release = result["non_head_release"]
        self.assertEqual(release["queue_before"]["descriptor_free_head_page"], 2)
        self.assertFalse(release["trace"]["tail_was_free_head"])
        self.assertEqual(release["trace"]["predecessor_page"], 2)
        self.assertEqual(release["trace"]["retirement_descriptor_preads"], 4)
        self.assertEqual(release["trace"]["retirement_descriptor_pwrites"], 1)
        self.assertEqual(release["trace"]["retirement_descriptors_scanned"], 0)
        self.assertEqual(release["released_arena_pages"], 2)
        self.assertEqual(release["released_arena_bytes"], 8192)
        self.assertEqual(release["queue_after"]["descriptor_arena_pages"], 4)

        reuse = result["reuse_head_predecessor_repair"]
        self.assertTrue(reuse["new_head_predecessor_cleared"])
        self.assertEqual(reuse["queue_after"]["descriptor_free_head_page"], 4)

        identity = result["identity_after_non_head_shrink"]
        self.assertTrue(identity["stale_rejected_after_shrink"])
        self.assertTrue(identity["incarnation_advanced"])
        self.assertTrue(identity["stale_rejected_after_reuse"])
        self.assertEqual(identity["arena_pages_after_reuse"], 6)

        crashes = result["crash_matrix"]
        self.assertEqual(crashes["case_count"], 6)
        self.assertTrue(crashes["all_exact_committed_state_match"])
        self.assertTrue(crashes["all_recovery_scan_free"])
        self.assertTrue(crashes["all_second_recovery_idempotent"])


if __name__ == "__main__":
    unittest.main()
