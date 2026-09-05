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


def _fragments(token: str) -> set[str]:
    return {part for part in re.split(r"[-_]+", token) if len(part) >= 3}


@dataclass(frozen=True)
class ResolutionTrace:
    total_subjects: int
    token_posting_lookups: int
    fragment_posting_lookups: int
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
            + self.fragment_posting_lookups
            + self.ngram_posting_lookups
            + self.posting_entries_examined
            + self.profiles_scored
        )


@dataclass
class MaintenanceTrace:
    subjects_refreshed: int = 0
    assertions_examined: int = 0
    evidence_refs_examined: int = 0
    evidence_records_examined: int = 0
    profile_tokens_examined: int = 0
    posting_additions: int = 0
    posting_removals: int = 0
    predicate_posting_additions: int = 0
    predicate_posting_removals: int = 0

    @property
    def logical_work(self) -> int:
        return (
            self.subjects_refreshed
            + self.assertions_examined
            + self.evidence_refs_examined
            + self.evidence_records_examined
            + self.profile_tokens_examined
            + self.posting_additions
            + self.posting_removals
            + self.predicate_posting_additions
            + self.predicate_posting_removals
        )

    def absorb(self, other: "MaintenanceTrace") -> None:
        self.subjects_refreshed += other.subjects_refreshed
        self.assertions_examined += other.assertions_examined
        self.evidence_refs_examined += other.evidence_refs_examined
        self.evidence_records_examined += other.evidence_records_examined
        self.profile_tokens_examined += other.profile_tokens_examined
        self.posting_additions += other.posting_additions
        self.posting_removals += other.posting_removals
        self.predicate_posting_additions += other.predicate_posting_additions
        self.predicate_posting_removals += other.predicate_posting_removals


