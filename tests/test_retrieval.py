import unittest

from benchmark.evaluator import build_store
from core.models import EvidenceRecord
from core.storage import MemoryStore
from rag.retrieval import RetrievalIndex, Retriever, coverage_satisfied
from simulator.retrieval import distractor_saturation_scenario, temporal_history_scenario
from simulator.world import correction_scenario, conflict_scenario


class RetrievalTests(unittest.TestCase):
    def test_identity_filter_recovers_target_under_saturation(self):
        s = distractor_saturation_scenario(0, 100)
        store = build_store([s])
        r = Retriever(RetrievalIndex(store))
        ids, _ = r.search(s.queries[0], mode="planned_multi_address", budget=1)
        self.assertEqual(ids, [s.queries[0].relevant_evidence_ids[0]])

    def test_temporal_filter_recovers_requested_time(self):
        s = temporal_history_scenario(0, 32)
        store = build_store([s])
        r = Retriever(RetrievalIndex(store))
        ids, _ = r.search(s.queries[0], mode="planned_multi_address", budget=2)
        self.assertIn(s.queries[0].relevant_evidence_ids[0], ids)

    def test_coverage_controller_expands_for_correction_relation(self):
        s = correction_scenario(0)
        q = next(q for q in s.queries if q.question_type == "relation_classification")
        store = build_store([s])
        r = Retriever(RetrievalIndex(store))
        ids1, _ = r.search(q, mode="planned_multi_address", budget=1)
        self.assertFalse(coverage_satisfied(store, q, ids1))
        ids, trace = r.adaptive_search(q, initial_budget=1, max_budget=4)
        self.assertTrue(trace.coverage_satisfied)
        self.assertGreaterEqual(len(ids), 2)

    def test_coverage_controller_expands_for_conflict(self):
        s = conflict_scenario(0)
        q = next(q for q in s.queries if q.question_type == "current")
        store = build_store([s])
        r = Retriever(RetrievalIndex(store))
        ids, trace = r.adaptive_search(q, initial_budget=1, max_budget=4)
        self.assertTrue(trace.coverage_satisfied)
        self.assertGreaterEqual(len(ids), 2)

    def test_empty_store_returns_no_hits(self):
        index = RetrievalIndex(MemoryStore())
        lexical, lexical_considered = index.lexical("anything", budget=1)
        semantic, semantic_considered = index.semantic("anything", budget=1)
        self.assertEqual(lexical, [])
        self.assertEqual(semantic, [])
        self.assertEqual(lexical_considered, 0)
        self.assertEqual(semantic_considered, 0)

    def test_concept_empty_corpus_returns_no_semantic_hits(self):
        store = MemoryStore()
        store.add_evidence(EvidenceRecord("e1", "project-0001", "source", 1))
        index = RetrievalIndex(store)
        semantic, considered = index.semantic("project-0001", budget=1)
        self.assertEqual(semantic, [])
        self.assertEqual(considered, 1)


if __name__ == "__main__":
    unittest.main()
