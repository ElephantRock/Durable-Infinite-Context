from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import math
import re
from typing import Optional

from core.storage import MemoryStore
from rag.planner import (
    QueryPlan,
    _DAY_RE,
    _PREDICATE_PHRASES,
    _RECORDED_RE,
    _STOP,
    _TOKEN_RE,
)
from core.predicates import REGISTRY


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def _ngrams(token: str, n: int = 4) -> set[str]:
    if len(token) < n:
        return {token} if token else set()
    return {token[i : i + n] for i in range(len(token) - n + 1)}


@dataclass(frozen=True)
class ResolutionTrace:
    total_subjects: int
    token_posting_lookups: int
    ngram_posting_lookups: int
    posting_entries_examined: int
    broad_postings_skipped: int
    candidates_generated: int
    profiles_scored: int
    candidate_subject_ids: tuple[str, ...]

    @property
    def logical_work(self) -> int:
        return (
            self.token_posting_lookups
            + self.ngram_posting_lookups
            + self.posting_entries_examined
            + self.profiles_scored
        )


class SubjectProfileIndex:
    """Rebuildable query-resolution index over durable evidence/assertions.

    Build cost is intentionally excluded from per-query planner cost. The index is a
    materialization: it can be regenerated from MemoryStore and does not become a
    second source of truth.
    """

    def __init__(self, store: MemoryStore):
        profiles: dict[str, Counter[str]] = defaultdict(Counter)
        subject_predicates: dict[str, set[str]] = defaultdict(set)

        for assertion in store.assertions.values():
            subject_predicates[assertion.subject_id].add(assertion.predicate)
            for evidence_id in assertion.evidence_ids:
                evidence = store.evidence.get(evidence_id)
                if evidence is None:
                    continue
                profiles[assertion.subject_id].update(
                    t for t in _tokens(evidence.payload) if t not in _STOP
                )

        self.profiles = dict(profiles)
        self.subject_predicates = {k: set(v) for k, v in subject_predicates.items()}
        self.total_subjects = len(self.profiles)

        token_postings: dict[str, list[str]] = defaultdict(list)
        ngram_postings: dict[str, list[str]] = defaultdict(list)
        for subject_id, profile in self.profiles.items():
            for token in profile:
                token_postings[token].append(subject_id)
                for gram in _ngrams(token):
                    ngram_postings[gram].append(subject_id)

        self.token_postings = {k: tuple(sorted(v)) for k, v in token_postings.items()}
        self.ngram_postings = {k: tuple(sorted(v)) for k, v in ngram_postings.items()}

        n = max(self.total_subjects, 1)
        self.idf = {
            token: math.log((1 + n) / (1 + len(subjects))) + 1.0
            for token, subjects in self.token_postings.items()
        }


