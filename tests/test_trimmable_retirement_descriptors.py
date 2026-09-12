from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from storage.reclaiming_radix_primary import NULL_PAGE
from storage.retirement_descriptor_pool import RETIREMENT_STATUS_FREE
from storage.trimmable_retirement_descriptor_primary import (
    TrimmableRetirementDescriptorPrimaryStore,
)


class TrimmableRetirementDescriptorTests(unittest.TestCase):
    @staticmethod
    def _drain(store: TrimmableRetirementDescriptorPrimaryStore) -> None:
        while store.retirement_queue_snapshot()["queue_count"]:
            store.reclaim_step(budget=2)

    def test_tail_release_shrinks_one_pair_and_stops_at_non_tail(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dic-v036-test-trim-") as tmp:
            path = Path(tmp) / "primary.pages"
            store = TrimmableRetirementDescriptorPrimaryStore(str(path))
            store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=4096)
            for index in range(65):
                store.insert(f"k-{index:03d}")
            self._drain(store)
            before = store.retirement_queue_snapshot()
            before_meta = store.meta_snapshot()
            self.assertEqual(before["descriptor_pool_count"], 3)
            released = before["free_descriptors"][0]

            trace = store.trim_retirement_descriptor_tail_step()
            after = store.retirement_queue_snapshot()
            after_meta = store.meta_snapshot()
            self.assertTrue(trace.tail_release_eligible)
            self.assertEqual(trace.retirement_descriptor_preads, 2)
            self.assertEqual(trace.physical_pages_released, 2)
            self.assertEqual(after["descriptor_pool_count"], 2)
            self.assertEqual(
                int(before_meta["file_size_bytes"]) - int(after_meta["file_size_bytes"]),
                2 * 4096,
            )
            with self.assertRaises(RuntimeError):
                store.retirement_descriptor_reference(
                    int(released["descriptor_page"]),
                    int(released["descriptor_incarnation"]),
                    expected_status=RETIREMENT_STATUS_FREE,
                )

            blocked = store.trim_retirement_descriptor_tail_step()
            self.assertFalse(blocked.tail_release_eligible)
            self.assertEqual(blocked.retirement_descriptor_preads, 2)
            self.assertEqual(store.retirement_queue_snapshot()["descriptor_pool_count"], 2)

    def test_fresh_descriptor_incarnation_does_not_reset_after_release(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dic-v036-test-incarnation-") as tmp:
            path = Path(tmp) / "primary.pages"
            store = TrimmableRetirementDescriptorPrimaryStore(str(path))
            store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=4096)
            for index in range(17):
                store.insert(f"k-{index:03d}")
            self._drain(store)
            free = store.retirement_queue_snapshot()
            self.assertEqual(
                [int(row["descriptor_incarnation"]) for row in free["free_descriptors"]],
                [1],
            )
            store.trim_retirement_descriptor_tail_step()
            self.assertEqual(store.retirement_queue_snapshot()["descriptor_pool_count"], 0)

            for index in range(17, 33):
                store.insert(f"k-{index:03d}")
            queued = store.retirement_queue_snapshot()
            self.assertEqual(queued["queue_count"], 1)
            self.assertEqual(int(queued["descriptors"][0]["descriptor_incarnation"]), 2)

    def test_global_incarnation_counter_has_explicit_uint64_exhaustion(self) -> None:
        meta = {"retirement_descriptor_incarnation_counter": NULL_PAGE - 1}
        self.assertEqual(
            TrimmableRetirementDescriptorPrimaryStore._claim_descriptor_incarnation(meta),
            NULL_PAGE,
        )
        with self.assertRaises(OverflowError):
            TrimmableRetirementDescriptorPrimaryStore._claim_descriptor_incarnation(meta)


if __name__ == "__main__":
    unittest.main()
