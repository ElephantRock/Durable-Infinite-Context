from __future__ import annotations

from rag.planner import QueryPlan
from rag.retrieval import RetrievalHit, RetrievalIndex, RetrievalTrace


class PlannedRetriever:
    """Apply a non-oracle QueryPlan to the existing multi-address index."""

    def __init__(self, index: RetrievalIndex):
        self.index = index

    def search(
        self,
        *,
        query_id: str,
        question: str,
        plan: QueryPlan,
        budget: int,
    ) -> tuple[list[str], RetrievalTrace]:
        if not plan.resolved:
            trace = RetrievalTrace(
                "inferred_multi_address",
                query_id,
                budget,
                0,
                [],
                ["planner_abstain"],
                coverage_satisfied=False,
            )
            return [], trace

        allowed = self.index.hard_filter(
            subject_id=plan.subject_id,
            predicate=plan.predicate,
            valid_time=plan.valid_time,
            recorded_seq=plan.recorded_seq,
        )
        hits, considered = self.index.hybrid(question, budget, allowed=allowed)
        hit_ids = {h.evidence_id for h in hits}
        if len(hits) < budget:
            remaining = sorted(
                (self.index.by_id[eid] for eid in allowed if eid not in hit_ids),
                key=lambda d: (-d.recorded_seq, d.evidence_id),
            )
            hits.extend(
                RetrievalHit(d.evidence_id, 0.0, {"identity", "predicate", "time"})
                for d in remaining[: budget - len(hits)]
            )

        ids = [h.evidence_id for h in hits[:budget]]
        trace = RetrievalTrace(
            "inferred_multi_address",
            query_id,
            budget,
            considered,
            ids,
            ["inferred_identity", "inferred_predicate", "inferred_time", "semantic", "lexical"],
        )
        return ids, trace
