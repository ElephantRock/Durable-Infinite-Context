import unittest

from benchmark.evaluator import build_store
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


if __name__ == "__main__":
    unittest.main()
