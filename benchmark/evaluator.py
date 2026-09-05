from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Callable

from core.models import Answer, QueryCase, StateStatus
from core.storage import MemoryStore
from simulator.world import Scenario
from state.projection import answer_from_assertions, answer_from_persistent_state, refresh_current_state


@dataclass
class Score:
    architecture: str
    total: int = 0
    value_correct: int = 0
    status_correct: int = 0
    exact_correct: int = 0
    overclaims: int = 0
    relation_total: int = 0
    relation_correct: int = 0
    provenance_total: int = 0
    provenance_correct: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        n = max(self.total, 1)
        d.update({
            "value_accuracy": self.value_correct / n,
            "status_accuracy": self.status_correct / n,
            "relation_accuracy": self.relation_correct / max(self.relation_total, 1),
            "provenance_accuracy": self.provenance_correct / max(self.provenance_total, 1),
            "exact_accuracy": self.exact_correct / n,
            "overclaim_rate": self.overclaims / n,
        })
        return d


def build_store(scenarios: list[Scenario]) -> MemoryStore:
    store = MemoryStore()
    touched = set()
    for s in scenarios:
        for e in s.evidence:
            store.add_evidence(e)
        for a in s.assertions:
            store.add_assertion(a)
            touched.add(a.key)
        for r in s.relations:
            store.add_relation(r)
    for key in touched:
        refresh_current_state(store, key)
    return store


def _score_answer(score: Score, q: QueryCase, a: Answer) -> None:
    score.total += 1
    value_ok = a.value == q.expected_value
    status_ok = a.status == q.expected_status
    relation_ok = True
    provenance_ok = True

    if q.expected_relation is not None:
        score.relation_total += 1
        relation_ok = a.relation == q.expected_relation
        score.relation_correct += int(relation_ok)

    if q.question_type == "provenance":
        score.provenance_total += 1
        expected_evidence = set(q.relevant_evidence_ids)
        actual_evidence = set(a.evidence_ids)
        provenance_ok = expected_evidence.issubset(actual_evidence)
        score.provenance_correct += int(provenance_ok)

    score.value_correct += int(value_ok)
    score.status_correct += int(status_ok)
    score.exact_correct += int(value_ok and status_ok and relation_ok and provenance_ok)
    if q.expected_status in {StateStatus.CONTESTED, StateStatus.UNKNOWN} and a.status == StateStatus.RESOLVED:
        score.overclaims += 1


def evaluate(
    scenarios: list[Scenario],
    architectures: dict[str, Callable[[MemoryStore, QueryCase], Answer]],
) -> list[dict]:
    store = build_store(scenarios)
    scores = {name: Score(name) for name in architectures}
    for s in scenarios:
        for q in s.queries:
            for name, fn in architectures.items():
                ans = fn(store, q)
                _score_answer(scores[name], q, ans)
    return [scores[name].to_dict() for name in architectures]


def default_state_architectures():
    from rag.baselines import EvidenceRecencyControl
    control = EvidenceRecencyControl()
    return {
        control.name: control.answer,
        "assertions_on_demand": answer_from_assertions,
        "persistent_state": answer_from_persistent_state,
    }