class SubjectProfileIndex:
    """Rebuildable and incrementally maintainable query-resolution materialization.

    Canonical evidence/assertions remain authoritative. This index stores only derived
    lexical addressability state and can be regenerated from ``MemoryStore``.

    v0.6 changes the physical representation from immutable posting tuples to mutable
    unique-subject sets. Update cost no longer requires rebuilding a long posting just
    to add or remove one subject.

    IDF is computed lazily from current posting cardinality. Eagerly rewriting every
    token's IDF after total-subject cardinality changes would turn a local insertion or
    deletion into global vocabulary maintenance.
    """

    def __init__(self, store: MemoryStore):
        self.profiles: dict[str, Counter[str]] = {}
        self.subject_predicates: dict[str, set[str]] = {}
        self.subject_evidence_ids: dict[str, set[str]] = {}

        self.token_postings: dict[str, set[str]] = defaultdict(set)
        self.fragment_postings: dict[str, set[str]] = defaultdict(set)
        self.ngram_postings: dict[str, set[str]] = defaultdict(set)
        self.token_predicate_postings: dict[tuple[str, str], set[str]] = defaultdict(set)
        self.fragment_predicate_postings: dict[tuple[str, str], set[str]] = defaultdict(set)
        self.ngram_predicate_postings: dict[tuple[str, str], set[str]] = defaultdict(set)

        self.build_trace = MaintenanceTrace()
        for subject_id in store.subject_ids():
            self.build_trace.absorb(self.refresh_subject(store, subject_id))

    @property
    def total_subjects(self) -> int:
        return len(self.profiles)

    def idf_for_token(self, token: str) -> float:
        n = max(self.total_subjects, 1)
        df = len(self.token_postings.get(token, set()))
        return math.log((1 + n) / (1 + df)) + 1.0

    @staticmethod
    def _features(profile: Counter[str]) -> tuple[set[str], set[str], set[str]]:
        tokens = set(profile)
        fragments: set[str] = set()
        grams: set[str] = set()
        for token in tokens:
            fragments.update(_fragments(token))
            grams.update(_ngrams(token))
        return tokens, fragments, grams

    @staticmethod
    def _add_posting(mapping: dict, key, subject_id: str) -> int:
        posting = mapping.setdefault(key, set())
        if subject_id in posting:
            return 0
        posting.add(subject_id)
        return 1

    @staticmethod
    def _remove_posting(mapping: dict, key, subject_id: str) -> int:
        posting = mapping.get(key)
        if not posting or subject_id not in posting:
            return 0
        posting.remove(subject_id)
        if not posting:
            mapping.pop(key, None)
        return 1

    def _snapshot_subject(
        self,
        store: MemoryStore,
        subject_id: str,
        trace: MaintenanceTrace,
    ) -> tuple[Counter[str], set[str], set[str]]:
        assertions = store.assertions_for_subject(subject_id)
        trace.assertions_examined += len(assertions)

        predicates: set[str] = set()
        evidence_ids: set[str] = set()
        for assertion in assertions:
            predicates.add(assertion.predicate)
            trace.evidence_refs_examined += len(assertion.evidence_ids)
            evidence_ids.update(assertion.evidence_ids)

        profile: Counter[str] = Counter()
        for evidence_id in sorted(evidence_ids):
            evidence = store.evidence.get(evidence_id)
            if evidence is None:
                continue
            trace.evidence_records_examined += 1
            lexical = [t for t in _tokens(evidence.payload) if t not in _STOP]
            trace.profile_tokens_examined += len(lexical)
            profile.update(lexical)

        return profile, predicates, evidence_ids

    def refresh_subject(self, store: MemoryStore, subject_id: str) -> MaintenanceTrace:
        """Recompute one subject from canonical state and patch only changed features."""

        trace = MaintenanceTrace(subjects_refreshed=1)
        old_profile = self.profiles.get(subject_id, Counter())
        old_predicates = self.subject_predicates.get(subject_id, set())
        old_tokens, old_fragments, old_grams = self._features(old_profile)

        new_profile, new_predicates, new_evidence_ids = self._snapshot_subject(
            store, subject_id, trace
        )
        new_tokens, new_fragments, new_grams = self._features(new_profile)

        for feature in old_tokens - new_tokens:
            trace.posting_removals += self._remove_posting(
                self.token_postings, feature, subject_id
            )
        for feature in new_tokens - old_tokens:
            trace.posting_additions += self._add_posting(
                self.token_postings, feature, subject_id
            )

        for feature in old_fragments - new_fragments:
            trace.posting_removals += self._remove_posting(
                self.fragment_postings, feature, subject_id
            )
        for feature in new_fragments - old_fragments:
            trace.posting_additions += self._add_posting(
                self.fragment_postings, feature, subject_id
            )

        for feature in old_grams - new_grams:
            trace.posting_removals += self._remove_posting(
                self.ngram_postings, feature, subject_id
            )
        for feature in new_grams - old_grams:
            trace.posting_additions += self._add_posting(
                self.ngram_postings, feature, subject_id
            )

        old_token_predicates = {(f, p) for f in old_tokens for p in old_predicates}
        new_token_predicates = {(f, p) for f in new_tokens for p in new_predicates}
        for key in old_token_predicates - new_token_predicates:
            trace.predicate_posting_removals += self._remove_posting(
                self.token_predicate_postings, key, subject_id
            )
        for key in new_token_predicates - old_token_predicates:
            trace.predicate_posting_additions += self._add_posting(
                self.token_predicate_postings, key, subject_id
            )

        old_fragment_predicates = {(f, p) for f in old_fragments for p in old_predicates}
        new_fragment_predicates = {(f, p) for f in new_fragments for p in new_predicates}
        for key in old_fragment_predicates - new_fragment_predicates:
            trace.predicate_posting_removals += self._remove_posting(
                self.fragment_predicate_postings, key, subject_id
            )
        for key in new_fragment_predicates - old_fragment_predicates:
            trace.predicate_posting_additions += self._add_posting(
                self.fragment_predicate_postings, key, subject_id
            )

        old_ngram_predicates = {(f, p) for f in old_grams for p in old_predicates}
        new_ngram_predicates = {(f, p) for f in new_grams for p in new_predicates}
        for key in old_ngram_predicates - new_ngram_predicates:
            trace.predicate_posting_removals += self._remove_posting(
                self.ngram_predicate_postings, key, subject_id
            )
        for key in new_ngram_predicates - old_ngram_predicates:
            trace.predicate_posting_additions += self._add_posting(
                self.ngram_predicate_postings, key, subject_id
            )

        if new_profile:
            self.profiles[subject_id] = new_profile
        else:
            self.profiles.pop(subject_id, None)

        if new_predicates:
            self.subject_predicates[subject_id] = set(new_predicates)
        else:
            self.subject_predicates.pop(subject_id, None)

        if new_evidence_ids:
            self.subject_evidence_ids[subject_id] = set(new_evidence_ids)
        else:
            self.subject_evidence_ids.pop(subject_id, None)

        return trace

    def equivalent_to(self, other: "SubjectProfileIndex") -> bool:
        """Strong materialization parity check used by the v0.6 rebuild oracle."""

        return (
            self.profiles == other.profiles
            and self.subject_predicates == other.subject_predicates
            and self.subject_evidence_ids == other.subject_evidence_ids
            and dict(self.token_postings) == dict(other.token_postings)
            and dict(self.fragment_postings) == dict(other.fragment_postings)
            and dict(self.ngram_postings) == dict(other.ngram_postings)
            and dict(self.token_predicate_postings) == dict(other.token_predicate_postings)
            and dict(self.fragment_predicate_postings) == dict(other.fragment_predicate_postings)
            and dict(self.ngram_predicate_postings) == dict(other.ngram_predicate_postings)
        )

    def _token_set(self, token: str, predicate: Optional[str]) -> set[str]:
        if predicate is None:
            return self.token_postings.get(token, set())
        return self.token_predicate_postings.get((token, predicate), set())

    def _fragment_set(self, fragment: str, predicate: Optional[str]) -> set[str]:
        if predicate is None:
            return self.fragment_postings.get(fragment, set())
        return self.fragment_predicate_postings.get((fragment, predicate), set())

    def _ngram_set(self, gram: str, predicate: Optional[str]) -> set[str]:
        if predicate is None:
            return self.ngram_postings.get(gram, set())
        return self.ngram_predicate_postings.get((gram, predicate), set())

    # Cardinality access is O(1) on the mutable backing set. The planner checks this
    # before materializing an immutable posting, so broad postings are never copied.
    def token_posting_size(self, token: str, predicate: Optional[str]) -> int:
        return len(self._token_set(token, predicate))

    def fragment_posting_size(self, fragment: str, predicate: Optional[str]) -> int:
        return len(self._fragment_set(fragment, predicate))

    def ngram_posting_size(self, gram: str, predicate: Optional[str]) -> int:
        return len(self._ngram_set(gram, predicate))

    def token_posting(self, token: str, predicate: Optional[str]) -> frozenset[str]:
        return frozenset(self._token_set(token, predicate))

    def fragment_posting(self, fragment: str, predicate: Optional[str]) -> frozenset[str]:
        return frozenset(self._fragment_set(fragment, predicate))

    def ngram_posting(self, gram: str, predicate: Optional[str]) -> frozenset[str]:
        return frozenset(self._ngram_set(gram, predicate))


