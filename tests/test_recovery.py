from __future__ import annotations

import unittest

from core.models import Assertion, EvidenceRecord
from simulator.cascade import alias, assertion_id, build_cascade_store, evidence_id, subject_id
from state.cascade import CascadeMaterialization, clone_canonical_store
from state.dependencies import DerivationStatus
from state.recovery import MaintenancePhase, RecoveryCoordinator


class RecoveryTests(unittest.TestCase):
    def _coordinator(self, n: int = 64) -> RecoveryCoordinator:
        return RecoveryCoordinator(CascadeMaterialization(build_cascade_store(n)))

    def _oracle(self, coordinator: RecoveryCoordinator) -> CascadeMaterialization:
        return CascadeMaterialization(clone_canonical_store(coordinator.store))

    def _assert_recovered(self, coordinator: RecoveryCoordinator) -> None:
        self.assertEqual(coordinator.journal, {})
        self.assertTrue(coordinator.all_derived_fresh())
        self.assertTrue(coordinator.materialization.equivalent_to(self._oracle(coordinator)))

    def test_prepared_intent_blocks_reads_and_replays_canonical_write(self):
        coordinator = self._coordinator()
        i = 7
        key = (subject_id(i), "deadline", "default")
        intent_id = coordinator.prepare_upsert_evidence(EvidenceRecord(
            evidence_id(i),
            f"{alias(i, 'Nova')} finance migration deadline is day 42.",
            "source",
            100,
            source_event_time=42,
        ))

        with self.assertRaises(RuntimeError):
            coordinator.read_context(key)

        restarted = coordinator.durable_image()
        trace = restarted.recover_all()
        self.assertGreater(trace.logical_work, 0)
        self._assert_recovered(restarted)
        self.assertIn("Nova", restarted.read_context(key) or "")
        self.assertNotIn(intent_id, restarted.journal)

    def test_crash_after_canonical_write_cannot_serve_stale_context(self):
        coordinator = self._coordinator()
        i = 11
        old = coordinator.store.assertions[assertion_id(i)]
        key = old.key
        intent_id = coordinator.prepare_upsert_assertion(Assertion(
            old.id,
            old.subject_id,
            old.predicate,
            77,
            old.recorded_seq,
            valid_from=old.valid_from,
            valid_to=old.valid_to,
            evidence_ids=old.evidence_ids,
        ))
        coordinator.run_until(intent_id, MaintenancePhase.CANONICAL_APPLIED)

        # Canonical truth has changed while the old derived cell still exists.
        self.assertEqual(coordinator.store.assertions[old.id].object_value, 77)
        self.assertNotEqual(coordinator.store.state[key].operative_values, [77])
        with self.assertRaises(RuntimeError):
            coordinator.read_context(key)

        restarted = coordinator.durable_image()
        restarted.recover_all()
        self._assert_recovered(restarted)
        self.assertEqual(restarted.store.state[key].operative_values, [77])

    def test_crash_after_invalidation_recovers_only_named_region(self):
        coordinator = self._coordinator()
        i = 13
        old = coordinator.store.assertions[assertion_id(i)]
        intent_id = coordinator.prepare_upsert_assertion(Assertion(
            old.id,
            old.subject_id,
            old.predicate,
            81,
            old.recorded_seq,
            valid_from=old.valid_from,
            valid_to=old.valid_to,
            evidence_ids=old.evidence_ids,
        ))
        coordinator.run_until(intent_id, MaintenancePhase.INVALIDATED)
        affected = coordinator.affected_signature(intent_id)
        self.assertEqual(len(affected), 3)
        self.assertTrue(all(
            coordinator.graph.status_of(node_id) == DerivationStatus.INVALID
            for node_id in affected
        ))

        restarted = coordinator.durable_image()
        trace = restarted.recover_all()
        self.assertEqual(trace.rebuild.nodes_rebuilt, 3)
        self._assert_recovered(restarted)

    def test_crash_with_partial_rebuild_reinvalidates_untrusted_write(self):
        coordinator = self._coordinator()
        i = 17
        old = coordinator.store.assertions[assertion_id(i)]
        intent_id = coordinator.prepare_upsert_assertion(Assertion(
            old.id,
            old.subject_id,
            old.predicate,
            88,
            old.recorded_seq,
            valid_from=old.valid_from,
            valid_to=old.valid_to,
            evidence_ids=old.evidence_ids,
        ))
        coordinator.run_until(intent_id, MaintenancePhase.REBUILDING)
        intent = coordinator.journal[intent_id]
        self.assertIsNotNone(intent.partial_rebuild_node_id)
        self.assertEqual(
            coordinator.graph.status_of(intent.partial_rebuild_node_id or ""),
            DerivationStatus.REBUILDING,
        )

        restarted = coordinator.durable_image()
        trace = restarted.recover_all()
        self.assertGreater(trace.reinvalidation.nodes_invalidated, 0)
        self._assert_recovered(restarted)
        self.assertEqual(restarted.store.state[old.key].operative_values, [88])

    def test_crash_after_repair_only_needs_journal_finalization(self):
        coordinator = self._coordinator()
        i = 19
        old = coordinator.store.assertions[assertion_id(i)]
        intent_id = coordinator.prepare_upsert_assertion(Assertion(
            old.id,
            old.subject_id,
            old.predicate,
            91,
            old.recorded_seq,
            valid_from=old.valid_from,
            valid_to=old.valid_to,
            evidence_ids=old.evidence_ids,
        ))
        coordinator.run_until(intent_id, MaintenancePhase.REPAIRED)
        self.assertTrue(coordinator.materialization.equivalent_to(self._oracle(coordinator)))
        self.assertIn(intent_id, coordinator.journal)

        restarted = coordinator.durable_image()
        trace = restarted.recover_all()
        self.assertEqual(trace.rebuild.nodes_rebuilt, 0)
        self.assertEqual(trace.canonical_mutations, 0)
        self._assert_recovered(restarted)

    def test_delete_crash_recovery_retires_branch(self):
        coordinator = self._coordinator()
        i = 23
        previous = coordinator.store.assertions[assertion_id(i)]
        key = previous.key
        intent_id = coordinator.prepare_delete_assertion(previous.id)
        coordinator.run_until(intent_id, MaintenancePhase.REBUILDING)

        restarted = coordinator.durable_image()
        restarted.recover_all()
        self._assert_recovered(restarted)
        self.assertNotIn(key, restarted.store.state)
        self.assertNotIn(key, restarted.materialization.supports)
        self.assertNotIn(key, restarted.materialization.contexts)
        self.assertNotIn(previous.subject_id, restarted.materialization.index.profiles)

    def test_recovery_is_idempotent_after_completion(self):
        coordinator = self._coordinator()
        i = 29
        old = coordinator.store.assertions[assertion_id(i)]
        intent_id = coordinator.prepare_upsert_assertion(Assertion(
            old.id,
            old.subject_id,
            old.predicate,
            95,
            old.recorded_seq,
            valid_from=old.valid_from,
            valid_to=old.valid_to,
            evidence_ids=old.evidence_ids,
        ))
        coordinator.run_until(intent_id, MaintenancePhase.CANONICAL_APPLIED)
        restarted = coordinator.durable_image()
        first = restarted.recover_all()
        second = restarted.recover_all()
        self.assertGreater(first.logical_work, 0)
        self.assertEqual(second.logical_work, 0)
        self._assert_recovered(restarted)


if __name__ == "__main__":
    unittest.main()
