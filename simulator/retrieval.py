from __future__ import annotations

from core.models import Assertion, EvidenceRecord, QueryCase, StateStatus
from simulator.world import Scenario


def distractor_saturation_scenario(i: int, distractors: int) -> Scenario:
    sid = f"ret-sat-{distractors}-{i:04d}"
    subject = f"target-{distractors}-{i:04d}"
    alias = "Orion"
    # User-visible text intentionally shares the same alias and semantics across all
    # candidates. Only the structured identity address can distinguish the target.
    target_text = f"Certification review changed {alias} deadline to day 42 after the supplier approval slipped."
    evidence = [EvidenceRecord(f"{sid}-target", target_text, "source", recorded_seq=100000, source_event_time=42)]
    assertions = [Assertion(
        id=f"{sid}-a-target", subject_id=subject, predicate="deadline", object_value=42,
        recorded_seq=100000, valid_from=42, evidence_ids=(f"{sid}-target",),
    )]

    for d in range(distractors):
        ds = f"distractor-{distractors}-{i:04d}-{d:05d}"
        eid = f"{sid}-d{d:05d}"
        text = f"Certification review changed {alias} deadline to day 42 after the supplier approval slipped."
        evidence.append(EvidenceRecord(eid, text, "source", recorded_seq=d + 1, source_event_time=42))
        assertions.append(Assertion(
            id=f"{sid}-ad{d:05d}", subject_id=ds, predicate="deadline", object_value=42,
            recorded_seq=d + 1, valid_from=42, evidence_ids=(eid,),
        ))

    q = QueryCase(
        id=f"{sid}-q", scenario_type="retrieval_saturation", question_type="current",
        subject_id=subject, predicate="deadline", as_of_valid_time=None, as_of_recorded_seq=None,
        expected_status=StateStatus.RESOLVED, expected_value=42,
        relevant_evidence_ids=(f"{sid}-target",),
        question_text=f"What is {alias}'s current deadline after the supplier certification delay?",
    )
    return Scenario(sid, evidence, assertions, [], [q])


def temporal_history_scenario(i: int, history_len: int) -> Scenario:
    sid = f"ret-time-{history_len}-{i:04d}"
    subject = f"project-time-{history_len}-{i:04d}"
    alias = "Orion"
    evidence = []
    assertions = []
    for t in range(1, history_len + 1):
        eid = f"{sid}-e{t:04d}"
        # Payloads are intentionally textually indistinguishable in time. Temporal
        # metadata, not lexical coincidence, must resolve the requested interval.
        evidence.append(EvidenceRecord(
            eid, f"{alias} deadline update recorded in the project timeline.",
            "timeline", recorded_seq=t, source_event_time=t,
        ))
        assertions.append(Assertion(
            id=f"{sid}-a{t:04d}", subject_id=subject, predicate="deadline", object_value=t,
            recorded_seq=t, valid_from=t, valid_to=t, evidence_ids=(eid,),
        ))
    target_t = max(1, history_len // 3)
    q = QueryCase(
        id=f"{sid}-q", scenario_type="retrieval_temporal", question_type="historical",
        subject_id=subject, predicate="deadline", as_of_valid_time=target_t, as_of_recorded_seq=None,
        expected_status=StateStatus.RESOLVED, expected_value=target_t,
        relevant_evidence_ids=(f"{sid}-e{target_t:04d}",),
        question_text=f"What was {alias}'s deadline as of day {target_t}?",
    )
    return Scenario(sid, evidence, assertions, [], [q])


def build_retrieval_suite(distractors: int, entities: int = 20) -> list[Scenario]:
    return [distractor_saturation_scenario(i, distractors) for i in range(entities)]


def build_temporal_suite(history_len: int, entities: int = 20) -> list[Scenario]:
    return [temporal_history_scenario(i, history_len) for i in range(entities)]


def semantic_saturation_scenario(i: int, distractors: int) -> Scenario:
    sid = f"ret-sem-{distractors}-{i:04d}"
    subject = f"semantic-target-{distractors}-{i:04d}"
    alias = f"alias-target-{i:04d}"
    target_eid = f"{sid}-target"
    evidence = [EvidenceRecord(
        target_eid,
        f"Supplier certification slipped and delayed release planning for {alias}; the deadline became day 42.",
        "source", recorded_seq=100000, source_event_time=42,
    )]
    assertions = [Assertion(
        id=f"{sid}-a-target", subject_id=subject, predicate="deadline", object_value=42,
        recorded_seq=100000, valid_from=42, evidence_ids=(target_eid,),
    )]
    for d in range(distractors):
        ds = f"semantic-distractor-{distractors}-{i:04d}-{d:05d}"
        da = f"alias-distractor-{i:04d}-{d:05d}"
        eid = f"{sid}-d{d:05d}"
        evidence.append(EvidenceRecord(
            eid,
            f"Vendor approval slipped and delayed launch planning for {da}; the due date became day 42.",
            "source", recorded_seq=d + 1, source_event_time=42,
        ))
        assertions.append(Assertion(
            id=f"{sid}-ad{d:05d}", subject_id=ds, predicate="deadline", object_value=42,
            recorded_seq=d + 1, valid_from=42, evidence_ids=(eid,),
        ))
    q = QueryCase(
        id=f"{sid}-q", scenario_type="semantic_saturation", question_type="current",
        subject_id=subject, predicate="deadline", as_of_valid_time=None, as_of_recorded_seq=None,
        expected_status=StateStatus.RESOLVED, expected_value=42,
        relevant_evidence_ids=(target_eid,),
        question_text=f"After the supplier certification delay, what is {alias}'s current release deadline?",
    )
    return Scenario(sid, evidence, assertions, [], [q])


def build_semantic_suite(distractors: int, entities: int = 20) -> list[Scenario]:
    return [semantic_saturation_scenario(i, distractors) for i in range(entities)]
