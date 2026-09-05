from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from collections import defaultdict
from typing import Iterable, Optional

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from core.models import QueryCase
from core.storage import MemoryStore


# Small, explicit normalizer for the controlled benchmark. This is not presented
# as a production embedding model; it provides a deterministic paraphrase-aware
# similarity channel so retrieval-mode ablations are reproducible offline.
_CONCEPTS = {
    "launch": "release", "released": "release", "launching": "release",
    "deadline": "due", "due": "due", "target": "due",
    "delayed": "delay", "slipped": "delay", "slip": "delay", "late": "delay",
    "vendor": "supplier", "supplier": "supplier",
    "certification": "approval", "certified": "approval", "approval": "approval",
    "moved": "changed", "changed": "changed", "revised": "changed",
    "wrong": "correction", "incorrect": "correction", "correction": "correction",
    "reports": "report", "reported": "report", "says": "report",
    "depends": "dependency", "dependency": "dependency",
}

_TOKEN_RE = re.compile(r"[a-zA-Z0-9_-]+")
_ENTITYISH_RE = re.compile(r"^(?:project|company|person|service|vendor|target|distractor|alias)[-_].+|^[a-z]+-\d{3,}$")


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def concept_normalize(text: str) -> str:
    out = []
    for tok in _tokens(text):
        # Exclude structured entity identifiers from the semantic/concept channel.
        # Identity is intentionally tested as a separate address dimension.
        if _ENTITYISH_RE.match(tok):
            continue
        out.append(_CONCEPTS.get(tok, tok))
    return " ".join(out)


@dataclass(frozen=True)
class RetrievalDoc:
    evidence_id: str
    text: str
    subject_ids: tuple[str, ...]
    predicates: tuple[str, ...]
    valid_froms: tuple[int, ...]
    valid_intervals: tuple[tuple[Optional[int], Optional[int]], ...]
    recorded_seq: int
    source_event_time: Optional[int]


@dataclass
class RetrievalHit:
    evidence_id: str
    score: float
    channels: set[str] = field(default_factory=set)


@dataclass
class RetrievalTrace:
    architecture: str
    query_id: str
    budget: int
    candidates_considered: int
    returned_ids: list[str]
    channel_calls: list[str]
    rounds: int = 1
    coverage_satisfied: Optional[bool] = None


