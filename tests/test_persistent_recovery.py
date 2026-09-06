from __future__ import annotations

import unittest

from simulator.process_recovery import run_process_crash_case


class PersistentProcessRecoveryTests(unittest.TestCase):
    def test_sigkill_inside_canonical_transaction_rolls_back_atomically(self):
        case = run_process_crash_case(
            16,
            "replace_assertion_object",
            "canonical_uncommitted",
            index=5,
        )
        self.assertEqual(case.durable_phase_after_crash, "prepared")
        self.assertFalse(case.canonical_visible_after_crash)
        # PREPARED recovery must apply the canonical mutation exactly once.
        self.assertEqual(case.recovery_trace["canonical_mutations"], 1)
        self.assertTrue(case.materialization_equal)

    def test_committed_canonical_phase_is_trusted_without_redundant_redo(self):
        case = run_process_crash_case(
            16,
            "replace_assertion_object",
            "canonical_committed",
            index=6,
        )
        self.assertEqual(case.durable_phase_after_crash, "canonical_applied")
        self.assertTrue(case.canonical_visible_after_crash)
        # Canonical write + phase advance share one SQLite transaction, so this
        # state is trustworthy in v0.9 and does not need v0.8's defensive redo.
        self.assertEqual(case.recovery_trace["canonical_mutations"], 0)
        self.assertTrue(case.materialization_equal)

    def test_sigkill_inside_invalidation_transaction_rolls_back_invalidation(self):
        case = run_process_crash_case(
            16,
            "replace_evidence_payload",
            "invalidation_uncommitted",
            index=7,
        )
        self.assertEqual(case.durable_phase_after_crash, "canonical_applied")
        self.assertEqual(case.invalid_nodes_after_crash, 0)
        self.assertEqual(case.recovery_trace["canonical_mutations"], 0)
        self.assertTrue(case.semantic_check)

    def test_partial_persistent_rebuild_is_reinvalidated_and_repaired_locally(self):
        case = run_process_crash_case(
            16,
            "delete_assertion",
            "partial_rebuild_committed",
            index=8,
        )
        self.assertEqual(case.durable_phase_after_crash, "rebuilding")
        self.assertEqual(case.recovery_trace["reinvalidated_nodes"], 4)
        self.assertTrue(case.journal_empty)
        self.assertTrue(case.all_derived_fresh)
        self.assertTrue(case.materialization_equal)


if __name__ == "__main__":
    unittest.main()
