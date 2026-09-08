from __future__ import annotations

import unittest

from simulator.recovery_interruption import run_recovery_interruption_case


class RecoveryInterruptionTests(unittest.TestCase):
    def test_future_delete_before_commit_rolls_back_then_converges(self) -> None:
        row = run_recovery_interruption_case(
            "future_overflow", "future_delete_uncommitted"
        )
        self.assertEqual(row.future_rows_after_interrupt, 1)
        self.assertEqual(row.final_future_rows, 0)
        self.assertTrue(row.converged_to_exact_snapshot)
        self.assertFalse(row.abandoned_visible_after_epoch_advance)
        self.assertTrue(row.replacement_visible_after_epoch_advance)

    def test_future_delete_after_commit_survives_process_death(self) -> None:
        row = run_recovery_interruption_case(
            "future_overflow", "future_delete_committed"
        )
        self.assertEqual(row.future_rows_after_interrupt, 0)
        self.assertEqual(row.final_future_rows, 0)
        self.assertTrue(row.final_audit_valid)
        self.assertFalse(row.abandoned_visible_after_epoch_advance)

    def test_tail_truncate_before_fsync_preserves_logical_state_in_sigkill_model(self) -> None:
        row = run_recovery_interruption_case("fixed_tail", "tail_truncated")
        self.assertGreater(row.pre_tail_bytes, 0)
        self.assertEqual(row.tail_bytes_after_interrupt, 0)
        self.assertEqual(row.final_tail_bytes, 0)
        self.assertTrue(row.interrupted_snapshot_exact)
        self.assertTrue(row.converged_to_exact_snapshot)

    def test_tail_after_fsync_is_restart_idempotent(self) -> None:
        row = run_recovery_interruption_case("fixed_tail", "tail_synced")
        self.assertEqual(row.tail_bytes_after_interrupt, 0)
        self.assertEqual(row.retry_two["reclaimed_tail_bytes"], 0)
        self.assertEqual(row.retry_two["deleted_future_overflow_rows"], 0)
        self.assertTrue(row.final_audit_valid)


if __name__ == "__main__":
    unittest.main()
