from __future__ import annotations

from core.models import Assertion, AssertionRelation, EvidenceRecord, QueryCase, RelationType, StateStatus
from simulator.world import Scenario


def long_transition_scenario(i: int, history_len: int) -> Scenario:
    if history_len < 1:
        raise ValueError("history_len must be >= 1")
    sid = f"scale-{history_len}-{i:04d}"
    subject = f"project-scale-{history_len}-{i:04d}"
    evidence: list[EvidenceRecord] = []
    assertions: list[Assertion] = []
    relations: list[AssertionRelation] = []

    for n in range(1, history_len + 1):
        value = n
        if n == 1:
            text = f"{subject} deadline is day {value}."
        else:
            text = f"{subject} deadline moved from day {n-1} to day {value}."
        evidence.append(EvidenceRecord(f"{sid}-e{n}", text, "source", recorded_seq=n, source_event_time=n))
        assertions.append(Assertion(
            id=f"{sid}-a{n}", subject_id=subject, predicate="deadline", object_value=value,
            recorded_seq=n, valid_from=n, valid_to=(n if n < history_len else None),
            evidence_ids=(f"{sid}-e{n}",),
        ))
        if n > 1:
            relations.append(AssertionRelation(f"{sid}-a{n}", RelationType.SUPERSEDES, f"{sid}-a{n-1}"))

    q = [
        QueryCase(
            f"{sid}-q-current", "scaling", "current", subject, "deadline", None, None,
            StateStatus.RESOLVED, history_len, relevant_evidence_ids=(f"{sid}-e{history_len}",),
        ),
        QueryCase(
            f"{sid}-q-prov", "scaling", "provenance", subject, "deadline", None, None,
            StateStatus.RESOLVED, history_len, relevant_evidence_ids=(f"{sid}-e{history_len}",),
        ),
        QueryCase(
            f"{sid}-q-hist", "scaling", "historical", subject, "deadline", 1, None,
            StateStatus.RESOLVED, 1, relevant_evidence_ids=(f"{sid}-e1",),
        ),
    ]
    return Scenario(sid, evidence, assertions, relations, q)


def build_scaling_suite(history_len: int, entities: int = 100) -> list[Scenario]:
    return [long_transition_scenario(i, history_len) for i in range(entities)]
