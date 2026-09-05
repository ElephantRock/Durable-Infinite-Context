import unittest

from benchmark.evaluator import build_store
from simulator.world import conflict_scenario, correction_scenario, transition_scenario
from state.projection import answer_from_assertions
from core.models import StateStatus


class ReconciliationTests(unittest.TestCase):
    def test_correction_current_and_past_belief(self):
        s = correction_scenario(1)
        store = build_store([s])
        current = answer_from_assertions(store, s.queries[0])
        past_belief = answer_from_assertions(store, s.queries[1])
        now_about_past = answer_from_assertions(store, s.queries[2])
        relation = answer_from_assertions(store, s.queries[3])
        self.assertEqual(current.value, 14)
        self.assertEqual(past_belief.value, 10)
        self.assertEqual(now_about_past.value, 14)
        self.assertEqual(relation.relation, "correction")

    def test_transition_preserves_history(self):
        s = transition_scenario(2)
        store = build_store([s])
        current = answer_from_assertions(store, s.queries[0])
        historical = answer_from_assertions(store, s.queries[1])
        relation = answer_from_assertions(store, s.queries[2])
        self.assertEqual(current.value, 14)
        self.assertEqual(historical.value, 10)
        self.assertEqual(relation.relation, "transition")

    def test_conflict_is_contested(self):
        s = conflict_scenario(3)
        store = build_store([s])
        current = answer_from_assertions(store, s.queries[0])
        self.assertEqual(current.status, StateStatus.CONTESTED)
        self.assertIsNone(current.value)


if __name__ == "__main__":
    unittest.main()
