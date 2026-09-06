from __future__ import annotations

import copy
from dataclasses import dataclass, field
from enum import Enum
from itertools import count

from core.models import Assertion, EvidenceRecord
from state.cascade import (
    CascadeMaterialization,
    assertion_node,
    context_node,
    evidence_node,
    profile_node,
    state_node,
    support_node,
)
from state.dependencies import DependencyTrace, DerivationStatus


class MaintenanceOperation(str, Enum):
    UPSERT_EVIDENCE = "upsert_evidence"
    UPSERT_ASSERTION = "upsert_assertion"
    DELETE_ASSERTION = "delete_assertion"


class MaintenancePhase(str, Enum):
    PREPARED = "prepared"
    CANONICAL_APPLIED = "canonical_applied"
    INVALIDATED = "invalidated"
    REBUILDING = "rebuilding"
    REPAIRED = "repaired"


@dataclass
class MaintenanceIntent:
    id: str
    operation: MaintenanceOperation
    phase: MaintenancePhase = MaintenancePhase.PREPARED
    evidence: EvidenceRecord | None = None
    assertion: Assertion | None = None
    assertion_id: str | None = None
    previous_assertion: Assertion | None = None
    affected_node_ids: tuple[str, ...] = ()
    partial_rebuild_node_id: str | None = None


@dataclass
class RecoveryTrace:
    journal_reads: int = 0
    journal_writes: int = 0
    canonical_mutations: int = 0
    invalidation: DependencyTrace = field(default_factory=DependencyTrace)
    reinvalidation: DependencyTrace = field(default_factory=DependencyTrace)
    rebuild: DependencyTrace = field(default_factory=DependencyTrace)

    @property
    def logical_work(self) -> int:
        return (
            self.journal_reads
            + self.journal_writes
            + self.canonical_mutations
            + self.invalidation.logical_work
            + self.reinvalidation.logical_work
            + self.rebuild.logical_work
        )

    def absorb(self, other: "RecoveryTrace") -> None:
        self.journal_reads += other.journal_reads
        self.journal_writes += other.journal_writes
        self.canonical_mutations += other.canonical_mutations
        self.invalidation.absorb(other.invalidation)
        self.reinvalidation.absorb(other.reinvalidation)
        self.rebuild.absorb(other.rebuild)


