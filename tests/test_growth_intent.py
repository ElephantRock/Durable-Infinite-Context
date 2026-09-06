from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from simulator.cascade import subject_id
from simulator.growth import (
    run_growth_locality_case,
    run_v011_growth_control,
    run_v012_growth_creation_case,
)
from storage.growth_intent import GrowthAwareTopologyStore


class GrowthIntentTests(unittest.TestCase):
    def test_v011_control_loses_materialization_on_brand_new_subject(self):
        case = run_v011_growth_control(entity_count=48, moved_index=30)
        self.assertTrue(case["canonical_moved"])
        self.assertFalse(case["target_context_present"])
        self.assertEqual(case["target_derived_count"], 0)
        self.assertTrue(case["old_subject_retired"])
        self.assertFalse(case["materialization_equal"])
        self.assertTrue(case["all_derived_fresh"])

    def test_v012_creates_missing_target_materialization_locally(self):
        case = run_v012_growth_creation_case(entity_count=48, moved_index=30)
        self.assertTrue(case["canonical_moved"])
        self.assertTrue(case["target_context_present"])
        self.assertEqual(case["target_derived_count"], 4)
        self.assertTrue(case["old_subject_retired"])
        self.assertTrue(case["materialization_equal"])
        self.assertTrue(case["all_derived_fresh"])

    def test_new_target_read_is_blocked_between_canonical_commit_and_growth_repair(self):
        with tempfile.TemporaryDirectory(prefix="dic-v012-growth-gap-") as tmp:
            store = GrowthAwareTopologyStore(Path(tmp) / "memory.sqlite3")
            store.bootstrap(24)
            target_index = 41
            target_subject = subject_id(target_index)

            intent = store.enqueue_topology_move(12, target_index, writer="growth-gap")
            promoted = store.promote_next()
            self.assertIsNotNone(promoted)
            self.assertEqual(promoted["intent_id"], intent["intent_id"])

            store.apply_canonical_transaction()
            with self.assertRaises(RuntimeError):
                store.read_context(target_subject)

            # The protection remains local: an unrelated derived read is admitted.
            self.assertIsNotNone(store.read_context(subject_id(3)))

            store.recover()
            self.assertIsNotNone(store.read_context(target_subject))
            self.assertTrue(store.materialization_matches_clean_rebuild())

    def test_object_only_update_does_not_create_growth_obligations(self):
        with tempfile.TemporaryDirectory(prefix="dic-v012-no-growth-") as tmp:
            store = GrowthAwareTopologyStore(Path(tmp) / "memory.sqlite3")
            store.bootstrap(16)
            store.enqueue_operation("replace_assertion_object", 5, new_value=88)
            promoted = store.promote_next()
            self.assertIsNotNone(promoted)
            with store.connect() as conn:
                intent = store._intent(conn)
                self.assertIsNotNone(intent)
                self.assertEqual(store._candidate_growth_specs(intent), [])
            store.recover()
            self.assertEqual(store.canonical_value(5), 88)
            self.assertTrue(store.materialization_matches_clean_rebuild())

    def test_growth_creation_work_is_local_to_fixed_output_obligation(self):
        small = run_growth_locality_case(64)
        larger = run_growth_locality_case(256)
        self.assertEqual(small["recovery_work"], larger["recovery_work"])
        self.assertLess(small["recovery_work"], small["full_rebuild_work"])
        self.assertLess(larger["recovery_work"], larger["full_rebuild_work"])
        self.assertEqual(small["target_derived_count"], 4)
        self.assertEqual(larger["target_derived_count"], 4)
        self.assertTrue(small["materialization_equal"])
        self.assertTrue(larger["materialization_equal"])


if __name__ == "__main__":
    unittest.main()
