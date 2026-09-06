from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from simulator.cascade import subject_id
from simulator.predicate_schema import (
    run_predicate_locality_case,
    run_v012_predicate_control,
    run_v013_predicate_addition,
    run_v013_predicate_replacement,
)
from storage.predicate_schema import PredicateSchemaAwareStore


class PredicateSchemaTests(unittest.TestCase):
    def test_v012_control_retires_subject_profile_after_deadline_to_launch_date(self):
        case = run_v012_predicate_control(entity_count=48, index=30)
        self.assertTrue(case["canonical_changed"])
        self.assertTrue(case["new_context_present"])
        self.assertTrue(case["old_context_retired"])
        self.assertFalse(case["profile_present"])
        self.assertEqual(case["subject_derived_count"], 3)
        self.assertFalse(case["materialization_equal"])
        self.assertTrue(case["all_derived_fresh"])

    def test_v013_replacement_preserves_subject_profile_under_new_predicate(self):
        case = run_v013_predicate_replacement(entity_count=48, index=30)
        self.assertTrue(case["canonical_changed"])
        self.assertTrue(case["new_context_present"])
        self.assertTrue(case["old_context_retired"])
        self.assertTrue(case["profile_present"])
        self.assertEqual(case["profile_predicates"], ["launch_date"])
        self.assertEqual(case["subject_derived_count"], 4)
        self.assertTrue(case["materialization_equal"])
        self.assertTrue(case["all_derived_fresh"])
        self.assertTrue(case["profile_lookup_uses_index"])

    def test_v013_profile_aggregates_two_live_predicates(self):
        case = run_v013_predicate_addition(entity_count=48, index=30)
        self.assertTrue(case["added_assertion_present"])
        self.assertTrue(case["deadline_context_present"])
        self.assertTrue(case["new_context_present"])
        self.assertTrue(case["profile_present"])
        self.assertEqual(case["profile_predicates"], ["deadline", "launch_date"])
        self.assertEqual(case["subject_derived_count"], 7)
        self.assertEqual(case["queue_final"]["done"], 2)
        self.assertEqual(case["queue_final"]["conflict"], 0)
        self.assertTrue(case["materialization_equal"])
        self.assertTrue(case["all_derived_fresh"])
        self.assertTrue(case["profile_lookup_uses_index"])

    def test_predicate_change_blocks_both_old_and_new_reads_during_canonical_gap(self):
        with tempfile.TemporaryDirectory(prefix="dic-v013-predicate-gap-") as tmp:
            store = PredicateSchemaAwareStore(Path(tmp) / "memory.sqlite3")
            store.bootstrap(24)
            subject = subject_id(12)
            unrelated = subject_id(3)

            intent = store.enqueue_predicate_change(
                12,
                "launch_date",
                new_value=55,
                writer="predicate-gap",
            )
            promoted = store.promote_next()
            self.assertIsNotNone(promoted)
            self.assertEqual(promoted["intent_id"], intent["intent_id"])

            store.apply_canonical_transaction()
            with self.assertRaises(RuntimeError):
                store.read_context(subject, "deadline")
            with self.assertRaises(RuntimeError):
                store.read_context(subject, "launch_date")

            self.assertIsNotNone(store.read_context(unrelated, "deadline"))

            store.recover()
            self.assertIsNone(store.read_context(subject, "deadline"))
            self.assertIsNotNone(store.read_context(subject, "launch_date"))
            self.assertTrue(store.materialization_matches_clean_rebuild())

    def test_predicate_replacement_work_is_local_to_subject_schema(self):
        small = run_predicate_locality_case(64)
        larger = run_predicate_locality_case(256)
        self.assertEqual(small["recovery_work"], larger["recovery_work"])
        self.assertLess(small["recovery_work"], small["full_rebuild_work"])
        self.assertLess(larger["recovery_work"], larger["full_rebuild_work"])
        self.assertTrue(small["materialization_equal"])
        self.assertTrue(larger["materialization_equal"])


if __name__ == "__main__":
    unittest.main()
