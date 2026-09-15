from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from storage.live_tail_evacuation_retirement_descriptor_primary import (
    LiveTailEvacuationRetirementDescriptorPrimaryStore,
)


class LiveTailPredecessorMetadataTests(unittest.TestCase):
    def test_tail_predecessor_tracks_enqueue_and_is_ignored_for_singleton(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dic-v040-tailpred-") as tmp:
            path = Path(tmp) / "primary.pages"
            store = LiveTailEvacuationRetirementDescriptorPrimaryStore(str(path))
            store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=4096)

            index = 0
            while int(store.retirement_queue_snapshot()["queue_count"]) < 3:
                store.insert(f"k-{index:04d}")
                index += 1
                if index > 4096:
                    raise AssertionError("fixture did not reach three retirement descriptors")

            three = store.retirement_queue_snapshot()
            self.assertEqual([int(row["descriptor_page"]) for row in three["descriptors"]], [0, 2, 4])
            self.assertEqual(int(three["tail_page"]), 4)
            self.assertEqual(int(three["tail_predecessor_page"]), 2)
            self.assertEqual(
                int(three["tail_predecessor_incarnation"]),
                int(three["descriptors"][-2]["descriptor_incarnation"]),
            )

            while int(store.retirement_queue_snapshot()["queue_count"]) > 1:
                store.reclaim_step(budget=1_000_000)

            one = store.retirement_queue_snapshot()
            self.assertEqual(int(one["queue_count"]), 1)
            self.assertIsNone(one["tail_predecessor_page"])
            self.assertIsNone(one["tail_predecessor_incarnation"])

            while int(store.retirement_queue_snapshot()["queue_count"]) < 2:
                store.insert(f"k-{index:04d}")
                index += 1
                if index > 8192:
                    raise AssertionError("fixture did not enqueue after singleton")

            two = store.retirement_queue_snapshot()
            self.assertEqual(int(two["queue_count"]), 2)
            self.assertEqual(
                int(two["tail_predecessor_page"]),
                int(two["descriptors"][-2]["descriptor_page"]),
            )
            self.assertEqual(
                int(two["tail_predecessor_incarnation"]),
                int(two["descriptors"][-2]["descriptor_incarnation"]),
            )


if __name__ == "__main__":
    unittest.main()
