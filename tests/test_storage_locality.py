import unittest

from core.models import Assertion, AssertionRelation, EvidenceRecord, RelationType
from core.storage import MemoryStore
from rag.scalable_planner import SubjectProfileIndex


class StorageLocalityTests(unittest.TestCase):
    def test_prepopulated_store_hydrates_locality_indexes(self):
        evidence = {
            "e1": EvidenceRecord("e1", "Atlas-0000001 deadline is day 42.", "src", 1),
        }
        assertion = Assertion(
            "a1", "subject-1", "deadline", 42, 1, evidence_ids=("e1",)
        )
        relation = AssertionRelation("a1", RelationType.REFINES, "a0")
        store = MemoryStore(
            evidence=evidence,
            assertions={"a1": assertion},
            relations=[relation],
        )

        self.assertEqual(store.subject_ids(), ("subject-1",))
        self.assertEqual(store.subjects_for_evidence("e1"), ("subject-1",))
        self.assertEqual(store.assertions_for_subject("subject-1"), [assertion])
        self.assertEqual(store.relations_from("a1"), [relation])
        self.assertIn("subject-1", SubjectProfileIndex(store).profiles)

    def test_equal_recorded_sequence_uses_assertion_id_tiebreaker(self):
        # Insert in reverse lexical order to ensure set/hash/insertion behavior cannot
        # determine the returned semantic ordering.
        b = Assertion("b", "subject", "deadline", 20, 7)
        a = Assertion("a", "subject", "deadline", 10, 7)
        store = MemoryStore(assertions={"b": b, "a": a})

        ordered = store.assertions_for_key(("subject", "deadline", "default"))
        self.assertEqual([item.id for item in ordered], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
