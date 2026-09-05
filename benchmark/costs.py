from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from core.models import Answer, QueryCase, StateStatus
from core.storage import MemoryStore
from state.reconciliation import classify_relation, reconcile


@dataclass
class LogicalCost:
    state_cells_read: int = 0
    assertions_read: int = 0
    relation_edges_read: int = 0
    evidence_records_read: int = 0
    context_items: int = 0
    context_chars: int = 0

    @property
    def logical_reads(self) -> int:
        return self.state_cells_read + self.assertions_read + self.relation_edges_read + self.evidence_records_read

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["logical_reads"] = self.logical_reads
        return d


@dataclass
class CostedAnswer:
    answer: Answer
    cost: LogicalCost


def _answer_value(cell) -> Any:
    if len(cell.operative_values) == 1:
        return cell.operative_values[0]
    return cell.operative_values if cell.operative_values else None


def _context_cost(answer: Answer, query: QueryCase, evidence_payloads: list[str]) -> tuple[int, int]:
    # v0.2 uses a deterministic logical context serialization. We count logical
    # items and characters rather than tokenizer-specific tokens.
    items = 1  # state/result line
    chars = len(str(answer.value)) + len(answer.status.value) + 16
    if answer.relation:
        items += 1
        chars += len(answer.relation) + 12
    if query.question_type == "provenance":
        items += len(evidence_payloads)
        chars += sum(len(p) for p in evidence_payloads)
    return items, chars


def assertions_on_demand_costed(store: MemoryStore, query: QueryCase) -> CostedAnswer:
    cost = LogicalCost()
    key = (query.subject_id, query.predicate, "default")
    assertions = store.assertions_for_key(key)
    cost.assertions_read += len(assertions)
    rels = store.relations_for_assertions({a.id for a in assertions})
    cost.relation_edges_read += len(rels)

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

    evidence_ids: list[str] = []
    payloads: list[str] = []
    if query.question_type == "provenance":
        for aid in cell.supporting_assertion_ids + cell.competing_assertion_ids:
            evidence_ids.extend(store.assertions[aid].evidence_ids)
        evidence_ids = sorted(set(evidence_ids))
        cost.evidence_records_read += len(evidence_ids)
        payloads = [store.evidence[eid].payload for eid in evidence_ids]

    ans = Answer(cell.status, _answer_value(cell), relation, evidence_ids)
    cost.context_items, cost.context_chars = _context_cost(ans, query, payloads)
    return CostedAnswer(ans, cost)


def persistent_state_costed(store: MemoryStore, query: QueryCase) -> CostedAnswer:
    # Current-state and provenance reads use the materialized cell. Historical and
    # relation-classification queries intentionally descend to assertions because
    # v0.1 materializes only current state.
    if query.as_of_valid_time is not None or query.as_of_recorded_seq is not None or query.question_type == "relation_classification":
        return assertions_on_demand_costed(store, query)

    cost = LogicalCost(state_cells_read=1)
    key = (query.subject_id, query.predicate, "default")
    cell = store.state.get(key)
    if cell is None:
        ans = Answer(StateStatus.UNKNOWN)
        cost.context_items, cost.context_chars = _context_cost(ans, query, [])
        return CostedAnswer(ans, cost)

    evidence_ids: list[str] = []
    payloads: list[str] = []
    if query.question_type == "provenance":
        support_ids = cell.supporting_assertion_ids + cell.competing_assertion_ids
        cost.assertions_read += len(support_ids)
        for aid in support_ids:
            evidence_ids.extend(store.assertions[aid].evidence_ids)
        evidence_ids = sorted(set(evidence_ids))
        cost.evidence_records_read += len(evidence_ids)
        payloads = [store.evidence[eid].payload for eid in evidence_ids]

    ans = Answer(cell.status, _answer_value(cell), evidence_ids=evidence_ids)
    cost.context_items, cost.context_chars = _context_cost(ans, query, payloads)
    return CostedAnswer(ans, cost)
