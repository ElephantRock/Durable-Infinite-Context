import unittest

from core.storage import MemoryStore
from simulator.world import correction_scenario, transition_scenario, conflict_scenario
from state.incremental import apply_incremental_current_state


def ingest_incrementally(scenario):
    store = MemoryStore()
    rels_by_source = {}
    for r in scenario.relations:
        rels_by_source.setdefault(r.source_assertion_id, []).append(r)
    for e in scenario.evidence:
        store.add_evidence(e)
    total = 0
    for a in scenario.assertions:
        store.add_assertion(a)
        rs = rels_by_source.get(a.id, [])
        for r in rs:
            store.add_relation(r)
        total += apply_incremental_current_state(store, a, rs).logical_ops
    return store, total


class IncrementalTests(unittest.TestCase):
    def test_correction(self):
        s, _ = ingest_incrementally(correction_scenario(1))
        cell = next(iter(s.state.values()))
        self.assertEqual(cell.operative_values, [14])

    def test_transition(self):
        s, _ = ingest_incrementally(transition_scenario(1))
        cell = next(iter(s.state.values()))
        self.assertEqual(cell.operative_values, [14])

    def test_conflict(self):
        s, _ = ingest_incrementally(conflict_scenario(1))
        cell = next(iter(s.state.values()))
        self.assertEqual(cell.status.value, "contested")


if __name__ == "__main__":
    unittest.main()
