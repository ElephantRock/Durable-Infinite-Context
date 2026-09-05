from __future__ import annotations

from typing import Protocol

from core.models import Answer, QueryCase, StateStatus
from core.storage import MemoryStore


class ArchitectureAdapter(Protocol):
    name: str

    def answer(self, store: MemoryStore, query: QueryCase) -> Answer: ...


class EvidenceRecencyControl:
    """A deliberately simple evidence-only control, NOT the strong agentic-RAG baseline.

    It uses oracle-linked assertions only to recover values from the same evidence while
    ignoring persistent reconciliation semantics. Its purpose is a smoke control.
    """

    name = "evidence_recency_control"

    def answer(self, store: MemoryStore, query: QueryCase) -> Answer:
        key = (query.subject_id, query.predicate, "default")
        items = store.assertions_for_key(key)
        items = [a for a in items if query.as_of_recorded_seq is None or a.recorded_seq <= query.as_of_recorded_seq]
        if query.as_of_valid_time is not None:
            items = [
                a for a in items
                if (a.valid_from is None or a.valid_from <= query.as_of_valid_time)
                and (a.valid_to is None or query.as_of_valid_time <= a.valid_to)
            ]
        if not items:
            return Answer(StateStatus.UNKNOWN)
        latest = items[-1]
        relation = None
        # Intentionally does not infer correction vs transition.
        return Answer(StateStatus.RESOLVED, latest.object_value, relation, list(latest.evidence_ids))


class AgenticRAGAdapter:
    """Integration seam for the strong baseline.

    Supply a callable agent_fn(store, query) -> Answer using the same evidence and
    budgets as the state architecture. The MFP repo does not fake an LLM agent.
    """

    name = "agentic_hybrid_rag"

    def __init__(self, agent_fn):
        self.agent_fn = agent_fn

    def answer(self, store: MemoryStore, query: QueryCase) -> Answer:
        return self.agent_fn(store, query)
