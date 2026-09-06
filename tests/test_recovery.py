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

    def test_torn_prepared_marker_replays_already_durable_assertion_upsert(self):
        """Canonical write durable, PREPARED phase marker still durable: replay is safe."""

        coordinator = self._coordinator()
        i = 8
        old = coordinator.store.assertions[assertion_id(i)]
        replacement = Assertion(
            old.id,
            old.subject_id,
            old.predicate,
            76,
            old.recorded_seq,
            valid_from=old.valid_from,
            valid_to=old.valid_to,
            evidence_ids=old.evidence_ids,
        )
        intent_id = coordinator.prepare_upsert_assertion(replacement)

        # Simulate canonical persistence succeeding while the phase advancement is lost.
        coordinator.store.add_assertion(replacement)
        self.assertEqual(coordinator.journal[intent_id].phase, MaintenancePhase.PREPARED)

        restarted = coordinator.durable_image()
        trace = restarted.recover_all()
        self.assertEqual(trace.canonical_mutations, 1)
        self._assert_recovered(restarted)
        self.assertEqual(restarted.store.state[old.key].operative_values, [76])

    def test_torn_prepared_marker_replays_already_durable_delete(self):
        """Deletion must also be redo-safe when phase advancement is lost."""

        coordinator = self._coordinator()
        i = 9
        old = coordinator.store.assertions[assertion_id(i)]
        intent_id = coordinator.prepare_delete_assertion(old.id)

        coordinator.store.remove_assertion(old.id)
        self.assertEqual(coordinator.journal[intent_id].phase, MaintenancePhase.PREPARED)
        self.assertNotIn(old.id, coordinator.store.assertions)

        restarted = coordinator.durable_image()
        trace = restarted.recover_all()
        self.assertEqual(trace.canonical_mutations, 1)
        self._assert_recovered(restarted)
        self.assertNotIn(old.key, restarted.store.state)
        self.assertNotIn(old.subject_id, restarted.materialization.index.profiles)

    def test_torn_canonical_applied_marker_replays_missing_canonical_write(self):
        """Phase marker durable, canonical write lost: recovery must redo canonical mutation."""

        coordinator = self._coordinator()
        i = 10
        old = coordinator.store.assertions[assertion_id(i)]
        replacement = Assertion(
            old.id,
            old.subject_id,
            old.predicate,
            79,
            old.recorded_seq,
            valid_from=old.valid_from,
            valid_to=old.valid_to,
            evidence_ids=old.evidence_ids,
        )
        intent_id = coordinator.prepare_upsert_assertion(replacement)

        # Simulate the opposite torn boundary: the phase marker persisted but the
        # canonical write did not. A redo-style recovery protocol must repair this.
        coordinator.journal[intent_id].phase = MaintenancePhase.CANONICAL_APPLIED
        self.assertEqual(coordinator.store.assertions[old.id].object_value, old.object_value)

        restarted = coordinator.durable_image()
        trace = restarted.recover_all()
        self.assertEqual(trace.canonical_mutations, 1)
        self._assert_recovered(restarted)
        self.assertEqual(restarted.store.assertions[old.id].object_value, 79)
        self.assertEqual(restarted.store.state[old.key].operative_values, [79])

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

    def test_recovery_path_does_not_scan_all_invalid_nodes(self):
        coordinator = self._coordinator(256)
        i = 31
        old = coordinator.store.assertions[assertion_id(i)]
        intent_id = coordinator.prepare_upsert_assertion(Assertion(
            old.id,
            old.subject_id,
            old.predicate,
            99,
            old.recorded_seq,
            valid_from=old.valid_from,
            valid_to=old.valid_to,
            evidence_ids=old.evidence_ids,
        ))

        def forbidden_global_scan():
            raise AssertionError("recovery performed a whole-graph invalid_nodes scan")

        coordinator.graph.invalid_nodes = forbidden_global_scan  # type: ignore[method-assign]
        coordinator.run_until(intent_id, MaintenancePhase.REBUILDING)
        restarted = coordinator.durable_image()
        restarted.graph.invalid_nodes = forbidden_global_scan  # type: ignore[method-assign]
        restarted.recover_all()
        self.assertEqual(restarted.store.state[old.key].operative_values, [99])
        self.assertEqual(restarted.journal, {})


if __name__ == "__main__":
    unittest.main()
