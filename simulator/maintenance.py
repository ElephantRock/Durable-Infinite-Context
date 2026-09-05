from __future__ import annotations

from core.models import Assertion, EvidenceRecord
from core.storage import MemoryStore


def subject_id(i: int) -> str:
    return f"maint-subject-{i:07d}"


def evidence_id(i: int) -> str:
    return f"maint-evidence-{i:07d}"


def assertion_id(i: int) -> str:
    return f"maint-assertion-{i:07d}"


def alias(i: int, prefix: str = "Atlas") -> str:
    return f"{prefix}-{i:07d}"


def build_maintenance_store(entity_count: int) -> MemoryStore:
    """Create a one-assertion/one-evidence-per-subject addressability world."""

    store = MemoryStore()
    for i in range(entity_count):
        eid = evidence_id(i)
        store.add_evidence(EvidenceRecord(
            id=eid,
            payload=f"{alias(i)} finance migration deadline is day 42.",
            source_id="source",
            recorded_seq=i + 1,
            source_event_time=42,
        ))
        store.add_assertion(Assertion(
            id=assertion_id(i),
            subject_id=subject_id(i),
            predicate="deadline",
            object_value=42,
            recorded_seq=i + 1,
            valid_from=42,
            evidence_ids=(eid,),
        ))
    return store
