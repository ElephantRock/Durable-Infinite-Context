from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from core.models import Assertion, AssertionRelation, EvidenceRecord, StateCell
from core.storage import MemoryStore
from rag.scalable_planner import SubjectProfileIndex
from state.dependencies import DependencyGraph, DependencyTrace, DerivationStatus
from state.reconciliation import reconcile


StateKey = tuple[str, str, str]


def evidence_node(evidence_id: str) -> str:
    return f"evidence:{evidence_id}"


def assertion_node(assertion_id: str) -> str:
    return f"assertion:{assertion_id}"


def relation_node(relation: AssertionRelation) -> str:
    return (
        f"relation:{relation.source_assertion_id}:"
        f"{relation.relation.value}:{relation.target_assertion_id}"
    )


def state_node(key: StateKey) -> str:
    return f"state:{key[0]}:{key[1]}:{key[2]}"


def profile_node(subject_id: str) -> str:
    return f"profile:{subject_id}"


def support_node(key: StateKey) -> str:
    return f"support:{key[0]}:{key[1]}:{key[2]}"


def context_node(key: StateKey) -> str:
    return f"context:{key[0]}:{key[1]}:{key[2]}"


@dataclass(frozen=True)
class SupportSnapshot:
    key: StateKey
    status: str
    operative_values: tuple[Any, ...]
    assertion_ids: tuple[str, ...]
    evidence_payloads: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class CascadeResult:
    operation: str
    invalidated_node_ids: tuple[str, ...]
    trace: DependencyTrace