class ScalableQueryPlanner:
    """v0.5 planner with bounded indexed subject candidate generation.

    Unlike v0.4 QueryPlanner, this class never iterates over all subject profiles on
    the query path. Broad postings are not expanded; if no narrower evidence exists,
    the planner abstains instead of arbitrarily selecting one member of a large alias
    collision.
    """

    def __init__(
        self,
        profile_index: SubjectProfileIndex,
        *,
        candidate_limit: int = 32,
        broad_posting_limit: int = 128,
    ):
        self.index = profile_index
        self.candidate_limit = candidate_limit
        self.broad_posting_limit = broad_posting_limit

    def _intent(self, question: str) -> str:
        q = question.lower()
        if any(x in q for x in ("why do", "evidence", "source", "support")):
            return "provenance"
        if any(x in q for x in ("correction", "corrected", "wrong", "superseded", "moved from", "changed from")):
            return "relation_classification"
        if any(x in q for x in ("as of", "previous", "earlier", "before", "historical")):
            return "historical"
        return "current"

    def _predicate(self, question: str) -> Optional[str]:
        q = question.lower()
        matches: list[tuple[int, str]] = []
        for predicate, phrases in _PREDICATE_PHRASES.items():
            if predicate not in REGISTRY:
                continue
            best = max((len(phrase) for phrase in phrases if phrase in q), default=0)
            if best:
                matches.append((best, predicate))
        if not matches:
            return None
        matches.sort(key=lambda x: (-x[0], x[1]))
        if len(matches) > 1 and matches[0][0] == matches[1][0]:
            return None
        return matches[0][1]

    def _time(self, question: str) -> tuple[Optional[int], Optional[int]]:
        day = _DAY_RE.search(question)
        recorded = _RECORDED_RE.search(question)
        return (int(day.group(1)) if day else None, int(recorded.group(1)) if recorded else None)

    def _candidate_subjects(
        self,
        question: str,
        predicate: Optional[str],
    ) -> tuple[list[str], dict[str, float], ResolutionTrace, bool]:
        q_tokens = [t for t in _tokens(question) if t not in _STOP]
        candidate_scores: Counter[str] = Counter()
        token_lookups = 0
        ngram_lookups = 0
        entries_examined = 0
        broad_skipped = 0
        saw_broad = False

        def eligible(subject_id: str) -> bool:
            return predicate is None or predicate in self.index.subject_predicates.get(subject_id, set())

        # Exact token postings are the highest-precision candidate source. Common
        # aliases/predicate words are detected by posting cardinality and skipped.
        missing_tokens: list[str] = []
        for token in q_tokens:
            token_lookups += 1
            posting = self.index.token_postings.get(token, ())
            if not posting:
                missing_tokens.append(token)
                continue
            if len(posting) > self.broad_posting_limit:
                broad_skipped += 1
                saw_broad = True
                continue
            entries_examined += len(posting)
            weight = self.index.idf.get(token, 1.0)
            for subject_id in posting:
                if eligible(subject_id):
                    candidate_scores[subject_id] += 4.0 * weight

        # Fuzzy rescue is only used for query tokens with no exact posting. Four-
        # gram postings tolerate small alias/descriptor typos without scanning all
        # profiles. Broad grams are skipped by the same cardinality guard.
        for token in missing_tokens:
            for gram in _ngrams(token):
                ngram_lookups += 1
                posting = self.index.ngram_postings.get(gram, ())
                if not posting:
                    continue
                if len(posting) > self.broad_posting_limit:
                    broad_skipped += 1
                    saw_broad = True
                    continue
                entries_examined += len(posting)
                for subject_id in posting:
                    if eligible(subject_id):
                        candidate_scores[subject_id] += 1.0

        ranked = sorted(candidate_scores.items(), key=lambda x: (-x[1], x[0]))
        candidates = [subject_id for subject_id, _ in ranked[: self.candidate_limit]]
        trace = ResolutionTrace(
            total_subjects=self.index.total_subjects,
            token_posting_lookups=token_lookups,
            ngram_posting_lookups=ngram_lookups,
            posting_entries_examined=entries_examined,
            broad_postings_skipped=broad_skipped,
            candidates_generated=len(candidates),
            profiles_scored=len(candidates),
            candidate_subject_ids=tuple(candidates),
        )
        return candidates, dict(candidate_scores), trace, saw_broad

    def _subject(
        self,
        question: str,
        predicate: Optional[str],
    ) -> tuple[Optional[str], tuple[str, ...], float, ResolutionTrace]:
        q_tokens = [t for t in _tokens(question) if t not in _STOP]
        candidates, generation_scores, trace, saw_broad = self._candidate_subjects(question, predicate)
        if not candidates:
            # A broad alias with no discriminating narrow token is explicitly
            # unresolved. This prevents candidate truncation from manufacturing a
            # false winner.
            return None, (), 0.0, trace

        scored: list[tuple[float, str]] = []
        for subject_id in candidates:
            profile = self.index.profiles[subject_id]
            exact_score = 0.0
            for token in q_tokens:
                if token in profile:
                    exact_score += self.index.idf.get(token, 1.0) * (1.0 + math.log(profile[token]))
            # Candidate-generation score carries typo evidence into final ranking.
            score = exact_score + 0.1 * generation_scores.get(subject_id, 0.0)
            scored.append((score, subject_id))

        scored.sort(key=lambda x: (-x[0], x[1]))
        top_score = scored[0][0]
        tied = tuple(subject_id for score, subject_id in scored if abs(score - top_score) <= 1e-9)
        if len(tied) > 1:
            return None, tied, 0.0, trace

        # If the only signal was a broad posting, resolving would be unsafe. In
        # practice broad postings were skipped, so this protects future changes to
        # candidate generation as well.
        if top_score <= 0 and saw_broad:
            return None, (), 0.0, trace

        second = scored[1][0] if len(scored) > 1 else 0.0
        confidence = 1.0 if top_score <= 0 else max(0.0, min(1.0, (top_score - second) / top_score))
        return scored[0][1], (), confidence, trace

    def plan_with_trace(self, question: str) -> tuple[QueryPlan, ResolutionTrace]:
        predicate = self._predicate(question)
        valid_time, recorded_seq = self._time(question)
        subject_id, ambiguous, confidence, trace = self._subject(question, predicate)
        return QueryPlan(
            intent=self._intent(question),
            subject_id=subject_id,
            predicate=predicate,
            valid_time=valid_time,
            recorded_seq=recorded_seq,
            ambiguous_subject_ids=ambiguous,
            subject_confidence=confidence,
        ), trace

    def plan(self, question: str) -> QueryPlan:
        plan, _ = self.plan_with_trace(question)
        return plan
