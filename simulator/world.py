from __future__ import annotations

from dataclasses import dataclass

from core.models import Assertion, AssertionRelation, EvidenceRecord, QueryCase, RelationType, StateStatus


@dataclass
class Scenario:
    id: str
    evidence: list[EvidenceRecord]
    assertions: list[Assertion]
    relations: list[AssertionRelation]
    queries: list[QueryCase]


def _e(sid: str, n: int, text: str, source: str = "source") -> EvidenceRecord:
    return EvidenceRecord(f"{sid}-e{n}", text, source, recorded_seq=n, source_event_time=n)


def _a(
    sid: str,
    n: int,
    subject: str,
    predicate: str,
    value,
    *,
    valid_from: int | None = None,
    valid_to: int | None = None,
) -> Assertion:
    return Assertion(
        id=f"{sid}-a{n}",
        subject_id=subject,
        predicate=predicate,
        object_value=value,
        recorded_seq=n,
        valid_from=valid_from,
        valid_to=valid_to,
        evidence_ids=(f"{sid}-e{n}",),
    )


def correction_scenario(i: int) -> Scenario:
    sid = f"corr-{i:04d}"
    subject = f"project-corr-{i:04d}"
    e1 = _e(sid, 1, f"{subject} deadline is day 10.")
    e2 = _e(sid, 2, f"Correction: day 10 was wrong. {subject} deadline is day 14.")
    a1 = _a(sid, 1, subject, "deadline", 10, valid_from=1)
    a2 = _a(sid, 2, subject, "deadline", 14, valid_from=1)
    rel = AssertionRelation(a2.id, RelationType.CORRECTS, a1.id)
    q = [
        QueryCase(f"{sid}-q-current", "correction", "current", subject, "deadline", None, None, StateStatus.RESOLVED, 14, relevant_evidence_ids=(e2.id,)),
        QueryCase(f"{sid}-q-oldbelief", "correction", "historical_belief", subject, "deadline", 1, 1, StateStatus.RESOLVED, 10, relevant_evidence_ids=(e1.id,)),
        QueryCase(f"{sid}-q-nowaboutpast", "correction", "historical", subject, "deadline", 1, None, StateStatus.RESOLVED, 14, relevant_evidence_ids=(e2.id,)),
        QueryCase(f"{sid}-q-rel", "correction", "relation_classification", subject, "deadline", None, None, StateStatus.RESOLVED, 14, expected_relation="correction", relevant_evidence_ids=(e1.id, e2.id)),
        QueryCase(f"{sid}-q-prov", "correction", "provenance", subject, "deadline", None, None, StateStatus.RESOLVED, 14, relevant_evidence_ids=(e2.id,)),
    ]
    return Scenario(sid, [e1, e2], [a1, a2], [rel], q)


def transition_scenario(i: int) -> Scenario:
    sid = f"trans-{i:04d}"
    subject = f"project-trans-{i:04d}"
    e1 = _e(sid, 1, f"{subject} deadline is day 10.")
    e2 = _e(sid, 2, f"{subject} deadline moved from day 10 to day 14.")
    a1 = _a(sid, 1, subject, "deadline", 10, valid_from=1, valid_to=1)
    a2 = _a(sid, 2, subject, "deadline", 14, valid_from=2)
    rel = AssertionRelation(a2.id, RelationType.SUPERSEDES, a1.id)
    q = [
        QueryCase(f"{sid}-q-current", "transition", "current", subject, "deadline", None, None, StateStatus.RESOLVED, 14, relevant_evidence_ids=(e2.id,)),
        QueryCase(f"{sid}-q-hist", "transition", "historical", subject, "deadline", 1, None, StateStatus.RESOLVED, 10, relevant_evidence_ids=(e1.id,)),
        QueryCase(f"{sid}-q-rel", "transition", "relation_classification", subject, "deadline", None, None, StateStatus.RESOLVED, 14, expected_relation="transition", relevant_evidence_ids=(e1.id, e2.id)),
    ]
    return Scenario(sid, [e1, e2], [a1, a2], [rel], q)


def conflict_scenario(i: int) -> Scenario:
    sid = f"conf-{i:04d}"
    subject = f"company-{i:04d}"
    e1 = _e(sid, 1, f"Source A reports {subject} value is 10.", source="A")
    e2 = _e(sid, 2, f"Source B reports {subject} value is 12.", source="B")
    a1 = _a(sid, 1, subject, "value", 10, valid_from=1)
    a2 = _a(sid, 2, subject, "value", 12, valid_from=1)
    q = [
        QueryCase(f"{sid}-q-current", "conflict", "current", subject, "value", None, None, StateStatus.CONTESTED, None, relevant_evidence_ids=(e1.id, e2.id)),
        QueryCase(f"{sid}-q-prov", "conflict", "provenance", subject, "value", None, None, StateStatus.CONTESTED, None, relevant_evidence_ids=(e1.id, e2.id)),
    ]
    return Scenario(sid, [e1, e2], [a1, a2], [], q)


def build_suite(n_each: int = 250) -> list[Scenario]:
    out: list[Scenario] = []
    for i in range(n_each):
        out.append(correction_scenario(i))
        out.append(transition_scenario(i))
        out.append(conflict_scenario(i))
    return out
