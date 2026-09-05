import unittest

from benchmark.evaluator import build_store
from core.models import Assertion, EvidenceRecord
from core.storage import MemoryStore
from rag.scalable_planner import ScalableQueryPlanner, SubjectProfileIndex
from simulator.scalable_planner import (
    build_ambiguous_cardinality_suite,
    build_contextual_cardinality_suite,
    build_unique_cardinality_suite,
)


class ScalablePlannerTests(unittest.TestCase):
    def _planner(self, suite):
        store = build_store([suite.scenario])
        return ScalableQueryPlanner(SubjectProfileIndex(store))

    def test_unique_alias_uses_bounded_candidate_set(self):
        suite = build_unique_cardinality_suite(1000, queries=1, noisy_alias=False)
        planner = self._planner(suite)
        q = suite.cases[0].query
        plan, trace = planner.plan_with_trace(q.question_text or "")
        self.assertEqual(plan.subject_id, q.subject_id)
        self.assertIn(q.subject_id, trace.candidate_subject_ids)
        self.assertLessEqual(trace.profiles_scored, 32)
        self.assertLess(trace.profiles_scored, trace.total_subjects)

    def test_noisy_alias_recovers_target_without_full_scan(self):
        suite = build_unique_cardinality_suite(1000, queries=1, noisy_alias=True)
        planner = self._planner(suite)
        q = suite.cases[0].query
        plan, trace = planner.plan_with_trace(q.question_text or "")
        self.assertEqual(plan.subject_id, q.subject_id)
        self.assertIn(q.subject_id, trace.candidate_subject_ids)
        self.assertLess(trace.logical_work, trace.total_subjects)

    def test_context_descriptor_resolves_shared_alias(self):
        suite = build_contextual_cardinality_suite(1000, queries=1, noisy_descriptor=False)
        planner = self._planner(suite)
        q = suite.cases[0].query
        plan, trace = planner.plan_with_trace(q.question_text or "")
        self.assertEqual(plan.subject_id, q.subject_id)
        self.assertLessEqual(trace.profiles_scored, 32)

    def test_noisy_context_descriptor_recovers_target(self):
        suite = build_contextual_cardinality_suite(1000, queries=1, noisy_descriptor=True)
        planner = self._planner(suite)
        q = suite.cases[0].query
        plan, trace = planner.plan_with_trace(q.question_text or "")
        self.assertEqual(plan.subject_id, q.subject_id)
        self.assertIn(q.subject_id, trace.candidate_subject_ids)

    def test_broad_irreducible_alias_abstains(self):
        suite = build_ambiguous_cardinality_suite(1000, queries=1)
        planner = self._planner(suite)
        q = suite.cases[0].query
        plan, trace = planner.plan_with_trace(q.question_text or "")
        self.assertIsNone(plan.subject_id)
        self.assertGreater(trace.broad_postings_skipped, 0)
        self.assertLess(trace.logical_work, trace.total_subjects)

    def test_shared_evidence_is_counted_once_per_subject(self):
        store = MemoryStore()
        store.add_evidence(EvidenceRecord("e1", "Orion alpha deadline record.", "src", 1))
        store.add_evidence(EvidenceRecord("e2", "Orion alpha deadline record.", "src", 2))
        # s1 has two assertions pointing to the same evidence. Assertion multiplicity
        # must not increase its lexical term frequency relative to s2.
        store.add_assertion(Assertion("a1", "s1", "deadline", 10, 1, evidence_ids=("e1",)))
        store.add_assertion(Assertion("a2", "s1", "deadline", 10, 2, evidence_ids=("e1",)))
        store.add_assertion(Assertion("b1", "s2", "deadline", 10, 1, evidence_ids=("e2",)))

        index = SubjectProfileIndex(store)
        self.assertEqual(index.profiles["s1"], index.profiles["s2"])
        plan = ScalableQueryPlanner(index).plan("What is Orion alpha's due date?")
        self.assertIsNone(plan.subject_id)
        self.assertEqual(set(plan.ambiguous_subject_ids), {"s1", "s2"})

    def test_ngram_posting_contains_each_subject_once(self):
        store = MemoryStore()
        store.add_evidence(EvidenceRecord("e1", "alphaone alphatwo deadline.", "src", 1))
        store.add_evidence(EvidenceRecord("e2", "alphathree deadline.", "src", 2))
        store.add_assertion(Assertion("a1", "s1", "deadline", 10, 1, evidence_ids=("e1",)))
        store.add_assertion(Assertion("a2", "s2", "deadline", 10, 2, evidence_ids=("e2",)))

        index = SubjectProfileIndex(store)
        self.assertEqual(index.ngram_posting("alph", "deadline"), frozenset({"s1", "s2"}))
        plan = ScalableQueryPlanner(index).plan("What is alphp's due date?")
        self.assertIsNone(plan.subject_id)
        self.assertEqual(set(plan.ambiguous_subject_ids), {"s1", "s2"})

    def test_predicate_filter_applies_before_broadness_cutoff(self):
        store = MemoryStore()
        for i in range(129):
            eid = f"w-e{i}"
            store.add_evidence(EvidenceRecord(eid, "Orion employment record.", "src", i + 1))
            store.add_assertion(Assertion(
                f"w-a{i}", f"worker-{i}", "works_at", "Acme", i + 1, evidence_ids=(eid,)
            ))

        store.add_evidence(EvidenceRecord("d-e", "Orion deadline record.", "src", 200))
        store.add_assertion(Assertion(
            "d-a", "deadline-target", "deadline", 42, 200, evidence_ids=("d-e",)
        ))

        planner = ScalableQueryPlanner(SubjectProfileIndex(store), broad_posting_limit=128)
        plan, trace = planner.plan_with_trace("What is Orion's current due date?")
        self.assertEqual(plan.subject_id, "deadline-target")
        self.assertLess(trace.logical_work, trace.total_subjects)


if __name__ == "__main__":
    unittest.main()
