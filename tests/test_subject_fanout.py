from __future__ import annotations

import unittest

from simulator.subject_fanout import (
    run_head_fallback_case,
    run_v013_history_control,
    run_v014_head_index_case,
)


class SubjectFanoutTests(unittest.TestCase):
    def test_v013_profile_rebuild_work_grows_with_irrelevant_history_depth(self):
        shallow = run_v013_history_control(
            entity_count=64,
            predicate_count=4,
            history_depth=1,
        )
        deep = run_v013_history_control(
            entity_count=64,
            predicate_count=4,
            history_depth=8,
        )
        self.assertTrue(shallow["materialization_equal"])
        self.assertTrue(deep["materialization_equal"])
        self.assertGreater(deep["total_recovery_work"], shallow["total_recovery_work"])
        self.assertGreater(
            deep["base_recovery_trace"]["canonical_rows_read"],
            shallow["base_recovery_trace"]["canonical_rows_read"],
        )

    def test_v014_head_index_removes_history_depth_from_current_profile_rebuild(self):
        shallow = run_v014_head_index_case(
            entity_count=64,
            predicate_count=4,
            history_depth=1,
        )
        deep = run_v014_head_index_case(
            entity_count=64,
            predicate_count=4,
            history_depth=16,
        )
        self.assertEqual(shallow["total_recovery_work"], deep["total_recovery_work"])
        self.assertEqual(
            shallow["base_recovery_trace"]["canonical_rows_read"],
            deep["base_recovery_trace"]["canonical_rows_read"],
        )
        self.assertEqual(shallow["head_trace"]["head_rows_read"], 4)
        self.assertEqual(deep["head_trace"]["head_rows_read"], 4)
        self.assertEqual(deep["head_trace"]["head_refresh_queries"], 0)
        self.assertTrue(shallow["head_index_equal"])
        self.assertTrue(deep["head_index_equal"])
        self.assertTrue(deep["head_lookup_uses_index"])
        self.assertTrue(deep["head_refresh_uses_index"])

    def test_v014_profile_work_still_grows_with_true_live_predicate_fanout(self):
        small = run_v014_head_index_case(
            entity_count=64,
            predicate_count=2,
            history_depth=4,
        )
        larger = run_v014_head_index_case(
            entity_count=64,
            predicate_count=8,
            history_depth=4,
        )
        self.assertEqual(small["profile_predicate_count"], 2)
        self.assertEqual(larger["profile_predicate_count"], 8)
        self.assertGreater(larger["total_recovery_work"], small["total_recovery_work"])
        self.assertEqual(small["head_trace"]["head_rows_read"], 2)
        self.assertEqual(larger["head_trace"]["head_rows_read"], 8)
        self.assertTrue(small["materialization_equal"])
        self.assertTrue(larger["materialization_equal"])

    def test_v014_fixed_local_fanout_is_independent_of_unrelated_global_cardinality(self):
        small = run_v014_head_index_case(
            entity_count=64,
            predicate_count=4,
            history_depth=8,
        )
        larger = run_v014_head_index_case(
            entity_count=512,
            predicate_count=4,
            history_depth=8,
        )
        self.assertEqual(small["total_recovery_work"], larger["total_recovery_work"])
        self.assertLess(small["total_recovery_work"], small["full_rebuild_work"])
        self.assertLess(larger["total_recovery_work"], larger["full_rebuild_work"])
        self.assertTrue(small["head_index_equal"])
        self.assertTrue(larger["head_index_equal"])

    def test_v014_head_index_beats_v013_when_history_is_deep(self):
        control = run_v013_history_control(
            entity_count=64,
            predicate_count=8,
            history_depth=16,
        )
        fixed = run_v014_head_index_case(
            entity_count=64,
            predicate_count=8,
            history_depth=16,
        )
        self.assertLess(fixed["total_recovery_work"], control["total_recovery_work"])
        self.assertTrue(fixed["materialization_equal"])
        self.assertTrue(fixed["head_index_equal"])

    def test_head_index_falls_back_after_predicate_move_and_removes_deleted_head(self):
        case = run_head_fallback_case(entity_count=48, index=24, history_depth=4)
        fallback = case["fallback_assertion_id"]
        self.assertEqual(case["after_move"]["deadline"], fallback)
        self.assertIn("renamed_deadline", case["after_move"])
        self.assertTrue(case["move_materialization_equal"])
        self.assertTrue(case["move_head_index_equal"])
        self.assertGreaterEqual(case["move_head_trace"]["head_refresh_queries"], 2)
        self.assertTrue(case["head_lookup_uses_index"])
        self.assertTrue(case["head_refresh_uses_index"])

        self.assertEqual(case["after_delete"]["deadline"], fallback)
        self.assertNotIn("renamed_deadline", case["after_delete"])
        self.assertTrue(case["delete_materialization_equal"])
        self.assertTrue(case["delete_head_index_equal"])
        self.assertEqual(case["final_profile_predicates"], ["deadline", "facet_001"])
        self.assertTrue(case["deadline_context_present"])
        self.assertFalse(case["renamed_context_present"])
        self.assertEqual(case["delete_queue_final"]["conflict"], 0)


if __name__ == "__main__":
    unittest.main()
