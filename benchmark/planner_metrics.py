from __future__ import annotations

from dataclasses import asdict, dataclass

from rag.planner import QueryPlan
from simulator.planner import PlannerBenchmarkCase


@dataclass
class PlannerScore:
    label: str
    cases: int = 0
    resolvable_cases: int = 0
    abstention_cases: int = 0
    subject_correct: int = 0
    predicate_correct: int = 0
    time_correct: int = 0
    intent_correct: int = 0
    exact_plan: int = 0
    correct_abstention: int = 0
    over_resolution: int = 0
    inferred_complete_support: int = 0
    oracle_complete_support: int = 0

    def add(
        self,
        case: PlannerBenchmarkCase,
        plan: QueryPlan,
        inferred_ids: list[str],
        oracle_ids: list[str],
    ) -> None:
        self.cases += 1
        q = case.query
        expected = set(q.relevant_evidence_ids)

        if case.should_abstain:
            self.abstention_cases += 1
            abstained = not plan.resolved
            self.correct_abstention += int(abstained)
            self.over_resolution += int(not abstained)
            self.exact_plan += int(abstained)
            return

        self.resolvable_cases += 1
        subject_ok = plan.subject_id == q.subject_id and not plan.ambiguous_subject_ids
        predicate_ok = plan.predicate == q.predicate
        time_ok = plan.valid_time == q.as_of_valid_time and plan.recorded_seq == q.as_of_recorded_seq
        expected_intent = "historical" if q.question_type in {"historical", "historical_belief"} else q.question_type
        intent_ok = plan.intent == expected_intent

        self.subject_correct += int(subject_ok)
        self.predicate_correct += int(predicate_ok)
        self.time_correct += int(time_ok)
        self.intent_correct += int(intent_ok)
        self.exact_plan += int(subject_ok and predicate_ok and time_ok and intent_ok and plan.resolved)
        self.inferred_complete_support += int(expected.issubset(set(inferred_ids)))
        self.oracle_complete_support += int(expected.issubset(set(oracle_ids)))

    def to_dict(self) -> dict:
        out = asdict(self)
        r = max(self.resolvable_cases, 1)
        a = max(self.abstention_cases, 1)
        n = max(self.cases, 1)
        out.update({
            "subject_accuracy": self.subject_correct / r,
            "predicate_accuracy": self.predicate_correct / r,
            "time_accuracy": self.time_correct / r,
            "intent_accuracy": self.intent_correct / r,
            "exact_plan_rate": self.exact_plan / n,
            "correct_abstention_rate": self.correct_abstention / a if self.abstention_cases else None,
            "over_resolution_rate": self.over_resolution / a if self.abstention_cases else None,
            "inferred_complete_support_rate": self.inferred_complete_support / r,
            "oracle_complete_support_rate": self.oracle_complete_support / r,
        })
        return out
