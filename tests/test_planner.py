import unittest

from benchmark.evaluator import build_store
from rag.planned import PlannedRetriever
from rag.planner import QueryPlanner
from rag.retrieval import RetrievalIndex
from simulator.planner import (
    contextual_collision_case,
    irreducible_collision_case,
    temporal_resolution_case,
    unique_alias_case,
)


class PlannerTests(unittest.TestCase):
    def _planner_for(self, case):
        store = build_store([case.scenario])
        index = RetrievalIndex(store)
        return store, index, QueryPlanner(index)

    def test_unique_alias_resolves_without_querycase_identity(self):
        case = unique_alias_case(3, 20)
        _, _, planner = self._planner_for(case)
        plan = planner.plan(case.query.question_text)
        self.assertTrue(plan.resolved)
        self.assertEqual(plan.subject_id, case.query.subject_id)
        self.assertEqual(plan.predicate, "deadline")

    def test_contextual_qualifier_disambiguates_shared_alias(self):
        case = contextual_collision_case(4, 20)
        _, _, planner = self._planner_for(case)
        plan = planner.plan(case.query.question_text)
        self.assertTrue(plan.resolved)
        self.assertEqual(plan.subject_id, case.query.subject_id)

    def test_irreducible_alias_collision_abstains(self):
        case = irreducible_collision_case(5, 5)
        _, _, planner = self._planner_for(case)
        plan = planner.plan(case.query.question_text)
        self.assertFalse(plan.resolved)
        self.assertIsNone(plan.subject_id)
        self.assertGreaterEqual(len(plan.ambiguous_subject_ids), 2)

    def test_temporal_expression_is_inferred_and_retrieved(self):
        case = temporal_resolution_case(2, 16)
        _, index, planner = self._planner_for(case)
        plan = planner.plan(case.query.question_text)
        self.assertTrue(plan.resolved)
        self.assertEqual(plan.valid_time, case.query.as_of_valid_time)
        retriever = PlannedRetriever(index)
        ids, _ = retriever.search(
            query_id=case.query.id,
            question=case.query.question_text,
            plan=plan,
            budget=2,
        )
        self.assertIn(case.query.relevant_evidence_ids[0], ids)

    def test_predicate_synonym_due_date_maps_to_deadline(self):
        case = unique_alias_case(7, 3)
        _, _, planner = self._planner_for(case)
        alias = "Atlas-0007"
        plan = planner.plan(f"What is {alias}'s current due date?")
        self.assertEqual(plan.predicate, "deadline")
        self.assertEqual(plan.subject_id, case.query.subject_id)


if __name__ == "__main__":
    unittest.main()
