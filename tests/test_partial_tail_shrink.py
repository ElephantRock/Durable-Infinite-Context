from __future__ import annotations

import unittest

from simulator.partial_tail_shrink import run_partial_tail_shrink_experiment


class PartialTailShrinkTests(unittest.TestCase):
    def test_live_backlog_tail_pair_can_be_released_without_history_scan(self) -> None:
        result = run_partial_tail_shrink_experiment()
        self.assertTrue(result["survived"])
        self.assertEqual(result["candidate_descriptor_history_walks"], 0)
        self.assertEqual(result["candidate_relocations"], 0)

        control = result["v037_control"]
        self.assertEqual(control["retained_arena_pages"], 6)
        self.assertEqual(control["retained_arena_bytes"], 6 * 4096)

        nonaligned = result["nonaligned_noop"]
        self.assertFalse(nonaligned["trace"]["released"])
        self.assertEqual(nonaligned["trace"]["retirement_descriptor_preads"], 0)
        self.assertEqual(nonaligned["trace"]["retirement_descriptors_scanned"], 0)

        release = result["live_backlog_release"]
        self.assertTrue(release["live_backlog_preserved"])
        self.assertEqual(release["released_arena_pages"], 2)
        self.assertEqual(release["released_arena_bytes"], 8192)
        self.assertEqual(release["queue_before"]["descriptor_arena_pages"], 6)
        self.assertEqual(release["queue_after"]["descriptor_arena_pages"], 4)
        self.assertEqual(release["trace"]["retirement_descriptor_preads"], 2)
        self.assertEqual(release["trace"]["retirement_descriptor_pwrites"], 0)
        self.assertEqual(release["trace"]["retirement_descriptors_scanned"], 0)
        self.assertEqual(release["trace"]["candidate_relocations"], 0)

        identity = result["identity_after_partial_shrink"]
        self.assertTrue(identity["stale_rejected_after_shrink"])
        self.assertTrue(identity["incarnation_advanced"])
        self.assertTrue(identity["stale_rejected_after_reuse"])
        self.assertEqual(identity["arena_pages_after_reuse"], 6)

        crashes = result["crash_matrix"]
        self.assertEqual(crashes["case_count"], 5)
        self.assertTrue(crashes["all_exact_committed_state_match"])
        self.assertTrue(crashes["all_recovery_scan_free"])
        self.assertTrue(crashes["all_second_recovery_idempotent"])


if __name__ == "__main__":
    unittest.main()
