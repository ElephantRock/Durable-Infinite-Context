from __future__ import annotations

from dataclasses import dataclass, asdict

from core.models import QueryCase


@dataclass
class RetrievalScore:
    mode: str
    cases: int = 0
    support_recall_sum: float = 0.0
    complete_support: int = 0
    any_support: int = 0
    candidates_considered: int = 0
    returned: int = 0

    def add(self, q: QueryCase, ids: list[str], considered: int) -> None:
        self.cases += 1
        expected = set(q.relevant_evidence_ids)
        got = set(ids)
        recall = len(expected & got) / max(len(expected), 1)
        self.support_recall_sum += recall
        self.complete_support += int(expected.issubset(got))
        self.any_support += int(bool(expected & got))
        self.candidates_considered += considered
        self.returned += len(ids)

    def to_dict(self) -> dict:
        d = asdict(self)
        n = max(self.cases, 1)
        d.update({
            "support_recall": self.support_recall_sum / n,
            "complete_support_rate": self.complete_support / n,
            "any_support_rate": self.any_support / n,
            "avg_candidates_considered": self.candidates_considered / n,
            "avg_returned": self.returned / n,
        })
        return d
