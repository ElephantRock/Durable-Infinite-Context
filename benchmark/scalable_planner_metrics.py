from __future__ import annotations

from dataclasses import asdict, dataclass

from core.models import QueryCase
from rag.planner import QueryPlan
from rag.scalable_planner import ResolutionTrace


@dataclass
class ScalablePlannerScore:
    label: str
    entity_count: int
    cases: int = 0
    resolvable_cases: int = 0
    abstention_cases: int = 0
    exact_plan: int = 0
    correct_abstention: int = 0
    over_resolution: int = 0
    candidate_recall: int = 0
    total_candidates: int = 0
    total_profiles_scored: int = 0
    total_posting_entries_examined: int = 0
    total_posting_lookups: int = 0
    total_logical_work: int = 0

    def add(
        self,
        q: QueryCase,
        plan: QueryPlan,
        trace: ResolutionTrace,
        *,
        should_abstain: bool,
    ) -> None:
        self.cases += 1
        self.abstention_cases += int(should_abstain)
        self.resolvable_cases += int(not should_abstain)
        self.total_candidates += trace.candidates_generated
        self.total_profiles_scored += trace.profiles_scored
        self.total_posting_entries_examined += trace.posting_entries_examined
        self.total_posting_lookups += trace.token_posting_lookups + trace.ngram_posting_lookups
        self.total_logical_work += trace.logical_work

        if should_abstain:
            abstained = plan.subject_id is None
            self.correct_abstention += int(abstained)
            self.over_resolution += int(not abstained)
            self.exact_plan += int(abstained)
            return

        target_in_candidates = q.subject_id in trace.candidate_subject_ids
        self.candidate_recall += int(target_in_candidates)
        exact = (
            plan.subject_id == q.subject_id
            and plan.predicate == q.predicate
            and plan.valid_time == q.as_of_valid_time
            and plan.recorded_seq == q.as_of_recorded_seq
            and not plan.ambiguous_subject_ids
        )
        self.exact_plan += int(exact)

    def to_dict(self) -> dict:
        d = asdict(self)
        n = max(self.cases, 1)
        r = max(self.resolvable_cases, 1)
        a = max(self.abstention_cases, 1)
        d.update({
            "exact_plan_rate": self.exact_plan / n,
            "candidate_recall_rate": self.candidate_recall / r if self.resolvable_cases else None,
            "correct_abstention_rate": self.correct_abstention / a if self.abstention_cases else None,
            "over_resolution_rate": self.over_resolution / a if self.abstention_cases else None,
            "avg_candidates_generated": self.total_candidates / n,
            "avg_profiles_scored": self.total_profiles_scored / n,
            "avg_posting_entries_examined": self.total_posting_entries_examined / n,
            "avg_posting_lookups": self.total_posting_lookups / n,
            "avg_logical_work": self.total_logical_work / n,
            "full_scan_profiles_per_query": self.entity_count,
            "profile_scan_fraction": (self.total_profiles_scored / n) / max(self.entity_count, 1),
            "logical_work_fraction_vs_full_scan": (self.total_logical_work / n) / max(self.entity_count, 1),
        })
        return d
