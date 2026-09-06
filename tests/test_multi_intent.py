from __future__ import annotations

import unittest

from simulator.multi_intent import (
    run_concurrent_admission_case,
    run_overlapping_derived_case,
    run_phase_aware_read_case,
    run_same_key_conflict_case,
)


class MultiIntentTests(unittest.TestCase):
    def test_real_processes_concurrently_admit_independent_intents(self):
        case = run_concurrent_admission_case(entity_count=48, writer_count=6)
        self.assertEqual(case["admitted"], 6)
        self.assertEqual(case["unique_sequences"], 6)
        self.assertEqual(case["queue_after"]["done"], 6)
        self.assertEqual(case["queue_after"]["conflict"], 0)
        self.assertTrue(case["queue_lookup_uses_index"])
        self.assertTrue(case["semantic_check"])
        self.assertTrue(case["materialization_equal"])

    def test_same_key_intents_surface_conflict_instead_of_lost_update(self):
        case = run_same_key_conflict_case(entity_count=32, index=7)
        self.assertEqual(case["statuses"], ["done", "conflict", "done"])
        self.assertEqual(case["first_base_version"], case["second_base_version"])
        self.assertGreater(case["retry_base_version"], case["first_base_version"])
        self.assertEqual(case["final_value"], 73)
        self.assertTrue(case["semantic_check"])
        self.assertTrue(case["materialization_equal"])

    def test_process_crash_blocks_only_the_stale_derived_region(self):
        case = run_phase_aware_read_case(
            entity_count=40,
            active_index=5,
            queued_index=17,
        )
        self.assertEqual(case["durable_phase"], "canonical_applied")
        self.assertEqual(case["process_failure"], "SIGKILL")
        self.assertTrue(case["affected_read_blocked"])
        self.assertTrue(case["queued_read_admitted"])
        self.assertTrue(case["unrelated_read_admitted"])
        self.assertEqual(case["queue_final"]["done"], 2)
        self.assertTrue(case["semantic_check"])
        self.assertTrue(case["materialization_equal"])

    def test_overlapping_derived_regions_serialize_disjoint_canonical_writes(self):
        case = run_overlapping_derived_case(entity_count=32, index=9)
        self.assertTrue(case["distinct_write_keys"])
        self.assertTrue(case["shared_read_keys"])
        self.assertEqual(case["queue_final"]["done"], 2)
        self.assertEqual(case["queue_final"]["conflict"], 0)
        self.assertTrue(case["semantic_check"])
        self.assertTrue(case["materialization_equal"])


if __name__ == "__main__":
    unittest.main()
