import unittest

from benchmark.costs import assertions_on_demand_costed, persistent_state_costed
from benchmark.evaluator import build_store
from simulator.scaling import build_scaling_suite


class CostTests(unittest.TestCase):
    def test_current_state_read_cost_diverges_with_history(self):
        scenarios = build_scaling_suite(16, entities=1)
        store = build_store(scenarios)
        q = next(q for q in scenarios[0].queries if q.question_type == "current")
        a = assertions_on_demand_costed(store, q)
        s = persistent_state_costed(store, q)
        self.assertEqual(a.answer.value, 16)
        self.assertEqual(s.answer.value, 16)
        self.assertGreater(a.cost.logical_reads, s.cost.logical_reads)
        self.assertEqual(s.cost.state_cells_read, 1)

    def test_historical_query_falls_back_to_assertions(self):
        scenarios = build_scaling_suite(16, entities=1)
        store = build_store(scenarios)
        q = next(q for q in scenarios[0].queries if q.question_type == "historical")
        a = assertions_on_demand_costed(store, q)
        s = persistent_state_costed(store, q)
        self.assertEqual(a.answer.value, 1)
        self.assertEqual(s.answer.value, 1)
        self.assertEqual(a.cost.logical_reads, s.cost.logical_reads)


if __name__ == "__main__":
    unittest.main()