class RetrievalIndex:
    def __init__(self, store: MemoryStore):
        self.store = store
        by_evidence: dict[str, dict[str, set]] = defaultdict(lambda: {
            "subjects": set(), "predicates": set(), "valid_froms": set(), "valid_intervals": set()
        })
        for a in store.assertions.values():
            for eid in a.evidence_ids:
                by_evidence[eid]["subjects"].add(a.subject_id)
                by_evidence[eid]["predicates"].add(a.predicate)
                if a.valid_from is not None:
                    by_evidence[eid]["valid_froms"].add(a.valid_from)
                by_evidence[eid]["valid_intervals"].add((a.valid_from, a.valid_to))

        self.docs: list[RetrievalDoc] = []
        for e in sorted(store.evidence.values(), key=lambda x: (x.recorded_seq, x.id)):
            m = by_evidence[e.id]
            self.docs.append(RetrievalDoc(
                evidence_id=e.id,
                text=e.payload,
                subject_ids=tuple(sorted(m["subjects"])),
                predicates=tuple(sorted(m["predicates"])),
                valid_froms=tuple(sorted(m["valid_froms"])),
                valid_intervals=tuple(sorted(m["valid_intervals"], key=lambda x: ((-10**18 if x[0] is None else x[0]), (10**18 if x[1] is None else x[1])))),
                recorded_seq=e.recorded_seq,
                source_event_time=e.source_event_time,
            ))
        self.by_id = {d.evidence_id: d for d in self.docs}
        self.doc_pos = {d.evidence_id: i for i, d in enumerate(self.docs)}

        self.identity: dict[str, set[str]] = defaultdict(set)
        self.predicate: dict[str, set[str]] = defaultdict(set)
        self.token_index: dict[str, set[str]] = defaultdict(set)
        for d in self.docs:
            for sid in d.subject_ids:
                self.identity[sid].add(d.evidence_id)
            for p in d.predicates:
                self.predicate[p].add(d.evidence_id)
            for tok in set(_tokens(d.text)):
                self.token_index[tok].add(d.evidence_id)

        # TfidfVectorizer rejects an empty vocabulary. Keep a private sentinel in
        # matrix-only representations so an empty store, blank payloads, or a
        # concept channel containing only stripped entity identifiers produce an
        # ordinary no-hit result instead of failing during index construction.
        texts = [d.text if _tokens(d.text) else "__empty__" for d in self.docs] or ["__empty__"]
        concept_texts = [concept_normalize(d.text) or "__empty__" for d in self.docs] or ["__empty__"]
        self.lex_vectorizer = TfidfVectorizer(lowercase=True, token_pattern=r"(?u)\b[\w-]+\b", ngram_range=(1, 2))
        self.lex_matrix = self.lex_vectorizer.fit_transform(texts)
        self.concept_vectorizer = TfidfVectorizer(lowercase=True, token_pattern=r"(?u)\b[\w-]+\b", ngram_range=(1, 2))
        self.concept_matrix = self.concept_vectorizer.fit_transform(concept_texts)

    def _score_matrix(self, query: str, *, concept: bool = False, allowed: Optional[set[str]] = None) -> tuple[dict[str, float], int]:
        vec = self.concept_vectorizer if concept else self.lex_vectorizer
        mat = self.concept_matrix if concept else self.lex_matrix
        qtext = concept_normalize(query) if concept else query
        qv = vec.transform([qtext])
        if allowed is None:
            positions = list(range(len(self.docs)))
        else:
            positions = sorted(self.doc_pos[eid] for eid in allowed)
        if not positions:
            return {}, 0
        scores = cosine_similarity(qv, mat[positions])[0]
        out = {
            self.docs[pos].evidence_id: float(score)
            for pos, score in zip(positions, scores)
            if score > 0
        }
        return out, len(positions)

    def _rank(self, scores: dict[str, float], budget: int) -> list[RetrievalHit]:
        scored = sorted(scores.items(), key=lambda x: (-x[1], x[0]))
        return [RetrievalHit(eid, score) for eid, score in scored[:budget]]

    def semantic(self, query: str, budget: int, allowed: Optional[set[str]] = None) -> tuple[list[RetrievalHit], int]:
        scores, considered = self._score_matrix(query, concept=True, allowed=allowed)
        hits = self._rank(scores, budget)
        for h in hits:
            h.channels.add("semantic")
        return hits, considered

    def lexical(self, query: str, budget: int, allowed: Optional[set[str]] = None) -> tuple[list[RetrievalHit], int]:
        scores, considered = self._score_matrix(query, concept=False, allowed=allowed)
        hits = self._rank(scores, budget)
        for h in hits:
            h.channels.add("lexical")
        return hits, considered

    def hard_filter(
        self,
        *,
        subject_id: Optional[str] = None,
        predicate: Optional[str] = None,
        valid_time: Optional[int] = None,
        recorded_seq: Optional[int] = None,
    ) -> set[str]:
        allowed = set(self.by_id)
        if subject_id is not None:
            allowed &= self.identity.get(subject_id, set())
        if predicate is not None:
            allowed &= self.predicate.get(predicate, set())
        if recorded_seq is not None:
            allowed = {eid for eid in allowed if self.by_id[eid].recorded_seq <= recorded_seq}
        if valid_time is not None:
            # Oracle assertions provide valid-time metadata in this isolation test.
            # A document is eligible only if at least one linked assertion is active
            # at the requested valid time.
            def active(d):
                if not d.valid_intervals:
                    return True
                return any(
                    (start is None or start <= valid_time) and (end is None or valid_time <= end)
                    for start, end in d.valid_intervals
                )
            allowed = {eid for eid in allowed if active(self.by_id[eid])}
        return allowed

    def hybrid(self, query: str, budget: int, allowed: Optional[set[str]] = None) -> tuple[list[RetrievalHit], int]:
        sem, considered_sem = self._score_matrix(query, concept=True, allowed=allowed)
        lex, considered_lex = self._score_matrix(query, concept=False, allowed=allowed)
        ids = set(sem) | set(lex)
        scores = {eid: 0.55 * sem.get(eid, 0.0) + 0.45 * lex.get(eid, 0.0) for eid in ids}
        hits = self._rank(scores, budget)
        for h in hits:
            if h.evidence_id in sem:
                h.channels.add("semantic")
            if h.evidence_id in lex:
                h.channels.add("lexical")
        return hits, max(considered_sem, considered_lex)


