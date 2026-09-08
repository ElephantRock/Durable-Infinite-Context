from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from simulator.cross_store_hybrid import run_crash_case
from storage.cross_store_hybrid import CrossStoreHybridStore


class CrossStoreHybridTests(unittest.TestCase):
    def test_primary_hits_do_not_query_overflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = CrossStoreHybridStore(root / "primary.pages", root / "overflow.sqlite")
            store.initialize(initial_capacity=128, max_load=0.50)
            for index in range(32):
                trace = store.insert(f"key_{index:04d}", recovery_first=False)
                self.assertEqual(trace.path, "primary")
            lookup = store.lookup("key_0007")
            self.assertTrue(lookup.found)
            self.assertEqual(lookup.path, "primary")
            self.assertFalse(lookup.overflow_checked)
            self.assertEqual(lookup.coordinator_extra_preads, 0)

    def test_direct_overflow_is_epoch_gated_and_crash_atomic(self) -> None:
        committed_future = run_crash_case("overflow_admission", "overflow_committed")
        self.assertFalse(committed_future.expected_committed)
        self.assertFalse(committed_future.target_visible_after_crash)
        self.assertEqual(committed_future.pre_recovery_future_overflow_rows, 1)
        self.assertEqual(
            committed_future.recovery_one["deleted_future_overflow_rows"], 1
        )
        committed = run_crash_case("overflow_admission", "committed")
        self.assertTrue(committed.expected_committed)
        self.assertTrue(committed.target_visible_after_crash)
        self.assertEqual(committed.recovery_one["deleted_future_overflow_rows"], 0)

    def test_migration_start_stale_tail_is_reclaimed_without_logical_redo(self) -> None:
        row = run_crash_case("migration_start", "primary_data_synced")
        self.assertFalse(row.expected_committed)
        self.assertGreater(row.pre_recovery_tail_bytes, 0)
        self.assertEqual(
            row.recovery_one["reclaimed_tail_bytes"], row.pre_recovery_tail_bytes
        )
        self.assertEqual(row.recovery_one["logical_redo"], 0)
        self.assertTrue(row.recovery_one["logical_snapshot_unchanged"])
        self.assertEqual(row.recovery_two["reclaimed_tail_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