class ScalableQueryPlanner:
    """v0.5+ planner with bounded indexed subject candidate generation."""

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
        fragment_lookups = 0
        ngram_lookups = 0
        entries_examined = 0
        broad_skipped = 0
        saw_broad = False

        def consume(posting: frozenset[str], weight: float) -> None:
            nonlocal entries_examined
            entries_examined += len(posting)
            for subject_id in posting:
                candidate_scores[subject_id] += weight

        missing_tokens: list[str] = []
        for token in q_tokens:
            token_lookups += 1
            posting_size = self.index.token_posting_size(token, predicate)
            if posting_size == 0:
                missing_tokens.append(token)
                continue
            if posting_size > self.broad_posting_limit:
                broad_skipped += 1
                saw_broad = True
                continue
            consume(
                self.index.token_posting(token, predicate),
                4.0 * self.index.idf_for_token(token),
            )

        for token in missing_tokens:
            for fragment in _fragments(token):
                fragment_lookups += 1
                posting_size = self.index.fragment_posting_size(fragment, predicate)
                if posting_size == 0:
                    continue
                if posting_size > self.broad_posting_limit:
                    broad_skipped += 1
                    saw_broad = True
                    continue
                consume(self.index.fragment_posting(fragment, predicate), 3.0)

        for token in missing_tokens:
            for gram in _ngrams(token):
                ngram_lookups += 1
                posting_size = self.index.ngram_posting_size(gram, predicate)
                if posting_size == 0:
                    continue
                if posting_size > self.broad_posting_limit:
                    broad_skipped += 1
                    saw_broad = True
                    continue
                consume(self.index.ngram_posting(gram, predicate), 1.0)

        ranked = sorted(candidate_scores.items(), key=lambda x: (-x[1], x[0]))
        candidates = [subject_id for subject_id, _ in ranked[: self.candidate_limit]]
        trace = ResolutionTrace(
            total_subjects=self.index.total_subjects,
            token_posting_lookups=token_lookups,
            fragment_posting_lookups=fragment_lookups,
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
            return None, (), 0.0, trace

        scored: list[tuple[float, str]] = []
        for subject_id in candidates:
            profile = self.index.profiles[subject_id]
            exact_score = 0.0
            for token in q_tokens:
                if token in profile:
                    exact_score += self.index.idf_for_token(token) * (1.0 + math.log(profile[token]))
            score = exact_score + 0.1 * generation_scores.get(subject_id, 0.0)
            scored.append((score, subject_id))

        scored.sort(key=lambda x: (-x[0], x[1]))
        top_score = scored[0][0]
        tied = tuple(subject_id for score, subject_id in scored if abs(score - top_score) <= 1e-9)
        if len(tied) > 1:
            return None, tied, 0.0, trace

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
