from __future__ import annotations

from core.models import Answer, QueryCase, StateStatus
from core.storage import MemoryStore
from state.reconciliation import classify_relation, reconcile


def answer_from_assertions(store: MemoryStore, query: QueryCase) -> Answer:
    key = (query.subject_id, query.predicate, "default")
    assertions = store.assertions_for_key(key)
    rels = store.relations_for_assertions({a.id for a in assertions})
    cell = reconcile(
        assertions,
        rels,
        valid_time=query.as_of_valid_time,
        recorded_seq=query.as_of_recorded_seq,
    )

    relation = None
    if query.question_type == "relation_classification" and len(assertions) >= 2:
        visible = [a for a in assertions if query.as_of_recorded_seq is None or a.recorded_seq <= query.as_of_recorded_seq]
        if len(visible) >= 2:
            relation = classify_relation(rels, visible[-1].id, visible[-2].id)

    value = cell.operative_values[0] if len(cell.operative_values) == 1 else (
        cell.operative_values if cell.operative_values else None
    )
    evidence = []
    for aid in cell.supporting_assertion_ids + cell.competing_assertion_ids:
        a = store.assertions[aid]
        evidence.extend(a.evidence_ids)
    return Answer(cell.status, value=value, relation=relation, evidence_ids=sorted(set(evidence)))


def answer_from_persistent_state(store: MemoryStore, query: QueryCase) -> Answer:
    # v0.1 keeps only current state materialized. Historical/knowledge-time queries
    # intentionally fall back to the assertion layer, which is part of the hypothesis.
    if query.as_of_valid_time is not None or query.as_of_recorded_seq is not None or query.question_type == "relation_classification":
        return answer_from_assertions(store, query)

    key = (query.subject_id, query.predicate, "default")
    cell = store.state.get(key)
    if cell is None:
        return Answer(StateStatus.UNKNOWN)
    value = cell.operative_values[0] if len(cell.operative_values) == 1 else (
        cell.operative_values if cell.operative_values else None
    )
    evidence = []
    for aid in cell.supporting_assertion_ids + cell.competing_assertion_ids:
        a = store.assertions[aid]
        evidence.extend(a.evidence_ids)
    return Answer(cell.status, value=value, evidence_ids=sorted(set(evidence)))


def refresh_current_state(store: MemoryStore, key: tuple[str, str, str]) -> None:
    assertions = store.assertions_for_key(key)
    rels = store.relations_for_assertions({a.id for a in assertions})
    cell = reconcile(assertions, rels)
    cell.version = store.state.get(key, cell).version + (1 if key in store.state else 0)
    store.state[key] = cell
