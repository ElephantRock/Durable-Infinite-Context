from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from core.models import Assertion, AssertionRelation, EvidenceRecord, StateCell


@dataclass
class MemoryStore:
    evidence: dict[str, EvidenceRecord] = field(default_factory=dict)
    assertions: dict[str, Assertion] = field(default_factory=dict)
    relations: list[AssertionRelation] = field(default_factory=list)
    state: dict[tuple[str, str, str], StateCell] = field(default_factory=dict)
    _assertions_by_key: dict[tuple[str, str, str], list[str]] = field(default_factory=lambda: defaultdict(list), repr=False)
    _relations_by_source: dict[str, list[AssertionRelation]] = field(default_factory=lambda: defaultdict(list), repr=False)

    def add_evidence(self, item: EvidenceRecord) -> None:
        self.evidence[item.id] = item

    def add_assertion(self, item: Assertion) -> None:
        self.assertions[item.id] = item
        self._assertions_by_key[item.key].append(item.id)

    def add_relation(self, rel: AssertionRelation) -> None:
        self.relations.append(rel)
        self._relations_by_source[rel.source_assertion_id].append(rel)

    def assertions_for_key(self, key: tuple[str, str, str]) -> list[Assertion]:
        # The logical store is indexed by state key. This avoids conflating the
        # architecture experiment with an avoidable full-store scan.
        ids = self._assertions_by_key.get(key, [])
        return sorted((self.assertions[aid] for aid in ids), key=lambda a: a.recorded_seq)

    def relations_from(self, assertion_id: str) -> list[AssertionRelation]:
        return list(self._relations_by_source.get(assertion_id, []))

    def relations_to(self, assertion_id: str) -> list[AssertionRelation]:
        return [r for r in self.relations if r.target_assertion_id == assertion_id]

    def relations_for_assertions(self, assertion_ids: set[str]) -> list[AssertionRelation]:
        out: list[AssertionRelation] = []
        for aid in assertion_ids:
            for rel in self._relations_by_source.get(aid, []):
                if rel.target_assertion_id in assertion_ids:
                    out.append(rel)
        return out
