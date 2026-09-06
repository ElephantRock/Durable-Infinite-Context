from __future__ import annotations

import unittest

from simulator.topology import (
    run_topology_locality_case,
    run_v010_topology_control,
    run_v011_topology_revalidation_case,
)


class TopologyIntentTests(unittest.TestCase):
    def test_v010_admission_time_impact_metadata_leaks_stale_read(self):
        case = run_v010_topology_control(entity_count=48, moved_index=30, target_index=5)
        self.assertEqual(case["admission_read_keys"], [case["old_read_key"]])
        self.assertEqual(case["promotion_read_keys"], [case["old_read_key"]])
        self.assertFalse(case["read_keys_changed"])
        self.assertTrue(case["stale_read_admitted"])
        self.assertTrue(case["stale_value_visible"])
        self.assertTrue(case["unrelated_read_admitted"])
        self.assertTrue(case["semantic_check"])
        self.assertTrue(case["materialization_equal"])

    def test_v011_revalidation_blocks_newly_affected_target(self):
        case = run_v011_topology_revalidation_case(
            entity_count=48,
            moved_index=30,
            target_index=5,
        )
        self.assertEqual(case["admission_read_keys"], [case["old_read_key"]])
        self.assertEqual(case["promotion_read_keys"], [case["target_read_key"]])
        self.assertTrue(case["read_keys_changed"])
        self.assertTrue(case["stale_read_blocked"])
        self.assertFalse(case["stale_read_admitted"])
        self.assertTrue(case["unrelated_read_admitted"])
        self.assertTrue(case["final_context_has_new_payload"])
        self.assertTrue(case["old_subject_retired"])
        self.assertTrue(case["revalidation_lookup_uses_index"])
        self.assertTrue(case["semantic_check"])
        self.assertTrue(case["materialization_equal"])

    def test_topology_revalidation_work_is_local_to_fixed_intents(self):
        small = run_topology_locality_case(64)
        larger = run_topology_locality_case(256)
        self.assertEqual(small["total_recovery_work"], larger["total_recovery_work"])
        self.assertLess(small["total_recovery_work"], small["full_rebuild_work"])
        self.assertLess(larger["total_recovery_work"], larger["full_rebuild_work"])
        self.assertTrue(small["revalidation_lookup_uses_index"])
        self.assertTrue(larger["revalidation_lookup_uses_index"])
        self.assertTrue(small["semantic_check"])
        self.assertTrue(larger["semantic_check"])


if __name__ == "__main__":
    unittest.main()
