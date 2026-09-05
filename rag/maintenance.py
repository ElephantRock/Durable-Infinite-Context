from __future__ import annotations

from dataclasses import dataclass

from core.models import Assertion, EvidenceRecord
from core.storage import MemoryStore
from rag.scalable_planner import MaintenanceTrace, SubjectProfileIndex


@dataclass(frozen=True)
class MaintenanceResult:
    operation: str
    affected_subject_ids: tuple[str, ...]
    trace: MaintenanceTrace


class AddressabilityMaintainer:
    """Apply canonical mutations and refresh only their addressability dependencies.

    This class intentionally does not own canonical state. ``MemoryStore`` remains the
    source of truth; the maintainer computes the affected subject region, mutates the
    store, and asks ``SubjectProfileIndex`` to reconstruct only that region.
    """

    def __init__(self, store: MemoryStore, index: SubjectProfileIndex):
        self.store = store
        self.index = index

    def _refresh(self, operation: str, subject_ids: set[str]) -> MaintenanceResult:
        trace = MaintenanceTrace()
        ordered = tuple(sorted(subject_ids))
        for subject_id in ordered:
            trace.absorb(self.index.refresh_subject(self.store, subject_id))
        return MaintenanceResult(operation, ordered, trace)

    def upsert_evidence(self, item: EvidenceRecord) -> MaintenanceResult:
        # Any subject already depending on this stable evidence address may change
        # lexical profile when the evidence payload/version changes.
        affected = set(self.store.subjects_for_evidence(item.id))
        self.store.add_evidence(item)
        return self._refresh("upsert_evidence", affected)

    def delete_evidence(self, evidence_id: str) -> MaintenanceResult:
        affected = set(self.store.subjects_for_evidence(evidence_id))
        self.store.remove_evidence(evidence_id)
        return self._refresh("delete_evidence", affected)

    def upsert_assertion(self, item: Assertion) -> MaintenanceResult:
        previous = self.store.assertions.get(item.id)
        affected = {item.subject_id}
        if previous is not None:
            affected.add(previous.subject_id)
        self.store.add_assertion(item)
        return self._refresh("upsert_assertion", affected)

    def delete_assertion(self, assertion_id: str) -> MaintenanceResult:
        previous = self.store.assertions.get(assertion_id)
        if previous is None:
            return MaintenanceResult("delete_assertion", (), MaintenanceTrace())
        affected = {previous.subject_id}
        self.store.remove_assertion(assertion_id)
        return self._refresh("delete_assertion", affected)
