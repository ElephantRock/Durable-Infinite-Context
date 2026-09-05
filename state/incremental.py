from __future__ import annotations

from dataclasses import dataclass

from core.models import Assertion, AssertionRelation, RelationType, StateCell, StateStatus
from core.predicates import REGISTRY
from core.storage import MemoryStore
from state.reconciliation import reconcile


@dataclass
class MaintenanceCost:
    state_reads: int = 0
    state_writes: int = 0
    assertion_reads: int = 0
    relation_reads: int = 0
    fallbacks: int = 0

    @property
    def logical_ops(self) -> int:
        return self.state_reads + self.state_writes + self.assertion_reads + self.relation_reads

    def add(self, other: "MaintenanceCost") -> None:
        self.state_reads += other.state_reads
        self.state_writes += other.state_writes
        self.assertion_reads += other.assertion_reads
        self.relation_reads += other.relation_reads
        self.fallbacks += other.fallbacks


def apply_incremental_current_state(
    store: MemoryStore,
    assertion: Assertion,
    new_relations: list[AssertionRelation],
) -> MaintenanceCost:
    """Incrementally maintain the current StateCell for common v0.2 cases.

    The fast path is O(1) for linear correction/supersession, repeated support,
    multi-valued additions, and the first unresolved conflict. Ambiguous cases
    fall back to full reconciliation; fallbacks are counted explicitly.
    """
    cost = MaintenanceCost()
    key = assertion.key
    schema = REGISTRY[key[1]]
    old = store.state.get(key)
    if old is not None:
        cost.state_reads += 1

    outgoing = [r for r in new_relations if r.source_assertion_id == assertion.id]
    cost.relation_reads += len(outgoing)

    if old is None:
        if schema.cardinality == "multi":
            cell = StateCell(key, [assertion.object_value], StateStatus.RESOLVED, [assertion.id])
        else:
            cell = StateCell(key, [assertion.object_value], StateStatus.RESOLVED, [assertion.id])
        store.state[key] = cell
        cost.state_writes += 1
        return cost

    # Multi-valued state is additive in the controlled prototype.
    if schema.cardinality == "multi":
        vals = list(old.operative_values)
        if assertion.object_value not in vals:
            vals.append(assertion.object_value)
        support = list(old.supporting_assertion_ids)
        support.append(assertion.id)
        store.state[key] = StateCell(
            key, vals, StateStatus.RESOLVED, support,
            historical_assertion_ids=list(old.historical_assertion_ids),
            version=old.version + 1,
        )
        cost.state_writes += 1
        return cost

    replacement = next((r for r in outgoing if r.relation in {RelationType.CORRECTS, RelationType.SUPERSEDES}), None)
    active_ids = set(old.supporting_assertion_ids) | set(old.competing_assertion_ids)
    if replacement is not None and replacement.target_assertion_id in active_ids:
        historical = list(dict.fromkeys(
            list(old.historical_assertion_ids)
            + list(old.supporting_assertion_ids)
            + list(old.competing_assertion_ids)
        ))
        store.state[key] = StateCell(
            key,
            [assertion.object_value],
            StateStatus.RESOLVED,
            [assertion.id],
            historical_assertion_ids=historical,
            version=old.version + 1,
        )
        cost.state_writes += 1
        return cost

    if old.status == StateStatus.RESOLVED and old.operative_values == [assertion.object_value]:
        support = list(old.supporting_assertion_ids) + [assertion.id]
        store.state[key] = StateCell(
            key,
            list(old.operative_values),
            StateStatus.RESOLVED,
            support,
            historical_assertion_ids=list(old.historical_assertion_ids),
            version=old.version + 1,
        )
        cost.state_writes += 1
        return cost

    if old.status == StateStatus.RESOLVED and old.operative_values != [assertion.object_value] and not replacement:
        competing = list(old.supporting_assertion_ids) + [assertion.id]
        store.state[key] = StateCell(
            key,
            [],
            StateStatus.CONTESTED,
            competing_assertion_ids=competing,
            historical_assertion_ids=list(old.historical_assertion_ids),
            version=old.version + 1,
        )
        cost.state_writes += 1
        return cost

    # Complex contested updates use the correctness-preserving slow path.
    assertions = store.assertions_for_key(key)
    rels = store.relations_for_assertions({a.id for a in assertions})
    cost.assertion_reads += len(assertions)
    cost.relation_reads += len(rels)
    cost.fallbacks += 1
    cell = reconcile(assertions, rels)
    cell.version = old.version + 1
    store.state[key] = cell
    cost.state_writes += 1
    return cost
