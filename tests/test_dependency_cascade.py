import unittest

from core.models import Assertion, AssertionRelation, EvidenceRecord, RelationType
from core.storage import MemoryStore
from state.cascade import (
    CascadeMaintainer,
    CascadeMaterialization,
    clone_canonical_store,
    context_node,
    profile_node,
    state_node,
    support_node,
)
from state.dependencies import DependencyGraph, DerivationStatus


class DependencyGraphTests(unittest.TestCase):
    def test_invalidation_visits_only_reachable_subgraph(self):
        graph = DependencyGraph()
        graph.register_derived("d1", "layer", {"root"})
        graph.register_derived("d2", "layer", {"d1"})
        graph.register_derived("u1", "layer", {"other"})

        trace = graph.invalidate_from(["root"])

        self.assertEqual(graph.status_of("d1"), DerivationStatus.INVALID)
        self.assertEqual(graph.status_of("d2"), DerivationStatus.INVALID)
        self.assertEqual(graph.status_of("u1"), DerivationStatus.FRESH)
        self.assertEqual(trace.nodes_invalidated, 2)
        self.assertEqual(trace.edges_traversed, 2)

    def test_retirement_requires_leaf_to_root_order(self):
        graph = DependencyGraph()
        graph.register_derived("parent", "layer", {"canonical"})
        graph.register_derived("child", "layer", {"parent"})

        with self.assertRaises(ValueError):
            graph.remove_derived("parent")

        child_trace = graph.remove_derived("child")
        parent_trace = graph.remove_derived("parent")
        self.assertEqual(child_trace.nodes_retired, 1)
        self.assertEqual(parent_trace.nodes_retired, 1)
        self.assertEqual(graph.derived_nodes(), ())


class CascadeMaterializationTests(unittest.TestCase):
    def _materialization(self):
        store = MemoryStore()
        store.add_evidence(EvidenceRecord("e1", "Atlas Alpha deadline is day 42.", "src", 1))
        store.add_assertion(Assertion(
            "a1", "subject-1", "deadline", 42, 1, valid_from=1, evidence_ids=("e1",)
        ))
        materialization = CascadeMaterialization(store)
        return store, materialization, CascadeMaintainer(materialization)

    def test_bootstrap_nodes_are_fresh(self):
        _, materialization, _ = self._materialization()
        key = ("subject-1", "deadline", "default")
        for node_id in (
            profile_node("subject-1"),
            state_node(key),
            support_node(key),
            context_node(key),
        ):
            self.assertEqual(materialization.graph.status_of(node_id), DerivationStatus.FRESH)

    def test_evidence_change_invalidates_provenance_branch_not_state(self):
        store, materialization, maintainer = self._materialization()
        key = ("subject-1", "deadline", "default")

        result = maintainer.upsert_evidence(EvidenceRecord(
            "e1", "Nova Alpha deadline is day 42.", "src", 2
        ))

        self.assertEqual(
            set(result.invalidated_node_ids),
            {profile_node("subject-1"), support_node(key), context_node(key)},
        )
        self.assertEqual(materialization.graph.status_of(state_node(key)), DerivationStatus.FRESH)
        with self.assertRaises(RuntimeError):
            materialization.read_context(key)

        # Rebuild only the requested context proof. The unrelated addressability
        # profile remains invalid until a later demand or maintenance pass.
        materialization.rebuild([context_node(key)])
        self.assertEqual(materialization.graph.status_of(context_node(key)), DerivationStatus.FRESH)
        self.assertEqual(materialization.graph.status_of(support_node(key)), DerivationStatus.FRESH)
        self.assertEqual(materialization.graph.status_of(profile_node("subject-1")), DerivationStatus.INVALID)
        self.assertIn("Nova Alpha", materialization.read_context(key) or "")

        materialization.rebuild()
        oracle = CascadeMaterialization(clone_canonical_store(store))
        self.assertTrue(materialization.equivalent_to(oracle))

    def test_object_change_invalidates_state_chain_without_profile_rebuild(self):
        store, materialization, maintainer = self._materialization()
        key = ("subject-1", "deadline", "default")
        old = store.assertions["a1"]

        result = maintainer.upsert_assertion(Assertion(
            old.id,
            old.subject_id,
            old.predicate,
            43,
            old.recorded_seq,
            valid_from=old.valid_from,
            evidence_ids=old.evidence_ids,
        ))

        self.assertEqual(
            set(result.invalidated_node_ids),
            {state_node(key), support_node(key), context_node(key)},
        )
        self.assertEqual(materialization.graph.status_of(profile_node("subject-1")), DerivationStatus.FRESH)

        materialization.rebuild([context_node(key)])
        self.assertIn("VALUES=[43]", materialization.read_context(key) or "")
        oracle = CascadeMaterialization(clone_canonical_store(store))
        self.assertTrue(materialization.equivalent_to(oracle))

    def test_assertion_deletion_repairs_and_retires_all_affected_layers(self):
        store, materialization, maintainer = self._materialization()
        key = ("subject-1", "deadline", "default")

        result = maintainer.delete_assertion("a1")
        self.assertEqual(
            set(result.invalidated_node_ids),
            {
                profile_node("subject-1"),
                state_node(key),
                support_node(key),
                context_node(key),
            },
        )

        rebuild_trace = materialization.rebuild()
        self.assertNotIn(key, store.state)
        self.assertNotIn(key, materialization.supports)
        self.assertNotIn(key, materialization.contexts)
        self.assertNotIn("subject-1", materialization.index.profiles)
        self.assertEqual(rebuild_trace.nodes_retired, 4)
        for node_id in (
            profile_node("subject-1"),
            state_node(key),
            support_node(key),
            context_node(key),
        ):
            self.assertIsNone(materialization.graph.status_of(node_id))

        oracle = CascadeMaterialization(clone_canonical_store(store))
        self.assertTrue(materialization.equivalent_to(oracle))

    def test_correction_relation_reconciles_through_state_chain(self):
        store, materialization, maintainer = self._materialization()
        key = ("subject-1", "deadline", "default")
        store.add_evidence(EvidenceRecord("e2", "Correction: deadline is day 45.", "src", 2))

        maintainer.upsert_assertion(Assertion(
            "a2", "subject-1", "deadline", 45, 2, valid_from=1, evidence_ids=("e2",)
        ))
        maintainer.add_relation(AssertionRelation("a2", RelationType.CORRECTS, "a1"))

        self.assertEqual(materialization.graph.status_of(state_node(key)), DerivationStatus.INVALID)
        materialization.rebuild([context_node(key)])
        self.assertIn("VALUES=[45]", materialization.read_context(key) or "")
        oracle = CascadeMaterialization(clone_canonical_store(store))
        # Profile is separately invalid because a2 introduced an additional evidence
        # dependency; rebuild it before comparing the complete materialization.
        materialization.rebuild()
        self.assertTrue(materialization.equivalent_to(oracle))


if __name__ == "__main__":
    unittest.main()
