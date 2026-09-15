from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from storage.bounded_live_tail_evacuation_retirement_descriptor_primary import (
    LiveTailEvacuationRetirementDescriptorPrimaryStore,
)
from storage.retirement_descriptor_pool import RETIREMENT_STATUS_QUEUED


class LiveTailEvacuationTests(unittest.TestCase):
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
            if index > 4096:
                raise AssertionError("fixture did not reach requested retirement queue depth")
        return index

    def test_live_physical_queue_tail_moves_into_sole_lower_free_head(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dic-v040-focused-") as tmp:
            path = Path(tmp) / "primary.pages"
            store = LiveTailEvacuationRetirementDescriptorPrimaryStore(str(path))
            store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=4096)

            self._insert_until_queue_count(store, index=0, target=3)
            initial = store.retirement_queue_snapshot()
            self.assertEqual(
                [int(row["descriptor_page"]) for row in initial["descriptors"]],
                [0, 2, 4],
            )
            self.assertEqual(int(initial["tail_page"]), 4)
            self.assertEqual(int(initial["tail_predecessor_page"]), 2)

            while int(store.retirement_queue_snapshot()["head_page"]) == 0:
                store.reclaim_step(budget=2)

            before = store.retirement_queue_snapshot()
            self.assertEqual(int(before["queue_count"]), 2)
            self.assertEqual([int(row["descriptor_page"]) for row in before["descriptors"]], [2, 4])
            self.assertEqual(int(before["descriptor_free_count"]), 1)
            self.assertEqual(int(before["descriptor_free_head_page"]), 0)
            self.assertEqual(int(before["descriptor_arena_pages"]), 6)
            self.assertEqual(int(before["tail_predecessor_page"]), 2)

            old_tail_incarnation = int(before["tail_incarnation"])
            old_free_incarnation = int(before["free_descriptors"][0]["descriptor_incarnation"])
            trace = store.evacuate_live_retirement_arena_tail_step()
            after = store.retirement_queue_snapshot()

            self.assertTrue(trace.released)
            self.assertTrue(trace.tail_was_queued)
            self.assertTrue(trace.tail_was_queue_tail)
            self.assertEqual(trace.predecessor_page, 2)
            self.assertEqual(trace.destination_page, 0)
            self.assertEqual(trace.retirement_descriptor_preads, 6)
            self.assertEqual(trace.retirement_descriptor_pwrites, 2)
            self.assertEqual(trace.retirement_descriptors_scanned, 0)
            self.assertEqual(trace.live_descriptor_relocations, 1)
            self.assertEqual(int(after["descriptor_arena_pages"]), 4)
            self.assertEqual(int(after["queue_count"]), 2)
            self.assertEqual([int(row["descriptor_page"]) for row in after["descriptors"]], [2, 0])
            self.assertEqual(int(after["tail_page"]), 0)
            self.assertEqual(int(after["tail_predecessor_page"]), 2)
            self.assertEqual(int(after["descriptor_free_count"]), 0)
            self.assertIsNone(after["descriptor_free_head_page"])

            with self.assertRaises(RuntimeError):
                store.retirement_descriptor_reference(4, old_tail_incarnation)
            with self.assertRaises(RuntimeError):
                store.retirement_descriptor_reference(
                    0,
                    old_free_incarnation,
                    expected_status=RETIREMENT_STATUS_QUEUED,
                )
            current = store.retirement_descriptor_reference(
                0,
                int(after["tail_incarnation"]),
                expected_status=RETIREMENT_STATUS_QUEUED,
            )
            self.assertIsNotNone(current["generation"])

    def test_multiple_free_descriptors_are_refused_before_descriptor_io(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dic-v040-multifree-") as tmp:
            path = Path(tmp) / "primary.pages"
            store = LiveTailEvacuationRetirementDescriptorPrimaryStore(str(path))
            store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=4096)

            self._insert_until_queue_count(store, index=0, target=4)
            while int(store.retirement_queue_snapshot()["queue_count"]) > 2:
                store.reclaim_step(budget=2)

            before = store.retirement_queue_snapshot()
            self.assertEqual(int(before["descriptor_free_count"]), 2)
            self.assertEqual(int(before["tail_page"]), 6)
            self.assertEqual(int(before["tail_predecessor_page"]), 4)

            trace = store.evacuate_live_retirement_arena_tail_step()
            after = store.retirement_queue_snapshot()
            self.assertFalse(trace.released)
            self.assertEqual(trace.retirement_descriptor_preads, 0)
            self.assertEqual(trace.retirement_descriptor_pwrites, 0)
            self.assertEqual(trace.retirement_descriptors_scanned, 0)
            self.assertEqual(trace.live_descriptor_relocations, 0)
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
