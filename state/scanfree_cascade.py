from __future__ import annotations

from core.models import Assertion, AssertionRelation, EvidenceRecord
from state.cascade import CascadeMaterialization, CascadeResult, StateKey, evidence_node
from state.dependencies import DependencyTrace


class ScanFreeCascadeMaintainer:
    """v0.8 replacement for the v0.7 mutation wrapper.

    v0.7's semantic invalidation traversal was local, but its wrapper identified
    newly-invalid nodes by scanning ``graph.invalid_nodes()`` before and after each
    mutation. That O(total-derived-nodes) discovery work was not represented in the
    benchmark trace. This replacement returns the exact affected IDs carried by the
    invalidation traversal itself, so operational affected-region discovery is local.
    """

    def __init__(self, materialization: CascadeMaterialization):
        self.materialization = materialization
        self.store = materialization.store
        self.graph = materialization.graph

    @staticmethod
    def _result(operation: str, trace: DependencyTrace) -> CascadeResult:
        return CascadeResult(
            operation,
            tuple(sorted(trace.invalidated_node_ids)),
            trace,
        )

    def upsert_evidence(self, item: EvidenceRecord) -> CascadeResult:
        self.store.add_evidence(item)
        trace = self.graph.invalidate_from([evidence_node(item.id)])
        return self._result("upsert_evidence", trace)

    def delete_evidence(self, evidence_id: str) -> CascadeResult:
        self.store.remove_evidence(evidence_id)
        trace = self.graph.invalidate_from([evidence_node(evidence_id)])
        return self._result("delete_evidence", trace)

    def upsert_assertion(self, item: Assertion) -> CascadeResult:
        previous = self.store.assertions.get(item.id)
        old_key = previous.key if previous is not None else None
        old_subject = previous.subject_id if previous is not None else None
        self.store.add_assertion(item)

        seeds: set[str] = set()
        for key in {k for k in (old_key, item.key) if k is not None}:
            seeds.add(self.materialization.ensure_state_chain(key)[0])

        profile_changed = (
            previous is None
            or previous.subject_id != item.subject_id
            or previous.predicate != item.predicate
            or previous.evidence_ids != item.evidence_ids
        )
        if profile_changed:
            for subject_id in {
                s for s in (old_subject, item.subject_id) if s is not None
            }:
                seeds.add(self.materialization.ensure_profile(subject_id))

        trace = self.graph.invalidate_nodes(seeds)
        return self._result("upsert_assertion", trace)

    def delete_assertion(self, assertion_id: str) -> CascadeResult:
        previous = self.store.assertions.get(assertion_id)
        if previous is None:
            return CascadeResult("delete_assertion", (), DependencyTrace())
        self.store.remove_assertion(assertion_id)
        seeds = {
            self.materialization.ensure_state_chain(previous.key)[0],
            self.materialization.ensure_profile(previous.subject_id),
        }
        trace = self.graph.invalidate_nodes(seeds)
        return self._result("delete_assertion", trace)

    def add_relation(self, relation: AssertionRelation) -> CascadeResult:
        self.store.add_relation(relation)
        keys: set[StateKey] = set()
        source = self.store.assertions.get(relation.source_assertion_id)
        target = self.store.assertions.get(relation.target_assertion_id)
        if source is not None:
            keys.add(source.key)
        if target is not None:
            keys.add(target.key)
        seeds = {self.materialization.ensure_state_chain(key)[0] for key in keys}
        trace = self.graph.invalidate_nodes(seeds)
        return self._result("add_relation", trace)
