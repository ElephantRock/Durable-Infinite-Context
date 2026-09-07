from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from simulator.durable_hybrid import prepare_scenario
from storage.durable_hybrid import DurableHybridStore


class DurableHybridTests(unittest.TestCase):
    def test_primary_insert_lookup_and_audit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dic-v023-test-") as tmp:
            store = DurableHybridStore(Path(tmp) / "index.sqlite3")
            store.initialize(initial_capacity=128, max_load=0.50)
            trace = store.insert("alpha|deadline")
            self.assertEqual(trace.path, "primary")
            lookup = store.lookup("alpha|deadline")
            self.assertTrue(lookup.found)
            self.assertEqual(lookup.path, "current")
            self.assertFalse(lookup.overflow_checked)
            self.assertEqual(lookup.metadata_rows_read, 1)
            self.assertTrue(store.audit()["valid"])
            self.assertEqual(store.transaction_settings()["journal_mode"], "wal")
            self.assertEqual(store.transaction_settings()["synchronous"], 2)

    def test_migration_start_and_progress_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dic-v023-test-") as tmp:
            store = DurableHybridStore(Path(tmp) / "index.sqlite3")
            store.initialize(
                initial_capacity=32,
                max_load=0.50,
                migration_slot_budget=8,
            )
            for index in range(16):
                store.insert(f"entity_{index:06d}|deadline")
            trace = store.insert("entity_000016|deadline")
            self.assertTrue(trace.migration_started)
            self.assertLessEqual(trace.migration_source_slots_scanned, 8)
            self.assertLessEqual(trace.migration_rows_moved, 8)
            self.assertLessEqual(trace.primary_mutation_work, 1808)
            self.assertIsNotNone(store.meta_snapshot()["old_generation"])
            self.assertTrue(store.audit()["valid"])

    def test_concentrated_17th_key_uses_explicit_overflow(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dic-v023-test-") as tmp:
            store = DurableHybridStore(Path(tmp) / "index.sqlite3")
            store.initialize(
                initial_capacity=1024,
                max_load=0.90,
                force_same_pair=True,
            )
            for index in range(16):
                self.assertEqual(
                    store.insert(f"collision_{index:06d}").path,
                    "primary",
                )
            trace = store.insert("collision_000016")
            self.assertEqual(trace.path, "overflow")
            self.assertEqual(trace.new_key_primary_work, 200)
            self.assertEqual(trace.overflow_row_writes, 1)
            lookup = store.lookup("collision_000016")
            self.assertTrue(lookup.found)
            self.assertEqual(lookup.path, "overflow")
            self.assertTrue(lookup.overflow_checked)
            self.assertTrue(store.audit()["valid"])

    def test_migration_can_escape_to_overflow_without_duplicate_membership(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dic-v023-test-") as tmp:
            store, _keys, target = prepare_scenario(
                Path(tmp) / "index.sqlite3", "migration_to_overflow"
            )
            trace = store.insert(target)
            self.assertGreater(trace.migration_rows_to_overflow, 0)
            self.assertTrue(trace.migration_completed)
            self.assertIsNone(store.meta_snapshot()["old_generation"])
            audit = store.audit()
            self.assertTrue(audit["valid"])
            self.assertEqual(audit["duplicate_keys"], [])
            self.assertTrue(store.lookup(target).found)

    def test_application_recovery_is_zero_work_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dic-v023-test-") as tmp:
            store = DurableHybridStore(Path(tmp) / "index.sqlite3")
            store.initialize()
            store.insert("alpha|deadline")
            first = store.recover()
            second = store.recover()
            self.assertEqual(first, second)
            self.assertEqual(first["logical_work"], 0)
            self.assertTrue(first["audit_valid"])


if __name__ == "__main__":
    unittest.main()
