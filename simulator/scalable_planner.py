from __future__ import annotations

from dataclasses import dataclass

from core.models import Assertion, EvidenceRecord, QueryCase, StateStatus
from simulator.world import Scenario


@dataclass(frozen=True)
class ScalablePlannerCase:
    query: QueryCase
    should_abstain: bool = False


@dataclass(frozen=True)
class ScalablePlannerSuite:
    scenario: Scenario
    cases: tuple[ScalablePlannerCase, ...]
    entity_count: int
    label: str


def _sample_indices(entity_count: int, queries: int) -> list[int]:
    queries = min(max(1, queries), entity_count)
    if queries == 1:
        return [entity_count // 2]
    return sorted({round(i * (entity_count - 1) / (queries - 1)) for i in range(queries)})


def _transpose_alias(alias: str) -> str:
    if alias.startswith("Atlas-"):
        return "Atals-" + alias[len("Atlas-") :]
    return alias


def build_unique_cardinality_suite(
    entity_count: int,
    *,
    queries: int = 20,
    noisy_alias: bool = False,
) -> ScalablePlannerSuite:
    sid = f"scale-unique-{entity_count}-{'noisy' if noisy_alias else 'exact'}"
    evidence: list[EvidenceRecord] = []
    assertions: list[Assertion] = []
    query_rows: list[QueryCase] = []
    sampled = set(_sample_indices(entity_count, queries))

    for i in range(entity_count):
        subject = f"scale-subject-{i:07d}"
        alias = f"Atlas-{i:07d}"
        eid = f"{sid}-e{i:07d}"
        evidence.append(EvidenceRecord(
            eid,
            f"{alias} finance migration deadline is day 42.",
            "source",
            recorded_seq=i + 1,
            source_event_time=42,
        ))
        assertions.append(Assertion(
            id=f"{sid}-a{i:07d}",
            subject_id=subject,
            predicate="deadline",
            object_value=42,
            recorded_seq=i + 1,
            valid_from=42,
            evidence_ids=(eid,),
        ))
        if i in sampled:
            visible_alias = _transpose_alias(alias) if noisy_alias else alias
            query_rows.append(QueryCase(
                id=f"{sid}-q{i:07d}",
                scenario_type="scalable_unique_noisy" if noisy_alias else "scalable_unique",
                question_type="current",
                subject_id=subject,
                predicate="deadline",
                as_of_valid_time=None,
                as_of_recorded_seq=None,
                expected_status=StateStatus.RESOLVED,
                expected_value=42,
                relevant_evidence_ids=(eid,),
                question_text=f"What is {visible_alias}'s current due date?",
            ))

    scenario = Scenario(sid, evidence, assertions, [], query_rows)
    return ScalablePlannerSuite(
        scenario,
        tuple(ScalablePlannerCase(q, False) for q in query_rows),
        entity_count,
        f"unique_{'noisy' if noisy_alias else 'exact'}_{entity_count}",
    )


def build_contextual_cardinality_suite(
    entity_count: int,
    *,
    queries: int = 20,
    noisy_descriptor: bool = False,
) -> ScalablePlannerSuite:
    sid = f"scale-context-{entity_count}-{'noisy' if noisy_descriptor else 'exact'}"
    evidence: list[EvidenceRecord] = []
    assertions: list[Assertion] = []
    query_rows: list[QueryCase] = []
    sampled = set(_sample_indices(entity_count, queries))

    for i in range(entity_count):
        subject = f"scale-context-subject-{i:07d}"
        descriptor = f"program-{i:07d}"
        eid = f"{sid}-e{i:07d}"
        evidence.append(EvidenceRecord(
            eid,
            f"Orion {descriptor} deadline is day 42.",
            "source",
            recorded_seq=i + 1,
            source_event_time=42,
        ))
        assertions.append(Assertion(
            id=f"{sid}-a{i:07d}",
            subject_id=subject,
            predicate="deadline",
            object_value=42,
            recorded_seq=i + 1,
            valid_from=42,
            evidence_ids=(eid,),
        ))
        if i in sampled:
            qdesc = descriptor.replace("program-", "progrma-") if noisy_descriptor else descriptor
            query_rows.append(QueryCase(
                id=f"{sid}-q{i:07d}",
                scenario_type="scalable_context_noisy" if noisy_descriptor else "scalable_context",
                question_type="current",
                subject_id=subject,
                predicate="deadline",
                as_of_valid_time=None,
                as_of_recorded_seq=None,
                expected_status=StateStatus.RESOLVED,
                expected_value=42,
                relevant_evidence_ids=(eid,),
                question_text=f"What is Orion's due date for {qdesc}?",
            ))

    scenario = Scenario(sid, evidence, assertions, [], query_rows)
    return ScalablePlannerSuite(
        scenario,
        tuple(ScalablePlannerCase(q, False) for q in query_rows),
        entity_count,
        f"context_{'noisy' if noisy_descriptor else 'exact'}_{entity_count}",
    )


def build_ambiguous_cardinality_suite(
    entity_count: int,
    *,
    queries: int = 20,
) -> ScalablePlannerSuite:
    sid = f"scale-ambiguous-{entity_count}"
    evidence: list[EvidenceRecord] = []
    assertions: list[Assertion] = []

    for i in range(entity_count):
        subject = f"scale-ambiguous-subject-{i:07d}"
        eid = f"{sid}-e{i:07d}"
        evidence.append(EvidenceRecord(
            eid,
            "Orion program deadline is day 42.",
            "source",
            recorded_seq=i + 1,
            source_event_time=42,
        ))
        assertions.append(Assertion(
            id=f"{sid}-a{i:07d}",
            subject_id=subject,
            predicate="deadline",
            object_value=42,
            recorded_seq=i + 1,
            valid_from=42,
            evidence_ids=(eid,),
        ))

    # Reuse deterministic target identities only for evaluation. The visible question
    # is intentionally identical and lacks enough information to choose among them.
    query_rows: list[QueryCase] = []
    for j, i in enumerate(_sample_indices(entity_count, queries)):
        subject = f"scale-ambiguous-subject-{i:07d}"
        eid = f"{sid}-e{i:07d}"
        query_rows.append(QueryCase(
            id=f"{sid}-q{j:04d}",
            scenario_type="scalable_ambiguous",
            question_type="current",
            subject_id=subject,
            predicate="deadline",
            as_of_valid_time=None,
            as_of_recorded_seq=None,
            expected_status=StateStatus.RESOLVED,
            expected_value=42,
            relevant_evidence_ids=(eid,),
            question_text="What is Orion's current due date?",
        ))

    scenario = Scenario(sid, evidence, assertions, [], query_rows)
    return ScalablePlannerSuite(
        scenario,
        tuple(ScalablePlannerCase(q, True) for q in query_rows),
        entity_count,
        f"ambiguous_{entity_count}",
    )
