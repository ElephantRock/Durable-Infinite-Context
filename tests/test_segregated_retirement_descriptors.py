from __future__ import annotations

import unittest

from simulator.segregated_retirement_descriptors import (
    run_segregated_descriptor_arena_experiment,
)


class SegregatedRetirementDescriptorTests(unittest.TestCase):
    def test_segregated_arena_releases_post_drain_capacity_and_rejects_stale_identity(self) -> None:
        result = run_segregated_descriptor_arena_experiment()
        self.assertTrue(result["survived"])
        self.assertEqual(result["candidate_descriptor_history_walks"], 0)
        self.assertEqual(result["candidate_relocations"], 0)

        peak = result["peak_release"]
        self.assertEqual(peak["peak_descriptor_pairs"], 3)
        self.assertEqual(peak["peak_arena_pages"], 6)
        self.assertEqual(peak["released_arena_pages"], 6)
        self.assertEqual(peak["queue_after_drain"]["descriptor_arena_pages"], 0)
        self.assertEqual(peak["arena_after_drain"]["arena_file_bytes"], 0)

        identity = result["identity_after_reset"]
        self.assertTrue(identity["same_arena_page_reused"])
        self.assertTrue(identity["incarnation_advanced"])
        self.assertTrue(identity["stale_identity_rejected"])

        history = result["history_cycles"]
        self.assertGreaterEqual(history["cycle_count"], 4)
        self.assertTrue(history["all_cycles_return_to_zero_committed_arena_pages"])
        self.assertTrue(history["all_cycles_return_to_zero_arena_file_bytes"])

        crashes = result["crash_matrices"]
        self.assertEqual(crashes["case_count"], 26)
        for name in (
            "fresh_empty_queue",
            "fresh_nonempty_queue",
            "partial_head",
            "final_arena_reset",
        ):
            row = crashes[name]
            self.assertTrue(row["all_exact_committed_state_match"])
            self.assertTrue(row["all_recovery_scan_free"])
            self.assertTrue(row["all_second_recovery_idempotent"])


if __name__ == "__main__":
    unittest.main()
