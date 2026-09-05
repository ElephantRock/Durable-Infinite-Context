from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import math
import re
from typing import Optional

from core.predicates import REGISTRY
from rag.retrieval import RetrievalIndex


_TOKEN_RE = re.compile(r"[a-zA-Z0-9_-]+")
_DAY_RE = re.compile(r"\b(?:as\s+of\s+)?day\s+(\d+)\b", re.IGNORECASE)
_RECORDED_RE = re.compile(r"\b(?:record(?:ed)?(?:\s+sequence|\s+seq)?|seq)\s+(\d+)\b", re.IGNORECASE)

_STOP = {
    "a", "an", "and", "are", "as", "at", "be", "by", "current", "did",
    "do", "does", "for", "from", "has", "have", "how", "in", "is", "it",
    "latest", "of", "on", "or", "the", "to", "was", "what", "when", "where",
    "which", "who", "why", "with",
}

_PREDICATE_PHRASES = {
    "deadline": ("deadline", "due date", "due", "target date"),
    "project_status": ("project status", "status"),
    "works_at": ("works at", "employer", "employment"),
    "lives_in": ("lives in", "resides in", "residence"),
    "depends_on": ("depends on", "dependency", "dependencies"),
    "approved": ("approved", "approver", "approval"),
    "value": ("value", "amount"),
}


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


@dataclass(frozen=True)
class QueryPlan:
    intent: str
    subject_id: Optional[str]
    predicate: Optional[str]
    valid_time: Optional[int]
    recorded_seq: Optional[int]
    ambiguous_subject_ids: tuple[str, ...] = ()
    subject_confidence: float = 0.0

    @property
    def resolved(self) -> bool:
        return self.subject_id is not None and self.predicate is not None and not self.ambiguous_subject_ids


class QueryPlanner:
    """Deterministic non-oracle planner for the controlled v0.4 experiment.

    It may inspect the memory index built from oracle assertions, but it never reads
    hidden QueryCase fields. Entity resolution is inferred from the user-visible
    question and per-subject lexical profiles observed in memory.
    """

    def __init__(self, index: RetrievalIndex):
        self.index = index
        profiles: dict[str, Counter[str]] = defaultdict(Counter)
        for doc in index.docs:
            toks = [t for t in _tokens(doc.text) if t not in _STOP]
            for subject_id in doc.subject_ids:
                profiles[subject_id].update(toks)
        self.profiles = dict(profiles)

        df: Counter[str] = Counter()
        for profile in self.profiles.values():
            df.update(profile.keys())
        n = max(len(self.profiles), 1)
        self.idf = {tok: math.log((1 + n) / (1 + freq)) + 1.0 for tok, freq in df.items()}

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

    def _subject(self, question: str, predicate: Optional[str]) -> tuple[Optional[str], tuple[str, ...], float]:
        q_tokens = [t for t in _tokens(question) if t not in _STOP]
        if not q_tokens:
            return None, (), 0.0

        allowed_subjects: Optional[set[str]] = None
        if predicate is not None:
            evidence_ids = self.index.predicate.get(predicate, set())
            allowed_subjects = {
                sid
                for eid in evidence_ids
                for sid in self.index.by_id[eid].subject_ids
            }

        scores: list[tuple[float, str]] = []
        for subject_id, profile in self.profiles.items():
            if allowed_subjects is not None and subject_id not in allowed_subjects:
                continue
            score = 0.0
            for tok in q_tokens:
                if tok in profile:
                    score += self.idf.get(tok, 1.0) * (1.0 + math.log(profile[tok]))
            if score > 0:
                scores.append((score, subject_id))

        if not scores:
            return None, (), 0.0
        scores.sort(key=lambda x: (-x[0], x[1]))
        top = scores[0][0]
        tied = tuple(subject for score, subject in scores if abs(score - top) <= 1e-9)
        if len(tied) > 1:
            return None, tied, 0.0
        second = scores[1][0] if len(scores) > 1 else 0.0
        confidence = 1.0 if top <= 0 else max(0.0, min(1.0, (top - second) / top))
        return scores[0][1], (), confidence

    def plan(self, question: str) -> QueryPlan:
        predicate = self._predicate(question)
        valid_time, recorded_seq = self._time(question)
        subject_id, ambiguous, confidence = self._subject(question, predicate)
        return QueryPlan(
            intent=self._intent(question),
            subject_id=subject_id,
            predicate=predicate,
            valid_time=valid_time,
            recorded_seq=recorded_seq,
            ambiguous_subject_ids=ambiguous,
            subject_confidence=confidence,
        )
