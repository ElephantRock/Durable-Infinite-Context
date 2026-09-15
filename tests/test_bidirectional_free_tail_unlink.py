from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from storage.bidirectional_retirement_descriptor_primary import (
    BidirectionalRetirementDescriptorPrimaryStore,
)
from storage.retirement_descriptor_pool import RETIREMENT_STATUS_FREE


class BidirectionalFreeTailUnlinkTests(unittest.TestCase):
    @staticmethod
    def _insert_until_queue_count(
        store: BidirectionalRetirementDescriptorPrimaryStore,
        *,
        index: int,
        target: int,
    ) -> int:
        while int(store.retirement_queue_snapshot()["queue_count"]) < target:
            store.insert(f"k-{index:04d}")
            index += 1
            if index > 1024:
                raise AssertionError("fixture did not reach requested retirement queue depth")
        return index

    def test_non_head_physical_free_tail_unlinks_without_free_list_walk(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dic-v039-focused-") as tmp:
            path = Path(tmp) / "primary.pages"
            store = BidirectionalRetirementDescriptorPrimaryStore(str(path))
            store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=4096)

            index = self._insert_until_queue_count(store, index=0, target=3)
            initial = store.retirement_queue_snapshot()
            self.assertEqual(
                [int(row["descriptor_page"]) for row in initial["descriptors"]],
                [0, 2, 4],
            )

            while int(store.retirement_queue_snapshot()["queue_count"]) > 1:
                store.reclaim_step(budget=2)
            one_live = store.retirement_queue_snapshot()
            self.assertEqual(int(one_live["head_page"]), 4)
            self.assertEqual(int(one_live["descriptor_free_count"]), 2)

            index = self._insert_until_queue_count(store, index=index, target=3)
            reused = store.retirement_queue_snapshot()
            self.assertEqual(
                [int(row["descriptor_page"]) for row in reused["descriptors"]],
                [4, 2, 0],
            )

            stale_page = int(reused["head_page"])
            stale_incarnation = int(reused["head_incarnation"])
            while int(store.retirement_queue_snapshot()["head_page"]) == 4:
                store.reclaim_step(budget=2)
            while int(store.retirement_queue_snapshot()["head_page"]) == 2:
                store.reclaim_step(budget=2)

            buried = store.retirement_queue_snapshot()
            self.assertEqual(int(buried["queue_count"]), 1)
            self.assertEqual(int(buried["descriptor_arena_pages"]), 6)
            self.assertEqual(int(buried["descriptor_free_count"]), 2)
            self.assertEqual(int(buried["descriptor_free_head_page"]), 2)
            free_rows = buried["free_descriptors"]
            self.assertEqual([int(row["descriptor_page"]) for row in free_rows], [2, 4])
            self.assertNotIn("prev_descriptor_page", free_rows[0])
            self.assertEqual(int(free_rows[1]["prev_descriptor_page"]), 2)
            self.assertEqual(
                int(free_rows[1]["prev_descriptor_incarnation"]),
                int(free_rows[0]["descriptor_incarnation"]),
            )

            trace = store.shrink_retirement_arena_tail_step()
            after = store.retirement_queue_snapshot()
            self.assertTrue(trace.released)
            self.assertTrue(trace.tail_was_free)
            self.assertFalse(trace.tail_was_free_head)
            self.assertEqual(trace.predecessor_page, 2)
            self.assertEqual(trace.retirement_descriptor_preads, 4)
            self.assertEqual(trace.retirement_descriptor_pwrites, 1)
            self.assertEqual(trace.retirement_descriptors_scanned, 0)
            self.assertEqual(trace.candidate_relocations, 0)
            self.assertEqual(int(after["queue_count"]), 1)
            self.assertEqual(int(after["descriptor_arena_pages"]), 4)
            self.assertEqual(int(after["descriptor_free_count"]), 1)
            self.assertEqual(int(after["descriptor_free_head_page"]), 2)

            with self.assertRaises(RuntimeError):
                store.retirement_descriptor_reference(
                    stale_page,
                    stale_incarnation,
                    expected_status=RETIREMENT_STATUS_FREE,
                )


if __name__ == "__main__":
    unittest.main()
