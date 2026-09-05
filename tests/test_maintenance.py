import unittest

from core.models import Assertion, EvidenceRecord
from rag.maintenance import AddressabilityMaintainer
from rag.scalable_planner import ScalableQueryPlanner, SubjectProfileIndex
from simulator.maintenance import (
    alias,
    assertion_id,
    build_maintenance_store,
    evidence_id,
    subject_id,
)


class MaintenanceTests(unittest.TestCase):
    def test_evidence_dependency_refcount_survives_partial_assertion_removal(self):
        store = build_maintenance_store(1)
        store.add_assertion(Assertion(
            "second",
            subject_id(0),
            "approved",
            True,
            2,
            evidence_ids=(evidence_id(0),),
        ))
        self.assertEqual(store.subjects_for_evidence(evidence_id(0)), (subject_id(0),))
        store.remove_assertion("second")
        self.assertEqual(store.subjects_for_evidence(evidence_id(0)), (subject_id(0),))
        store.remove_assertion(assertion_id(0))
        self.assertEqual(store.subjects_for_evidence(evidence_id(0)), ())

    def test_insert_is_local_and_matches_full_rebuild(self):
        store = build_maintenance_store(200)
        index = SubjectProfileIndex(store)
        maintainer = AddressabilityMaintainer(store, index)

        i = 200
        maintainer.upsert_evidence(EvidenceRecord(
            evidence_id(i), f"{alias(i)} finance migration deadline is day 42.", "source", i + 1
        ))
        result = maintainer.upsert_assertion(Assertion(
            assertion_id(i), subject_id(i), "deadline", 42, i + 1, evidence_ids=(evidence_id(i),)
        ))

        self.assertEqual(result.affected_subject_ids, (subject_id(i),))
        self.assertEqual(result.trace.subjects_refreshed, 1)
        self.assertTrue(index.equivalent_to(SubjectProfileIndex(store)))
        plan = ScalableQueryPlanner(index).plan(f"What is {alias(i)}'s due date?")
        self.assertEqual(plan.subject_id, subject_id(i))

    def test_evidence_alias_replacement_is_local_and_removes_old_address(self):
        store = build_maintenance_store(200)
        index = SubjectProfileIndex(store)
        maintainer = AddressabilityMaintainer(store, index)
        i = 73

        result = maintainer.upsert_evidence(EvidenceRecord(
            evidence_id(i), f"{alias(i, 'Nova')} finance migration deadline is day 42.", "source", 500
        ))

        self.assertEqual(result.affected_subject_ids, (subject_id(i),))
        self.assertEqual(result.trace.subjects_refreshed, 1)
        self.assertTrue(index.equivalent_to(SubjectProfileIndex(store)))

        planner = ScalableQueryPlanner(index)
        self.assertEqual(
            planner.plan(f"What is {alias(i, 'Nova')}'s due date?").subject_id,
            subject_id(i),
        )
        self.assertIsNone(planner.plan(f"What is {alias(i)}'s due date?").subject_id)

    def test_predicate_change_updates_predicate_specific_postings(self):
        store = build_maintenance_store(200)
        index = SubjectProfileIndex(store)
        maintainer = AddressabilityMaintainer(store, index)
        i = 41

        old = store.assertions[assertion_id(i)]
        result = maintainer.upsert_assertion(Assertion(
            old.id,
            old.subject_id,
            "approved",
            True,
            old.recorded_seq,
            valid_from=old.valid_from,
            evidence_ids=old.evidence_ids,
        ))

        self.assertEqual(result.affected_subject_ids, (subject_id(i),))
        self.assertTrue(index.equivalent_to(SubjectProfileIndex(store)))
        planner = ScalableQueryPlanner(index)
        self.assertEqual(planner.plan(f"Is {alias(i)} approved?").subject_id, subject_id(i))
        self.assertIsNone(planner.plan(f"What is {alias(i)}'s due date?").subject_id)

    def test_shared_evidence_update_scales_with_actual_fanout(self):
        store = build_maintenance_store(500)
        index = SubjectProfileIndex(store)
        maintainer = AddressabilityMaintainer(store, index)
        shared_id = "shared-evidence"
        fanout = (7, 111, 223, 401)

        maintainer.upsert_evidence(EvidenceRecord(
            shared_id, "SharedBeacon portfolio marker.", "source", 1000
        ))
        for i in fanout:
            old = store.assertions[assertion_id(i)]
            maintainer.upsert_assertion(Assertion(
                old.id,
                old.subject_id,
                old.predicate,
                old.object_value,
                old.recorded_seq,
                valid_from=old.valid_from,
                evidence_ids=(evidence_id(i), shared_id),
            ))

        result = maintainer.upsert_evidence(EvidenceRecord(
            shared_id, "SharedNova portfolio marker.", "source", 1001
        ))
        self.assertEqual(set(result.affected_subject_ids), {subject_id(i) for i in fanout})
        self.assertEqual(result.trace.subjects_refreshed, len(fanout))
        self.assertTrue(index.equivalent_to(SubjectProfileIndex(store)))

    def test_assertion_and_evidence_deletion_match_full_rebuild(self):
        store = build_maintenance_store(100)
        index = SubjectProfileIndex(store)
        maintainer = AddressabilityMaintainer(store, index)

        removed_assertion_subject = subject_id(10)
        result_a = maintainer.delete_assertion(assertion_id(10))
        self.assertEqual(result_a.affected_subject_ids, (removed_assertion_subject,))
        self.assertNotIn(removed_assertion_subject, index.profiles)
        self.assertTrue(index.equivalent_to(SubjectProfileIndex(store)))

        result_e = maintainer.delete_evidence(evidence_id(20))
        self.assertEqual(result_e.affected_subject_ids, (subject_id(20),))
        self.assertNotIn(subject_id(20), index.profiles)
        self.assertTrue(index.equivalent_to(SubjectProfileIndex(store)))


if __name__ == "__main__":
    unittest.main()