class CascadeMaterialization:
    """v0.7 layered rebuildable state with explicit dependency lifecycle.

    Canonical evidence/assertions/relations remain authoritative. Four rebuildable
    derived layers are maintained here:

    - current ``StateCell`` projections;
    - subject addressability profiles;
    - support snapshots that bind current state to source evidence;
    - compact context strings derived from those support snapshots.

    The dependency graph owns only invalid/fresh lifecycle and lineage; it does not
    become a second source of semantic truth.
    """

    def __init__(self, store: MemoryStore):
        self.store = store
        self.graph = DependencyGraph()
        self.index = SubjectProfileIndex(store)
        self.supports: dict[StateKey, SupportSnapshot] = {}
        self.contexts: dict[StateKey, str] = {}
        self._key_by_node: dict[str, StateKey] = {}
        self._subject_by_profile_node: dict[str, str] = {}
        self.build_trace = DependencyTrace(profile_work=self.index.build_trace.logical_work)

        # State is a derived projection. Bootstrap from canonical assertions rather
        # than trusting any pre-existing materialized cells.
        self.store.state.clear()
        self._bootstrap()

    def _all_keys(self) -> tuple[StateKey, ...]:
        return tuple(sorted({assertion.key for assertion in self.store.assertions.values()}))

    def _state_dependencies(self, key: StateKey, trace: DependencyTrace | None = None) -> set[str]:
        assertions = self.store.assertions_for_key(key)
        if trace is not None:
            trace.assertion_reads += len(assertions)
        ids = {a.id for a in assertions}
        relations = self.store.relations_for_assertions(ids)
        if trace is not None:
            trace.relation_reads += len(relations)
        return (
            {assertion_node(a.id) for a in assertions}
            | {relation_node(r) for r in relations}
        )

    def _profile_dependencies(self, subject_id: str, trace: DependencyTrace | None = None) -> set[str]:
        assertions = self.store.assertions_for_subject(subject_id)
        if trace is not None:
            trace.assertion_reads += len(assertions)
        dependencies = {assertion_node(a.id) for a in assertions}
        evidence_ids: set[str] = set()
        for assertion in assertions:
            evidence_ids.update(assertion.evidence_ids)
        if trace is not None:
            trace.evidence_reads += sum(1 for eid in evidence_ids if eid in self.store.evidence)
        dependencies.update(evidence_node(eid) for eid in evidence_ids)
        return dependencies

    def _active_assertion_ids(self, cell: StateCell | None) -> tuple[str, ...]:
        if cell is None:
            return ()
        return tuple(dict.fromkeys(
            list(cell.supporting_assertion_ids) + list(cell.competing_assertion_ids)
        ))

    def ensure_profile(self, subject_id: str) -> str:
        node_id = profile_node(subject_id)
        self._subject_by_profile_node[node_id] = subject_id
        if self.graph.status_of(node_id) is None:
            self.build_trace.absorb(self.graph.register_derived(
                node_id,
                "profile",
                self._profile_dependencies(subject_id),
            ))
        return node_id

    def ensure_state_chain(self, key: StateKey) -> tuple[str, str, str]:
        s_node = state_node(key)
        u_node = support_node(key)
        c_node = context_node(key)
        self._key_by_node[s_node] = key
        self._key_by_node[u_node] = key
        self._key_by_node[c_node] = key

        if self.graph.status_of(s_node) is None:
            self.build_trace.absorb(self.graph.register_derived(
                s_node,
                "state",
                self._state_dependencies(key),
            ))
        if self.graph.status_of(u_node) is None:
            self.build_trace.absorb(self.graph.register_derived(
                u_node,
                "support",
                {s_node},
            ))
        if self.graph.status_of(c_node) is None:
            self.build_trace.absorb(self.graph.register_derived(
                c_node,
                "context",
                {u_node},
            ))
        return s_node, u_node, c_node

    def _bootstrap(self) -> None:
        for subject_id in self.store.subject_ids():
            self.ensure_profile(subject_id)

        for key in self._all_keys():
            s_node, u_node, c_node = self.ensure_state_chain(key)
            self._rebuild_state(key, self.build_trace)
            self.graph.mark_fresh(s_node)
            self._rebuild_support(key, self.build_trace)
            self.graph.mark_fresh(u_node)
            self._rebuild_context(key, self.build_trace)
            self.graph.mark_fresh(c_node)

    def _rebuild_state(self, key: StateKey, trace: DependencyTrace) -> None:
        assertions = self.store.assertions_for_key(key)
        trace.assertion_reads += len(assertions)
        assertion_ids = {a.id for a in assertions}
        relations = self.store.relations_for_assertions(assertion_ids)
        trace.relation_reads += len(relations)

        if not assertions:
            self.store.state.pop(key, None)
        else:
            cell = reconcile(assertions, relations)
            cell.version = 1
            self.store.state[key] = cell
        trace.materialization_writes += 1
        trace.absorb(self.graph.register_derived(
            state_node(key),
            "state",
            {assertion_node(a.id) for a in assertions}
            | {relation_node(r) for r in relations},
            status=DerivationStatus.REBUILDING,
        ))

    def _rebuild_profile(self, subject_id: str, trace: DependencyTrace) -> None:
        profile_trace = self.index.refresh_subject(self.store, subject_id)
        trace.profile_work += profile_trace.logical_work
        trace.absorb(self.graph.register_derived(
            profile_node(subject_id),
            "profile",
            self._profile_dependencies(subject_id, trace),
            status=DerivationStatus.REBUILDING,
        ))

    def _rebuild_support(self, key: StateKey, trace: DependencyTrace) -> None:
        cell = self.store.state.get(key)
        trace.materialization_reads += 1
        if cell is None:
            self.supports.pop(key, None)
            dependencies = {state_node(key)}
        else:
            assertion_ids = self._active_assertion_ids(cell)
            evidence_payloads: list[tuple[str, str]] = []
            dependencies = {state_node(key)}
            for assertion_id in assertion_ids:
                assertion = self.store.assertions.get(assertion_id)
                if assertion is None:
                    continue
                trace.assertion_reads += 1
                dependencies.add(assertion_node(assertion.id))
                for eid in assertion.evidence_ids:
                    dependencies.add(evidence_node(eid))
                    evidence = self.store.evidence.get(eid)
                    if evidence is not None:
                        trace.evidence_reads += 1
                        evidence_payloads.append((eid, evidence.payload))
            self.supports[key] = SupportSnapshot(
                key=key,
                status=cell.status.value,
                operative_values=tuple(cell.operative_values),
                assertion_ids=tuple(assertion_ids),
                evidence_payloads=tuple(sorted(set(evidence_payloads))),
            )
        trace.materialization_writes += 1
        trace.absorb(self.graph.register_derived(
            support_node(key),
            "support",
            dependencies,
            status=DerivationStatus.REBUILDING,
        ))

    def _rebuild_context(self, key: StateKey, trace: DependencyTrace) -> None:
        support = self.supports.get(key)
        trace.materialization_reads += 1
        if support is None:
            self.contexts.pop(key, None)
        else:
            self.contexts[key] = (
                f"ENTITY={key[0]} PROPERTY={key[1]} STATUS={support.status} "
                f"VALUES={list(support.operative_values)!r} "
                f"ASSERTIONS={list(support.assertion_ids)!r} "
                f"EVIDENCE={list(support.evidence_payloads)!r}"
            )
        trace.materialization_writes += 1
        trace.absorb(self.graph.register_derived(
            context_node(key),
            "context",
            {support_node(key)},
            status=DerivationStatus.REBUILDING,
        ))

    def rebuild(self, target_node_ids: Iterable[str] | None = None) -> DependencyTrace:
        """Rebuild invalid targets, recursively repairing invalid dependencies first."""

        targets = tuple(target_node_ids) if target_node_ids is not None else self.graph.invalid_nodes()
        trace = DependencyTrace()
        visiting: set[str] = set()
        done: set[str] = set()

        def rebuild_node(node_id: str) -> None:
            if node_id in done:
                return
            if self.graph.status_of(node_id) != DerivationStatus.INVALID:
                done.add(node_id)
                return
            if node_id in visiting:
                raise RuntimeError(f"Dependency cycle detected at {node_id}")
            visiting.add(node_id)
            for dependency in self.graph.dependencies_of(node_id):
                if self.graph.status_of(dependency) == DerivationStatus.INVALID:
                    rebuild_node(dependency)

            self.graph.mark_rebuilding(node_id)
            kind = self.graph.kind_of(node_id)
            if kind == "state":
                self._rebuild_state(self._key_by_node[node_id], trace)
            elif kind == "profile":
                self._rebuild_profile(self._subject_by_profile_node[node_id], trace)
            elif kind == "support":
                self._rebuild_support(self._key_by_node[node_id], trace)
            elif kind == "context":
                self._rebuild_context(self._key_by_node[node_id], trace)
            else:
                raise KeyError(f"Unknown derived node kind for {node_id}: {kind}")

            self.graph.mark_fresh(node_id)
            trace.nodes_rebuilt += 1
            visiting.remove(node_id)
            done.add(node_id)

        for node_id in targets:
            rebuild_node(node_id)
        return trace

    def read_context(self, key: StateKey) -> str | None:
        node_id = context_node(key)
        status = self.graph.status_of(node_id)
        if status is not None and status != DerivationStatus.FRESH:
            raise RuntimeError(f"Context materialization is not fresh: {node_id} ({status.value})")
        return self.contexts.get(key)

    @staticmethod
    def _state_signature(store: MemoryStore) -> dict[StateKey, tuple]:
        out: dict[StateKey, tuple] = {}
        for key, cell in store.state.items():
            out[key] = (
                tuple(cell.operative_values),
                cell.status,
                tuple(cell.supporting_assertion_ids),
                tuple(cell.competing_assertion_ids),
                tuple(cell.historical_assertion_ids),
            )
        return out

    def equivalent_to(self, other: "CascadeMaterialization") -> bool:
        return (
            self._state_signature(self.store) == self._state_signature(other.store)
            and self.index.equivalent_to(other.index)
            and self.supports == other.supports
            and self.contexts == other.contexts
        )


