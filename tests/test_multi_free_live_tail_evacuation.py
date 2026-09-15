from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from storage.multi_free_live_tail_evacuation_retirement_descriptor_primary import (
    LiveTailEvacuationRetirementDescriptorPrimaryStore,
)
from storage.retirement_descriptor_pool import RETIREMENT_STATUS_FREE, RETIREMENT_STATUS_QUEUED


class MultiFreeLiveTailEvacuationTests(unittest.TestCase):
    @staticmethod
    def _insert_until_queue_count(
        store: LiveTailEvacuationRetirementDescriptorPrimaryStore,
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
        store: LiveTailEvacuationRetirementDescriptorPrimaryStore,
        *,
        target: int,
    ) -> None:
        while int(store.retirement_queue_snapshot()["queue_count"]) > target:
            store.reclaim_step(budget=2)

    def test_multiple_free_head_consumption_repairs_new_head(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dic-v041-focused-") as tmp:
            path = Path(tmp) / "primary.pages"
            store = LiveTailEvacuationRetirementDescriptorPrimaryStore(str(path))
            store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=4096)

            self._insert_until_queue_count(store, index=0, target=4)
            self._reclaim_to_depth(store, target=2)
            before = store.retirement_queue_snapshot()
            self.assertEqual(
                [int(row["descriptor_page"]) for row in before["descriptors"]],
                [4, 6],
            )
            self.assertEqual(
                [int(row["descriptor_page"]) for row in before["free_descriptors"]],
                [2, 0],
            )
            self.assertEqual(int(before["tail_predecessor_page"]), 4)
            self.assertEqual(int(before["descriptor_free_head_page"]), 2)
            self.assertEqual(int(before["free_descriptors"][1]["prev_descriptor_page"]), 2)

            old_tail_incarnation = int(before["tail_incarnation"])
            old_destination_incarnation = int(before["descriptor_free_head_incarnation"])
            successor_incarnation = int(before["free_descriptors"][1]["descriptor_incarnation"])

            trace = store.evacuate_live_retirement_arena_tail_step()
            after = store.retirement_queue_snapshot()

            self.assertTrue(trace.released)
            self.assertEqual(trace.retirement_descriptor_preads, 8)
            self.assertEqual(trace.retirement_descriptor_pwrites, 3)
            self.assertEqual(trace.retirement_descriptors_scanned, 0)
            self.assertEqual(trace.live_descriptor_relocations, 1)
            self.assertEqual(trace.predecessor_page, 4)
            self.assertEqual(trace.destination_page, 2)
            self.assertEqual(int(after["descriptor_arena_pages"]), 6)
            self.assertEqual(
                [int(row["descriptor_page"]) for row in after["descriptors"]],
                [4, 2],
            )
            self.assertEqual(
                [int(row["descriptor_page"]) for row in after["free_descriptors"]],
                [0],
            )
            self.assertEqual(int(after["descriptor_free_head_page"]), 0)
            self.assertEqual(int(after["descriptor_free_count"]), 1)
            self.assertEqual(int(after["tail_page"]), 2)
            self.assertEqual(int(after["tail_predecessor_page"]), 4)
            self.assertNotIn("prev_descriptor_page", after["free_descriptors"][0])
            self.assertEqual(
                int(after["free_descriptors"][0]["descriptor_incarnation"]),
                successor_incarnation,
            )

            with self.assertRaises(RuntimeError):
                store.retirement_descriptor_reference(
                    6,
                    old_tail_incarnation,
                    expected_status=RETIREMENT_STATUS_QUEUED,
                )
            with self.assertRaises(RuntimeError):
                store.retirement_descriptor_reference(
                    2,
                    old_destination_incarnation,
                    expected_status=RETIREMENT_STATUS_QUEUED,
                )
            successor = store.retirement_descriptor_reference(
                0,
                successor_incarnation,
                expected_status=RETIREMENT_STATUS_FREE,
            )
            self.assertIsNone(successor.get("prev_descriptor_page"))
            self.assertIsNone(successor.get("prev_descriptor_incarnation"))

    def test_longer_free_chain_keeps_same_relocation_work(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dic-v041-long-free-") as tmp:
            path = Path(tmp) / "primary.pages"
            store = LiveTailEvacuationRetirementDescriptorPrimaryStore(str(path))
            store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=4096)

            self._insert_until_queue_count(store, index=0, target=6)
            self._reclaim_to_depth(store, target=2)
            before = store.retirement_queue_snapshot()
            self.assertEqual(
                [int(row["descriptor_page"]) for row in before["free_descriptors"]],
                [6, 4, 2, 0],
            )
            self.assertEqual(
                [int(row["descriptor_page"]) for row in before["descriptors"]],
                [8, 10],
            )

            trace = store.evacuate_live_retirement_arena_tail_step()
            after = store.retirement_queue_snapshot()
            self.assertEqual(trace.retirement_descriptor_preads, 8)
            self.assertEqual(trace.retirement_descriptor_pwrites, 3)
            self.assertEqual(trace.retirement_descriptors_scanned, 0)
            self.assertEqual(trace.live_descriptor_relocations, 1)
            self.assertEqual(
                [int(row["descriptor_page"]) for row in after["free_descriptors"]],
                [4, 2, 0],
            )
            self.assertNotIn("prev_descriptor_page", after["free_descriptors"][0])
            self.assertEqual(int(after["free_descriptors"][1]["prev_descriptor_page"]), 4)
            self.assertEqual(int(after["free_descriptors"][2]["prev_descriptor_page"]), 2)

    def test_sole_free_geometry_preserves_v040_work(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dic-v041-sole-free-") as tmp:
            path = Path(tmp) / "primary.pages"
            store = LiveTailEvacuationRetirementDescriptorPrimaryStore(str(path))
            store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=4096)

            self._insert_until_queue_count(store, index=0, target=3)
            self._reclaim_to_depth(store, target=2)
            before = store.retirement_queue_snapshot()
            self.assertEqual(int(before["descriptor_free_count"]), 1)

            trace = store.evacuate_live_retirement_arena_tail_step()
            after = store.retirement_queue_snapshot()
            self.assertTrue(trace.released)
            self.assertEqual(trace.retirement_descriptor_preads, 6)
            self.assertEqual(trace.retirement_descriptor_pwrites, 2)
            self.assertEqual(trace.retirement_descriptors_scanned, 0)
            self.assertEqual(trace.live_descriptor_relocations, 1)
            self.assertEqual(int(after["descriptor_free_count"]), 0)
            self.assertIsNone(after["descriptor_free_head_page"])


if __name__ == "__main__":
    unittest.main()
