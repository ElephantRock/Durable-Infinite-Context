from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from storage.bidirectional_queued_retirement_descriptor_primary import (
    BidirectionalQueuedRetirementDescriptorPrimaryStore,
)
from storage.multi_free_bidirectional_queued_retirement_descriptor_primary import (
    MultiFreeBidirectionalQueuedRetirementDescriptorPrimaryStore,
)
from storage.retirement_descriptor_pool import RETIREMENT_STATUS_FREE, RETIREMENT_STATUS_QUEUED


SEGMENT_BUDGET = 2


def _key(index: int) -> str:
    return f"k-{index:04d}"


def _insert_until_queue_count(store, *, index: int, target: int, cap: int = 131072) -> int:
    while int(store.retirement_queue_snapshot()["queue_count"]) < target:
        if index >= cap:
            raise AssertionError(f"did not reach retirement queue count {target}")
        store.insert(_key(index))
        index += 1
    if int(store.retirement_queue_snapshot()["queue_count"]) != target:
        raise AssertionError("retirement queue overshot requested count")
    return index


def _reclaim_to_depth(store, *, target: int) -> None:
    while int(store.retirement_queue_snapshot()["queue_count"]) > target:
        trace = store.reclaim_step(budget=SEGMENT_BUDGET)
        if int(trace.retirement_descriptors_scanned) != 0:
            raise AssertionError("setup reclaim scanned retirement descriptors")
    if int(store.retirement_queue_snapshot()["queue_count"]) != target:
        raise AssertionError("setup reclaim overshot target")


def _prepare(store, *, depth: int = 5, free_depth: int = 2):
    if depth < 5 or free_depth < 2:
        raise ValueError("v0.45 fixture requires depth >= 5 and free_depth >= 2")
    store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=4096)
    index = _insert_until_queue_count(store, index=0, target=depth + free_depth)
    _reclaim_to_depth(store, target=depth - 1)
    index = _insert_until_queue_count(store, index=index, target=depth)
    snapshot = store.retirement_queue_snapshot()
    expected_queue = list(range(2 * (free_depth + 1), 2 * (depth + free_depth), 2)) + [
        2 * free_depth
    ]
    expected_free = list(range(2 * (free_depth - 1), -1, -2))
    if [int(row["descriptor_page"]) for row in snapshot["descriptors"]] != expected_queue:
        raise AssertionError(f"v0.45 queue fixture drifted: {expected_queue}")
    if [int(row["descriptor_page"]) for row in snapshot["free_descriptors"]] != expected_free:
        raise AssertionError(f"v0.45 FREE fixture drifted: {expected_free}")
    return snapshot, index


class MultiFreeBidirectionalQueuedRetirementDescriptorTests(unittest.TestCase):
    def test_v044_control_refuses_multiple_free_before_descriptor_io(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dic-v045-control-") as tmp:
            path = Path(tmp) / "primary.pages"
            store = BidirectionalQueuedRetirementDescriptorPrimaryStore(str(path))
            before, _index = _prepare(store)
            trace = store.evacuate_bidirectional_queued_live_retirement_arena_tail_step()
            after = store.retirement_queue_snapshot()
            self.assertFalse(trace.released)
            self.assertEqual(trace.retirement_descriptor_preads, 0)
            self.assertEqual(trace.retirement_descriptor_pwrites, 0)
            self.assertEqual(trace.queued_predecessor_preads, 0)
            self.assertEqual(trace.queued_predecessor_pwrites, 0)
            self.assertEqual(before, after)

    def test_v045_composes_queue_and_free_neighbor_repairs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dic-v045-candidate-") as tmp:
            path = Path(tmp) / "primary.pages"
            store = MultiFreeBidirectionalQueuedRetirementDescriptorPrimaryStore(str(path))
            before, _index = _prepare(store)
            self.assertEqual(
                [int(row["descriptor_page"]) for row in before["descriptors"]],
                [6, 8, 10, 12, 4],
            )
            self.assertEqual(
                [int(row["descriptor_page"]) for row in before["free_descriptors"]],
                [2, 0],
            )
            tail_inc = int(before["physical_tail_incarnation"])
            destination_inc = int(before["descriptor_free_head_incarnation"])
            new_free_inc = int(before["free_descriptors"][1]["descriptor_incarnation"])

            trace = store.evacuate_multi_free_bidirectional_queued_live_retirement_arena_tail_step()
            after = store.retirement_queue_snapshot()

            self.assertTrue(trace.released)
            self.assertEqual(trace.retirement_descriptor_preads, 12)
            self.assertEqual(trace.retirement_descriptor_pwrites, 3)
            self.assertEqual(trace.queued_predecessor_preads, 8)
            self.assertEqual(trace.queued_predecessor_pwrites, 2)
            self.assertEqual(trace.retirement_descriptors_scanned, 0)
            self.assertEqual(trace.queued_predecessors_scanned, 0)
            self.assertEqual(trace.live_descriptor_relocations, 1)
            self.assertEqual(trace.retirement_arena_bytes_released, 8192)
            self.assertEqual(
                [int(row["descriptor_page"]) for row in after["descriptors"]],
                [6, 8, 10, 2, 4],
            )
            self.assertEqual(
                [int(row["descriptor_page"]) for row in after["free_descriptors"]],
                [0],
            )
            self.assertTrue(after["queued_predecessor_consistent"])
            self.assertEqual(int(after["descriptor_free_head_incarnation"]), new_free_inc)
            free_head = store.retirement_descriptor_reference(
                0, new_free_inc, expected_status=RETIREMENT_STATUS_FREE
            )
            self.assertEqual(store._free_prev(free_head), (None, None))

            with self.assertRaises(RuntimeError):
                store.retirement_descriptor_reference(
                    12, tail_inc, expected_status=RETIREMENT_STATUS_QUEUED
                )
            with self.assertRaises(RuntimeError):
                store.queued_predecessor_reference(12, tail_inc)
            with self.assertRaises(RuntimeError):
                store.retirement_descriptor_reference(
                    2, destination_inc, expected_status=RETIREMENT_STATUS_QUEUED
                )
            with self.assertRaises(RuntimeError):
                store.queued_predecessor_reference(2, destination_inc)


if __name__ == "__main__":
    unittest.main()
