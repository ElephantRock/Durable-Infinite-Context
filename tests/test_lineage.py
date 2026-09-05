import unittest

from benchmark.evaluator import build_store
from simulator.world import correction_scenario


class LineageTests(unittest.TestCase):
    def test_state_support_reaches_evidence(self):
        s = correction_scenario(1)
        store = build_store([s])
        key = (s.queries[0].subject_id, s.queries[0].predicate, "default")
        cell = store.state[key]
        self.assertTrue(cell.supporting_assertion_ids)
        for aid in cell.supporting_assertion_ids:
            assertion = store.assertions[aid]
            self.assertTrue(assertion.evidence_ids)
            for eid in assertion.evidence_ids:
                self.assertIn(eid, store.evidence)


if __name__ == "__main__":
    unittest.main()