def clone_canonical_store(store: MemoryStore) -> MemoryStore:
    """Clone authoritative fields while deliberately discarding derived state."""

    return MemoryStore(
        evidence=dict(store.evidence),
        assertions=dict(store.assertions),
        relations=list(store.relations),
    )


class CascadeMaintainer:
    """Apply canonical mutations, invalidate descendants, rebuild only on demand."""

    def __init__(self, materialization: CascadeMaterialization):
        self.materialization = materialization
        self.store = materialization.store
        self.graph = materialization.graph

    def _result(self, operation: str, before: set[str], trace: DependencyTrace) -> CascadeResult:
        after = set(self.graph.invalid_nodes())
        return CascadeResult(operation, tuple(sorted(after - before)), trace)

    def upsert_evidence(self, item: EvidenceRecord) -> CascadeResult:
        before = set(self.graph.invalid_nodes())
        self.store.add_evidence(item)
        trace = self.graph.invalidate_from([evidence_node(item.id)])
        return self._result("upsert_evidence", before, trace)

    def delete_evidence(self, evidence_id: str) -> CascadeResult:
        before = set(self.graph.invalid_nodes())
        self.store.remove_evidence(evidence_id)
        trace = self.graph.invalidate_from([evidence_node(evidence_id)])
        return self._result("delete_evidence", before, trace)

    def upsert_assertion(self, item: Assertion) -> CascadeResult:
        before_invalid = set(self.graph.invalid_nodes())
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
            for subject_id in {s for s in (old_subject, item.subject_id) if s is not None}:
                seeds.add(self.materialization.ensure_profile(subject_id))

        trace = self.graph.invalidate_nodes(seeds)
        return self._result("upsert_assertion", before_invalid, trace)

    def delete_assertion(self, assertion_id: str) -> CascadeResult:
        before_invalid = set(self.graph.invalid_nodes())
        previous = self.store.assertions.get(assertion_id)
        if previous is None:
            return CascadeResult("delete_assertion", (), DependencyTrace())
        self.store.remove_assertion(assertion_id)
        seeds = {
            self.materialization.ensure_state_chain(previous.key)[0],
            self.materialization.ensure_profile(previous.subject_id),
        }
        trace = self.graph.invalidate_nodes(seeds)
        return self._result("delete_assertion", before_invalid, trace)

    def add_relation(self, relation: AssertionRelation) -> CascadeResult:
        before_invalid = set(self.graph.invalid_nodes())
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
        return self._result("add_relation", before_invalid, trace)
