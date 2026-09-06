from __future__ import annotations

import unittest

from core.models import EvidenceRecord
from simulator.cascade import assertion_id, build_cascade_store, evidence_id, subject_id
from state.cascade import CascadeMaterialization, clone_canonical_store
from state.recovery import MaintenancePhase, RecoveryCoordinator


class RecoveryRedoTests(unittest.TestCase):
    def _coordinator(self) -> RecoveryCoordinator:
        return RecoveryCoordinator(CascadeMaterialization(build_cascade_store(64)))

    def _assert_recovered(self, coordinator: RecoveryCoordinator) -> None:
        oracle = CascadeMaterialization(clone_canonical_store(coordinator.store))
        self.assertEqual(coordinator.journal, {})
        self.assertTrue(coordinator.all_derived_fresh())
        self.assertTrue(coordinator.materialization.equivalent_to(oracle))

    def test_canonical_applied_marker_redoes_missing_evidence_upsert(self):
        coordinator = self._coordinator()
        i = 12
        key = (subject_id(i), "deadline", "default")
        item = EvidenceRecord(
            evidence_id(i),
            "RedoNova finance migration deadline is day 42.",
            "source",
            1000,
            source_event_time=42,
        )
        intent_id = coordinator.prepare_upsert_evidence(item)

        # Marker persisted, replacement payload did not.
        coordinator.journal[intent_id].phase = MaintenancePhase.CANONICAL_APPLIED
        self.assertNotEqual(coordinator.store.evidence[item.id].payload, item.payload)

        restarted = coordinator.durable_image()
        trace = restarted.recover_all()
        self.assertEqual(trace.canonical_mutations, 1)
        self._assert_recovered(restarted)
        self.assertEqual(restarted.store.evidence[item.id].payload, item.payload)
        self.assertIn("RedoNova", restarted.read_context(key) or "")

    def test_canonical_applied_marker_redoes_missing_delete(self):
        coordinator = self._coordinator()
        i = 14
        old = coordinator.store.assertions[assertion_id(i)]
        intent_id = coordinator.prepare_delete_assertion(old.id)

        # Marker persisted, deletion itself did not.
        coordinator.journal[intent_id].phase = MaintenancePhase.CANONICAL_APPLIED
        self.assertIn(old.id, coordinator.store.assertions)

        restarted = coordinator.durable_image()
        trace = restarted.recover_all()
        self.assertEqual(trace.canonical_mutations, 1)
        self._assert_recovered(restarted)
        self.assertNotIn(old.id, restarted.store.assertions)
        self.assertNotIn(old.key, restarted.store.state)
        self.assertNotIn(old.subject_id, restarted.materialization.index.profiles)


if __name__ == "__main__":
    unittest.main()
