from __future__ import annotations

from dataclasses import dataclass

from core.models import Assertion, EvidenceRecord, QueryCase, StateStatus
from simulator.world import Scenario


@dataclass(frozen=True)
class PlannerBenchmarkCase:
    scenario: Scenario
    query: QueryCase
    should_abstain: bool = False


def _case(
    sid: str,
    subject: str,
    alias: str,
    target_text: str,
    question: str,
    *,
    distractor_rows: list[tuple[str, str, int]],
    valid_from: int | None = None,
    valid_to: int | None = None,
    as_of_valid_time: int | None = None,
    expected_value: int = 42,
    should_abstain: bool = False,
) -> PlannerBenchmarkCase:
    target_eid = f"{sid}-target"
    evidence = [EvidenceRecord(target_eid, target_text, "source", recorded_seq=100000, source_event_time=valid_from)]
    assertions = [Assertion(
        id=f"{sid}-a-target",
        subject_id=subject,
        predicate="deadline",
        object_value=expected_value,
        recorded_seq=100000,
        valid_from=valid_from,
        valid_to=valid_to,
        evidence_ids=(target_eid,),
    )]

    for d, (dsubject, dtext, dvalue) in enumerate(distractor_rows):
        eid = f"{sid}-d{d:04d}"
        evidence.append(EvidenceRecord(eid, dtext, "source", recorded_seq=d + 1, source_event_time=valid_from))
        assertions.append(Assertion(
            id=f"{sid}-ad{d:04d}",
            subject_id=dsubject,
            predicate="deadline",
            object_value=dvalue,
            recorded_seq=d + 1,
            valid_from=valid_from,
            valid_to=valid_to,
            evidence_ids=(eid,),
        ))

    q = QueryCase(
        id=f"{sid}-q",
        scenario_type="planner_abstain" if should_abstain else "planner_resolvable",
        question_type="historical" if as_of_valid_time is not None else "current",
        subject_id=subject,
        predicate="deadline",
        as_of_valid_time=as_of_valid_time,
        as_of_recorded_seq=None,
        expected_status=StateStatus.RESOLVED,
        expected_value=expected_value,
        relevant_evidence_ids=(target_eid,),
        question_text=question,
    )
    scenario = Scenario(sid, evidence, assertions, [], [q])
    return PlannerBenchmarkCase(scenario, q, should_abstain)


def unique_alias_case(i: int, distractors: int) -> PlannerBenchmarkCase:
    sid = f"plan-unique-{distractors}-{i:04d}"
    subject = f"plan-unique-subject-{distractors}-{i:04d}"
    alias = f"Atlas-{i:04d}"
    distractor_rows = []
    for d in range(distractors):
        dalias = f"AtlasD-{i:04d}-{d:04d}"
        distractor_rows.append((
            f"plan-unique-distractor-{distractors}-{i:04d}-{d:04d}",
            f"{dalias} finance migration deadline is day 42.",
            42,
        ))
    return _case(
        sid,
        subject,
        alias,
        f"{alias} finance migration deadline is day 42.",
        f"What is {alias}'s current deadline?",
        distractor_rows=distractor_rows,
        expected_value=42,
    )


def contextual_collision_case(i: int, distractors: int) -> PlannerBenchmarkCase:
    sid = f"plan-context-{distractors}-{i:04d}"
    subject = f"plan-context-subject-{distractors}-{i:04d}"
    alias = "Orion"
    descriptor = f"finance-migration-{i:04d}"
    distractor_rows = []
    for d in range(distractors):
        ddesc = f"mobile-redesign-{i:04d}-{d:04d}"
        distractor_rows.append((
            f"plan-context-distractor-{distractors}-{i:04d}-{d:04d}",
            f"{alias} {ddesc} deadline is day 42.",
            42,
        ))
    return _case(
        sid,
        subject,
        alias,
        f"{alias} {descriptor} deadline is day 42.",
        f"What is {alias}'s deadline for {descriptor}?",
        distractor_rows=distractor_rows,
        expected_value=42,
    )


def irreducible_collision_case(i: int, distractors: int) -> PlannerBenchmarkCase:
    sid = f"plan-ambiguous-{distractors}-{i:04d}"
    subject = f"plan-ambiguous-subject-{distractors}-{i:04d}"
    alias = "Orion"
    distractor_rows = []
    for d in range(max(distractors, 1)):
        distractor_rows.append((
            f"plan-ambiguous-distractor-{distractors}-{i:04d}-{d:04d}",
            f"{alias} program deadline is day {10 + d}.",
            10 + d,
        ))
    return _case(
        sid,
        subject,
        alias,
        f"{alias} program deadline is day 42.",
        f"What is {alias}'s current deadline?",
        distractor_rows=distractor_rows,
        expected_value=42,
        should_abstain=True,
    )


def temporal_resolution_case(i: int, history_len: int) -> PlannerBenchmarkCase:
    sid = f"plan-time-{history_len}-{i:04d}"
    subject = f"plan-time-subject-{history_len}-{i:04d}"
    alias = f"Nova-{i:04d}"
    evidence = []
    assertions = []
    for t in range(1, history_len + 1):
        eid = f"{sid}-e{t:04d}"
        evidence.append(EvidenceRecord(
            eid,
            f"{alias} deadline update recorded in the project timeline.",
            "timeline",
            recorded_seq=t,
            source_event_time=t,
        ))
        assertions.append(Assertion(
            id=f"{sid}-a{t:04d}",
            subject_id=subject,
            predicate="deadline",
            object_value=t,
            recorded_seq=t,
            valid_from=t,
            valid_to=t,
            evidence_ids=(eid,),
        ))
    target_t = max(1, history_len // 3)
    q = QueryCase(
        id=f"{sid}-q",
        scenario_type="planner_temporal",
        question_type="historical",
        subject_id=subject,
        predicate="deadline",
        as_of_valid_time=target_t,
        as_of_recorded_seq=None,
        expected_status=StateStatus.RESOLVED,
        expected_value=target_t,
        relevant_evidence_ids=(f"{sid}-e{target_t:04d}",),
        question_text=f"What was {alias}'s deadline as of day {target_t}?",
    )
    scenario = Scenario(sid, evidence, assertions, [], [q])
    return PlannerBenchmarkCase(scenario, q, False)


def build_unique_suite(distractors: int, entities: int = 20) -> list[PlannerBenchmarkCase]:
    return [unique_alias_case(i, distractors) for i in range(entities)]


def build_contextual_suite(distractors: int, entities: int = 20) -> list[PlannerBenchmarkCase]:
    return [contextual_collision_case(i, distractors) for i in range(entities)]


def build_ambiguous_suite(distractors: int, entities: int = 20) -> list[PlannerBenchmarkCase]:
    return [irreducible_collision_case(i, distractors) for i in range(entities)]


def build_planner_temporal_suite(history_len: int, entities: int = 20) -> list[PlannerBenchmarkCase]:
    return [temporal_resolution_case(i, history_len) for i in range(entities)]
