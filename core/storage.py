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
    _assertions_by_key: dict[tuple[str, str, str], set[str]] = field(
        default_factory=lambda: defaultdict(set), repr=False
    )
    _assertions_by_subject: dict[str, set[str]] = field(
        default_factory=lambda: defaultdict(set), repr=False
    )
    _evidence_subject_refcounts: dict[str, dict[str, int]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(int)), repr=False
    )
    _relations_by_source: dict[str, list[AssertionRelation]] = field(
        default_factory=lambda: defaultdict(list), repr=False
    )

    def __post_init__(self) -> None:
        # The locality structures are rebuildable derivations of canonical public
        # fields. Rehydrate them unconditionally so constructing/restoring a store
        # with pre-populated assertions/relations cannot yield an empty stale index.
        self.rebuild_locality_indexes()

    def rebuild_locality_indexes(self) -> None:
        self._assertions_by_key = defaultdict(set)
        self._assertions_by_subject = defaultdict(set)
        self._evidence_subject_refcounts = defaultdict(lambda: defaultdict(int))
        self._relations_by_source = defaultdict(list)

        for item in self.assertions.values():
            self._add_assertion_indexes(item)
        for rel in self.relations:
            self._relations_by_source[rel.source_assertion_id].append(rel)

    def add_evidence(self, item: EvidenceRecord) -> None:
        # Evidence IDs are stable logical addresses. Reusing an ID is therefore an
        # in-place version replacement from the perspective of this prototype.
        self.evidence[item.id] = item

    def remove_evidence(self, evidence_id: str) -> EvidenceRecord | None:
        return self.evidence.pop(evidence_id, None)

    def _remove_assertion_indexes(self, item: Assertion) -> None:
        by_key = self._assertions_by_key.get(item.key)
        if by_key is not None:
            by_key.discard(item.id)
            if not by_key:
                self._assertions_by_key.pop(item.key, None)

        by_subject = self._assertions_by_subject.get(item.subject_id)
        if by_subject is not None:
            by_subject.discard(item.id)
            if not by_subject:
                self._assertions_by_subject.pop(item.subject_id, None)

        for evidence_id in item.evidence_ids:
            subject_counts = self._evidence_subject_refcounts.get(evidence_id)
            if subject_counts is None:
                continue
            count = subject_counts.get(item.subject_id, 0)
            if count <= 1:
                subject_counts.pop(item.subject_id, None)
            else:
                subject_counts[item.subject_id] = count - 1
            if not subject_counts:
                self._evidence_subject_refcounts.pop(evidence_id, None)

    def _add_assertion_indexes(self, item: Assertion) -> None:
        self._assertions_by_key[item.key].add(item.id)
        self._assertions_by_subject[item.subject_id].add(item.id)
        for evidence_id in item.evidence_ids:
            self._evidence_subject_refcounts[evidence_id][item.subject_id] += 1

    def add_assertion(self, item: Assertion) -> None:
        # Treat assertion IDs as stable logical addresses. Replacement updates all
        # locality indexes, avoiding duplicate/stale index entries when an assertion
        # changes predicate, subject, or evidence references.
        previous = self.assertions.get(item.id)
        if previous is not None:
            self._remove_assertion_indexes(previous)
        self.assertions[item.id] = item
        self._add_assertion_indexes(item)

    def remove_assertion(self, assertion_id: str) -> Assertion | None:
        item = self.assertions.pop(assertion_id, None)
        if item is None:
            return None
        self._remove_assertion_indexes(item)
        return item

    def add_relation(self, rel: AssertionRelation) -> None:
        self.relations.append(rel)
        self._relations_by_source[rel.source_assertion_id].append(rel)

    def assertions_for_key(self, key: tuple[str, str, str]) -> list[Assertion]:
        # Set-backed locality indexes need a deterministic secondary ordering key;
        # otherwise equal recorded_seq values can vary with Python hash seed.
        ids = self._assertions_by_key.get(key, set())
        return sorted(
            (self.assertions[aid] for aid in ids),
            key=lambda a: (a.recorded_seq, a.id),
        )

    def assertions_for_subject(self, subject_id: str) -> list[Assertion]:
        ids = self._assertions_by_subject.get(subject_id, set())
        return sorted((self.assertions[aid] for aid in ids), key=lambda a: (a.recorded_seq, a.id))

    def subject_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._assertions_by_subject))

    def subjects_for_evidence(self, evidence_id: str) -> tuple[str, ...]:
        # Direct dependency lookup: cost is proportional to the evidence item's
        # actual fan-out, not total assertion/evidence cardinality.
        return tuple(sorted(self._evidence_subject_refcounts.get(evidence_id, {})))

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