class RecoveryCoordinator:
    """Single-flight durable maintenance protocol for the v0.8 falsification test.

    The journal intent is durable *before* a canonical mutation. A restarted process
    must drain this journal before serving derived reads. This means a crash after a
    canonical write but before descendant invalidation cannot expose the old derived
    value as if it were still fresh.

    The prototype deliberately models durability with a deep-copied durable image;
    it does not claim filesystem/database atomicity. Recovery work is restricted to
    node IDs carried by the intent rather than discovered by a global graph scan.
    """

    _ids = count(1)

    def __init__(self, materialization: CascadeMaterialization):
        self.materialization = materialization
        self.store = materialization.store
        self.graph = materialization.graph
        self.journal: dict[str, MaintenanceIntent] = {}

    def _require_single_flight(self) -> None:
        if self.journal:
            raise RuntimeError("v0.8 prototype permits one in-flight maintenance intent")

    def _insert_intent(self, intent: MaintenanceIntent) -> str:
        self._require_single_flight()
        self.journal[intent.id] = intent
        return intent.id

    def prepare_upsert_evidence(self, item: EvidenceRecord) -> str:
        return self._insert_intent(MaintenanceIntent(
            id=f"maintenance-{next(self._ids)}",
            operation=MaintenanceOperation.UPSERT_EVIDENCE,
            evidence=item,
        ))

    def prepare_upsert_assertion(self, item: Assertion) -> str:
        return self._insert_intent(MaintenanceIntent(
            id=f"maintenance-{next(self._ids)}",
            operation=MaintenanceOperation.UPSERT_ASSERTION,
            assertion=item,
            previous_assertion=self.store.assertions.get(item.id),
        ))

    def prepare_delete_assertion(self, assertion_id: str) -> str:
        previous = self.store.assertions.get(assertion_id)
        if previous is None:
            raise KeyError(f"Unknown assertion: {assertion_id}")
        return self._insert_intent(MaintenanceIntent(
            id=f"maintenance-{next(self._ids)}",
            operation=MaintenanceOperation.DELETE_ASSERTION,
            assertion_id=assertion_id,
            previous_assertion=previous,
        ))

    def _apply_canonical(self, intent: MaintenanceIntent) -> None:
        if intent.operation == MaintenanceOperation.UPSERT_EVIDENCE:
            if intent.evidence is None:
                raise ValueError("Evidence intent missing payload")
            self.store.add_evidence(intent.evidence)
        elif intent.operation == MaintenanceOperation.UPSERT_ASSERTION:
            if intent.assertion is None:
                raise ValueError("Assertion intent missing payload")
            self.store.add_assertion(intent.assertion)
        elif intent.operation == MaintenanceOperation.DELETE_ASSERTION:
            if intent.assertion_id is None:
                raise ValueError("Delete intent missing assertion id")
            self.store.remove_assertion(intent.assertion_id)
        else:
            raise ValueError(f"Unsupported maintenance operation: {intent.operation}")

    def _invalidate(self, intent: MaintenanceIntent) -> DependencyTrace:
        before = set(self.graph.invalid_nodes())

        if intent.operation == MaintenanceOperation.UPSERT_EVIDENCE:
            if intent.evidence is None:
                raise ValueError("Evidence intent missing payload")
            trace = self.graph.invalidate_from([evidence_node(intent.evidence.id)])

        elif intent.operation == MaintenanceOperation.UPSERT_ASSERTION:
            item = intent.assertion
            if item is None:
                raise ValueError("Assertion intent missing payload")
            previous = intent.previous_assertion
            seeds: set[str] = set()
            for key in {k for k in (
                previous.key if previous is not None else None,
                item.key,
            ) if k is not None}:
                seeds.add(self.materialization.ensure_state_chain(key)[0])

            profile_changed = (
                previous is None
                or previous.subject_id != item.subject_id
                or previous.predicate != item.predicate
                or previous.evidence_ids != item.evidence_ids
            )
            if profile_changed:
                for subject_id in {
                    s for s in (
                        previous.subject_id if previous is not None else None,
                        item.subject_id,
                    ) if s is not None
                }:
                    seeds.add(self.materialization.ensure_profile(subject_id))
            trace = self.graph.invalidate_nodes(seeds)

        elif intent.operation == MaintenanceOperation.DELETE_ASSERTION:
            previous = intent.previous_assertion
            if previous is None:
                raise ValueError("Delete intent missing previous assertion")
            seeds = {
                self.materialization.ensure_state_chain(previous.key)[0],
                self.materialization.ensure_profile(previous.subject_id),
            }
            trace = self.graph.invalidate_nodes(seeds)
        else:
            raise ValueError(f"Unsupported maintenance operation: {intent.operation}")

        after = set(self.graph.invalid_nodes())
        intent.affected_node_ids = tuple(sorted(after - before))
        return trace

    @staticmethod
    def _rebuild_priority(kind: str | None) -> int:
        return {
            "profile": 0,
            "state": 0,
            "support": 1,
            "context": 2,
        }.get(kind, 99)

    def _simulate_durable_partial_rebuild(
        self,
        intent: MaintenanceIntent,
    ) -> DependencyTrace:
        """Persist one derived write while leaving its lifecycle as REBUILDING.

        This models a crash after a materialization/lineage write but before the
        fresh marker is durably committed. Recovery must treat that write as
        untrusted and reconstruct it idempotently from canonical state.
        """

        candidates = [
            node_id for node_id in intent.affected_node_ids
            if self.graph.status_of(node_id) == DerivationStatus.INVALID
        ]
        if not candidates:
            raise RuntimeError("No invalid affected node available for REBUILDING failpoint")
        candidates.sort(key=lambda node_id: (
            self._rebuild_priority(self.graph.kind_of(node_id)), node_id
        ))
        node_id = candidates[0]
        self.graph.mark_rebuilding(node_id)
        kind = self.graph.kind_of(node_id)
        trace = DependencyTrace()

        if kind == "profile":
            subject_id = self.materialization._subject_by_profile_node[node_id]
            self.materialization._rebuild_profile(subject_id, trace)
        elif kind == "state":
            key = self.materialization._key_by_node[node_id]
            self.materialization._rebuild_state(key, trace)
        elif kind == "support":
            key = self.materialization._key_by_node[node_id]
            self.materialization._rebuild_support(key, trace)
        elif kind == "context":
            key = self.materialization._key_by_node[node_id]
            self.materialization._rebuild_context(key, trace)
        else:
            raise KeyError(f"Unknown derived node kind for partial rebuild: {kind}")

        # The rebuild helper re-registers the node with REBUILDING status. Do not
        # mark it fresh: this is the crash boundary under test.
        intent.partial_rebuild_node_id = node_id
        return trace

    def run_until(
        self,
        intent_id: str,
        stop_after: MaintenancePhase | None = None,
    ) -> RecoveryTrace:
        intent = self.journal[intent_id]
        trace = RecoveryTrace(journal_reads=1)

        if stop_after == MaintenancePhase.PREPARED:
            return trace

        if intent.phase == MaintenancePhase.PREPARED:
            self._apply_canonical(intent)
            trace.canonical_mutations += 1
            intent.phase = MaintenancePhase.CANONICAL_APPLIED
            trace.journal_writes += 1
        if stop_after == MaintenancePhase.CANONICAL_APPLIED:
            return trace

        if intent.phase == MaintenancePhase.CANONICAL_APPLIED:
            trace.invalidation.absorb(self._invalidate(intent))
            intent.phase = MaintenancePhase.INVALIDATED
            trace.journal_writes += 1
        if stop_after == MaintenancePhase.INVALIDATED:
            return trace

        if stop_after == MaintenancePhase.REBUILDING and intent.phase == MaintenancePhase.INVALIDATED:
            # Partial derived work is deliberately not part of post-crash recovery
            # cost, but it is persisted in the durable image presented to recovery.
            self._simulate_durable_partial_rebuild(intent)
            intent.phase = MaintenancePhase.REBUILDING
            trace.journal_writes += 1
            return trace

        if intent.phase == MaintenancePhase.REBUILDING:
            # A REBUILDING node may contain an arbitrarily partial materialization.
            # Re-invalidate only the affected nodes named by the durable intent.
            trace.reinvalidation.absorb(
                self.graph.invalidate_nodes(intent.affected_node_ids)
            )
            intent.phase = MaintenancePhase.INVALIDATED
            trace.journal_writes += 1

        if intent.phase == MaintenancePhase.INVALIDATED:
            trace.rebuild.absorb(
                self.materialization.rebuild(intent.affected_node_ids)
            )
            intent.phase = MaintenancePhase.REPAIRED
            trace.journal_writes += 1
        if stop_after == MaintenancePhase.REPAIRED:
            return trace

        if intent.phase == MaintenancePhase.REPAIRED:
            self.journal.pop(intent_id, None)
            trace.journal_writes += 1
        return trace

    def recover_all(self) -> RecoveryTrace:
        trace = RecoveryTrace(journal_reads=len(self.journal))
        for intent_id in tuple(sorted(self.journal)):
            trace.absorb(self.run_until(intent_id))
        return trace

    def read_context(self, key: tuple[str, str, str]) -> str | None:
        if self.journal:
            raise RuntimeError("Recovery required before derived reads are admitted")
        return self.materialization.read_context(key)

    def durable_image(self) -> "RecoveryCoordinator":
        """Simulate the process-independent durable image at a crash boundary."""

        return copy.deepcopy(self)

    def all_derived_fresh(self) -> bool:
        return all(
            self.graph.status_of(node_id) == DerivationStatus.FRESH
            for node_id in self.graph.derived_nodes()
        )

    def affected_signature(self, intent_id: str) -> tuple[str, ...]:
        return self.journal[intent_id].affected_node_ids
