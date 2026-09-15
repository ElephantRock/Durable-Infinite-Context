from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from storage.middle_live_tail_evacuation_retirement_descriptor_primary import (
    MiddleLiveTailEvacuationRetirementDescriptorPrimaryStore,
)
from storage.retirement_descriptor_pool import RETIREMENT_STATUS_QUEUED


class MiddleLiveTailEvacuationTests(unittest.TestCase):
    @staticmethod
    def _insert_until_queue_count(
        store: MiddleLiveTailEvacuationRetirementDescriptorPrimaryStore,
        *,
        index: int,
        target: int,
    ) -> int:
        while int(store.retirement_queue_snapshot()["queue_count"]) < target:
            store.insert(f"k-{index:04d}")
            index += 1
            if index > 8192:
                raise AssertionError("fixture did not reach requested retirement queue depth")
        return index

    @staticmethod
    def _reclaim_to_depth(
        store: MiddleLiveTailEvacuationRetirementDescriptorPrimaryStore,
        *,
        target: int,
    ) -> None:
        while int(store.retirement_queue_snapshot()["queue_count"]) > target:
            store.reclaim_step(budget=2)

    def _build_middle_fixture(
        self,
        path: Path,
    ) -> tuple[MiddleLiveTailEvacuationRetirementDescriptorPrimaryStore, int]:
        store = MiddleLiveTailEvacuationRetirementDescriptorPrimaryStore(str(path))
        store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=4096)
        index = self._insert_until_queue_count(store, index=0, target=4)
        self._reclaim_to_depth(store, target=2)
        mid = store.retirement_queue_snapshot()
        self.assertEqual([int(r["descriptor_page"]) for r in mid["descriptors"]], [4, 6])
        self.assertEqual([int(r["descriptor_page"]) for r in mid["free_descriptors"]], [2, 0])
        index = self._insert_until_queue_count(store, index=index, target=3)
        return store, index

    def test_reuse_interiorizes_physical_tail_with_direct_predecessor_authority(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dic-v042-authority-") as tmp:
            store, _index = self._build_middle_fixture(Path(tmp) / "primary.pages")
            row = store.retirement_queue_snapshot()
            self.assertEqual([int(r["descriptor_page"]) for r in row["descriptors"]], [4, 6, 2])
            self.assertEqual([int(r["descriptor_page"]) for r in row["free_descriptors"]], [0])
            self.assertEqual(int(row["physical_tail_page"]), 6)
            self.assertEqual(int(row["physical_tail_predecessor_page"]), 4)
            self.assertEqual(int(row["head_page"]), 4)
            self.assertEqual(int(row["tail_page"]), 2)
            self.assertEqual(int(row["tail_predecessor_page"]), 6)

    def test_middle_physical_tail_relocates_into_sole_free_pair(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dic-v042-relocate-") as tmp:
            store, _index = self._build_middle_fixture(Path(tmp) / "primary.pages")
            before = store.retirement_queue_snapshot()
            old_tail_incarnation = int(before["physical_tail_incarnation"])
            old_destination_incarnation = int(before["descriptor_free_head_incarnation"])
            successor_incarnation = int(before["tail_incarnation"])

            trace = store.evacuate_middle_live_retirement_arena_tail_step()
            after = store.retirement_queue_snapshot()

            self.assertTrue(trace.released)
            self.assertEqual(trace.retirement_descriptor_preads, 8)
            self.assertEqual(trace.retirement_descriptor_pwrites, 2)
            self.assertEqual(trace.retirement_descriptors_scanned, 0)
            self.assertEqual(trace.live_descriptor_relocations, 1)
            self.assertEqual(trace.physical_tail_page, 6)
            self.assertEqual(trace.predecessor_page, 4)
            self.assertEqual(trace.successor_page, 2)
            self.assertEqual(trace.destination_page, 0)
            self.assertEqual(trace.new_physical_tail_page, 4)

            self.assertEqual([int(r["descriptor_page"]) for r in after["descriptors"]], [4, 0, 2])
            self.assertEqual(after["free_descriptors"], [])
            self.assertEqual(int(after["descriptor_arena_pages"]), 6)
            self.assertEqual(int(after["head_page"]), 4)
            self.assertEqual(int(after["tail_page"]), 2)
            self.assertEqual(int(after["tail_incarnation"]), successor_incarnation)
            self.assertEqual(int(after["tail_predecessor_page"]), 0)
            self.assertEqual(int(after["physical_tail_page"]), 4)
            self.assertIsNone(after["physical_tail_predecessor_page"])

            with self.assertRaises(RuntimeError):
                store.retirement_descriptor_reference(
                    6,
                    old_tail_incarnation,
                    expected_status=RETIREMENT_STATUS_QUEUED,
                )
            with self.assertRaises(RuntimeError):
                store.retirement_descriptor_reference(
                    0,
                    old_destination_incarnation,
                    expected_status=RETIREMENT_STATUS_QUEUED,
                )

    def test_second_step_refuses_outside_first_geometry(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dic-v042-second-") as tmp:
            store, _index = self._build_middle_fixture(Path(tmp) / "primary.pages")
            first = store.evacuate_middle_live_retirement_arena_tail_step()
            self.assertTrue(first.released)
            before = store.retirement_queue_snapshot()
            second = store.evacuate_middle_live_retirement_arena_tail_step()
            after = store.retirement_queue_snapshot()
            self.assertFalse(second.released)
            self.assertEqual(second.retirement_descriptor_pwrites, 0)
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