def query_text(q: QueryCase) -> str:
    # The natural-language channel does not automatically receive the resolved entity
    # ID. When question_text is supplied, only the planned multi-address path can use
    # q.subject_id as a structured identity constraint.
    if q.question_text:
        return q.question_text
    parts = [q.subject_id, q.predicate.replace("_", " ")]
    if q.question_type == "current":
        parts += ["current latest"]
    elif q.question_type in {"historical", "historical_belief"}:
        parts += ["historical previous"]
    elif q.question_type == "relation_classification":
        parts += ["correction changed transition previous current"]
    elif q.question_type == "provenance":
        parts += ["source evidence support"]
    if q.as_of_valid_time is not None:
        parts += [f"day {q.as_of_valid_time}"]
    return " ".join(parts)


class Retriever:
    def __init__(self, index: RetrievalIndex):
        self.index = index

    def search(self, q: QueryCase, *, mode: str, budget: int) -> tuple[list[str], RetrievalTrace]:
        qt = query_text(q)
        channels: list[str] = []
        if mode == "semantic_only":
            hits, considered = self.index.semantic(qt, budget)
            channels.append("semantic")
        elif mode == "lexical_only":
            hits, considered = self.index.lexical(qt, budget)
            channels.append("lexical")
        elif mode == "hybrid_text":
            hits, considered = self.index.hybrid(qt, budget)
            channels += ["semantic", "lexical"]
        elif mode == "planned_multi_address":
            allowed = self.index.hard_filter(
                subject_id=q.subject_id,
                predicate=q.predicate,
                valid_time=q.as_of_valid_time,
                recorded_seq=q.as_of_recorded_seq,
            )
            hits, considered = self.index.hybrid(qt, budget, allowed=allowed)
            # If text ranking has zero overlap, deterministic structured addressability
            # still returns the constrained region ordered by recency.
            hit_ids = {h.evidence_id for h in hits}
            if len(hits) < budget:
                remaining = sorted(
                    (self.index.by_id[eid] for eid in allowed if eid not in hit_ids),
                    key=lambda d: (-d.recorded_seq, d.evidence_id),
                )
                hits.extend(RetrievalHit(d.evidence_id, 0.0, {"identity", "predicate", "time"}) for d in remaining[:budget-len(hits)])
            channels += ["identity", "predicate", "time", "semantic", "lexical"]
        else:
            raise ValueError(f"Unknown retrieval mode: {mode}")

        ids = [h.evidence_id for h in hits[:budget]]
        return ids, RetrievalTrace(mode, q.id, budget, considered, ids, channels)

    def adaptive_search(
        self,
        q: QueryCase,
        *,
        initial_budget: int = 1,
        max_budget: int = 16,
    ) -> tuple[list[str], RetrievalTrace]:
        budget = initial_budget
        rounds = 0
        last_ids: list[str] = []
        considered = 0
        while True:
            rounds += 1
            ids, trace = self.search(q, mode="planned_multi_address", budget=budget)
            last_ids = ids
            considered = max(considered, trace.candidates_considered)
            if coverage_satisfied(self.index.store, q, ids):
                trace.rounds = rounds
                trace.coverage_satisfied = True
                return ids, trace
            if budget >= max_budget:
                trace.rounds = rounds
                trace.coverage_satisfied = False
                return ids, trace
            budget = min(max_budget, budget * 2)


def coverage_satisfied(store: MemoryStore, q: QueryCase, evidence_ids: Iterable[str]) -> bool:
    ev = set(evidence_ids)
    key = (q.subject_id, q.predicate, "default")
    assertions = [a for a in store.assertions_for_key(key) if set(a.evidence_ids) & ev]
    if not assertions:
        return False

    if q.question_type == "relation_classification":
        aids = {a.id for a in assertions}
        rels = store.relations_for_assertions(aids)
        return any(r.relation.value in {"corrects", "supersedes"} for r in rels)

    cell = store.state.get(key)
    if cell is not None and cell.status.value == "contested":
        return len({repr(a.object_value) for a in assertions}) >= 2

    if q.question_type == "provenance":
        return len(assertions) >= 1

    return len(assertions) >= 1
