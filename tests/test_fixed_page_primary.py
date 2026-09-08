from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from storage.fixed_page_primary import (
    PAGE_SIZE,
    FixedPagePrimaryStore,
    PrimaryAdmissionExhausted,
)


class FixedPagePrimaryTests(unittest.TestCase):
    def test_direct_address_lookup_and_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FixedPagePrimaryStore(Path(tmp) / "primary.pages")
            store.initialize(initial_capacity=128, max_load=0.50)
            for index in range(100):
                store.insert(f"entity_{index:06d}|deadline")
            trace = store.lookup("entity_000099|deadline")
            self.assertTrue(trace.found)
            self.assertEqual(trace.metadata_physical_preads, 2)
            self.assertEqual(trace.primary_physical_preads, 2 * trace.logical_primary_pages)
            expected_offsets: list[int] = []
            for page_id in trace.logical_page_ids:
                expected_offsets.extend(
                    [
                        PAGE_SIZE * (2 + 2 * page_id),
                        PAGE_SIZE * (3 + 2 * page_id),
                    ]
                )
            self.assertEqual(list(trace.physical_offsets), expected_offsets)
            self.assertTrue(store.audit()["valid"])
            self.assertEqual(store.address_formula()["primary_index_structure"], "none")

    def test_incremental_migration_stays_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FixedPagePrimaryStore(Path(tmp) / "primary.pages")
            store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=8)
            saw_start = False
            saw_complete = False
            for index in range(200):
                trace = store.insert(f"entity_{index:06d}|deadline")
                self.assertLessEqual(trace.migration_source_slots_scanned, 8)
                self.assertLessEqual(trace.migration_rows_moved, 8)
                self.assertEqual(trace.fsyncs, 2)
                saw_start |= trace.migration_started
                saw_complete |= trace.migration_completed
            self.assertTrue(saw_start)
            self.assertTrue(saw_complete)
            self.assertTrue(store.audit()["valid"])
            for index in range(200):
                self.assertTrue(store.lookup(f"entity_{index:06d}|deadline").found)

    def test_bounded_collision_rejection_preserves_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FixedPagePrimaryStore(Path(tmp) / "primary.pages")
            store.initialize(initial_capacity=1_024, max_load=0.90, force_same_pair=True)
            for index in range(16):
                store.insert(f"collision_{index:06d}")
            before = store.logical_snapshot()
            with self.assertRaises(PrimaryAdmissionExhausted):
                store.insert("collision_000016")
            self.assertEqual(store.logical_snapshot(), before)
            self.assertTrue(store.audit()["valid"])

    def test_dual_copy_commit_marker_selects_pre_or_post_state(self) -> None:
        class StopHere(RuntimeError):
            pass

        for stop_stage, committed in (
            ("pages_written", False),
            ("data_synced", False),
            ("committed", True),
        ):
            with self.subTest(stop_stage=stop_stage):
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / "primary.pages"
                    store = FixedPagePrimaryStore(path)
                    store.initialize(initial_capacity=32, max_load=0.50)
                    for index in range(16):
                        store.insert(f"entity_{index:06d}|deadline")
                    pre = store.logical_snapshot()

                    control_path = Path(tmp) / "control.pages"
                    control = FixedPagePrimaryStore(control_path)
                    control.initialize(initial_capacity=32, max_load=0.50)
                    for index in range(16):
                        control.insert(f"entity_{index:06d}|deadline")
                    control.insert("entity_000016|deadline")
                    post = control.logical_snapshot()

                    def failpoint(stage: str) -> None:
                        if stage == stop_stage:
                            raise StopHere(stage)

                    with self.assertRaises(StopHere):
                        store.insert("entity_000016|deadline", failpoint=failpoint)
                    expected = post if committed else pre
                    self.assertEqual(store.logical_snapshot(), expected)
                    self.assertEqual(
                        store.lookup("entity_000016|deadline").found, committed
                    )
                    self.assertTrue(store.audit()["valid"])


if __name__ == "__main__":
    unittest.main()
