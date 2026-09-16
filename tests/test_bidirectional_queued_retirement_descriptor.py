from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from storage.bidirectional_queued_retirement_descriptor_primary import (
    BidirectionalQueuedRetirementDescriptorPrimaryStore,
)
from storage.deep_middle_live_tail_evacuation_retirement_descriptor_primary import (
    DeepMiddleLiveTailEvacuationRetirementDescriptorPrimaryStore,
)


SEGMENT_BUDGET = 2


def _key(index: int) -> str:
    return f"k-{index:04d}"


def _insert_until_queue_count(store, *, index: int, target: int, cap: int = 65536) -> int:
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
        raise AssertionError("setup reclaim overshot target depth")


def _prepare_deeper_fixture(path: Path, store_cls, *, queue_depth: int = 5):
    if queue_depth < 5:
        raise ValueError("v0.44 deeper fixture requires queue depth >= 5")
    store = store_cls(str(path))
    store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=4096)
    index = _insert_until_queue_count(store, index=0, target=queue_depth + 1)
    initial = store.retirement_queue_snapshot()
    expected_initial = list(range(0, 2 * (queue_depth + 1), 2))
    if [int(row["descriptor_page"]) for row in initial["descriptors"]] != expected_initial:
        raise AssertionError("initial descriptor layout drifted")
    _reclaim_to_depth(store, target=queue_depth - 1)
    before_reuse = store.retirement_queue_snapshot()
    if [int(row["descriptor_page"]) for row in before_reuse["free_descriptors"]] != [2, 0]:
        raise AssertionError("pre-reuse FREE topology drifted")
    index = _insert_until_queue_count(store, index=index, target=queue_depth)
    ready = store.retirement_queue_snapshot()
    expected_live = list(range(4, 2 * (queue_depth + 1), 2)) + [2]
    if [int(row["descriptor_page"]) for row in ready["descriptors"]] != expected_live:
        raise AssertionError(f"ready live queue drifted: {expected_live}")
    if [int(row["descriptor_page"]) for row in ready["free_descriptors"]] != [0]:
        raise AssertionError("ready FREE topology drifted")
    return store, index, ready


class BidirectionalQueuedRetirementDescriptorTests(unittest.TestCase):
    def test_queued_predecessor_edges_survive_append_reclaim_and_reuse(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dic-v044-topology-") as tmp:
            path = Path(tmp) / "primary.pages"
            store = BidirectionalQueuedRetirementDescriptorPrimaryStore(str(path))
            store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=4096)
            index = _insert_until_queue_count(store, index=0, target=6)
            appended = store.retirement_queue_snapshot()
            self.assertTrue(appended["queued_predecessor_consistent"])
            self.assertEqual(
                [row["queued_prev_page"] for row in appended["descriptors"]],
                [None, 0, 2, 4, 6, 8],
            )

            _reclaim_to_depth(store, target=4)
            reclaimed = store.retirement_queue_snapshot()
            self.assertEqual(
                [int(row["descriptor_page"]) for row in reclaimed["descriptors"]],
                [4, 6, 8, 10],
            )
            self.assertEqual(
                [row["queued_prev_page"] for row in reclaimed["descriptors"]],
                [None, 4, 6, 8],
            )

            _insert_until_queue_count(store, index=index, target=5)
            reused = store.retirement_queue_snapshot()
            self.assertEqual(
                [int(row["descriptor_page"]) for row in reused["descriptors"]],
                [4, 6, 8, 10, 2],
            )
            self.assertEqual(
                [row["queued_prev_page"] for row in reused["descriptors"]],
                [None, 4, 6, 8, 10],
            )
            self.assertTrue(reused["queued_predecessor_consistent"])

    def test_v043_control_refuses_five_node_deeper_prefix_before_descriptor_io(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dic-v044-control-") as tmp:
            path = Path(tmp) / "primary.pages"
            store, _index, before = _prepare_deeper_fixture(
                path, DeepMiddleLiveTailEvacuationRetirementDescriptorPrimaryStore
            )
            self.assertEqual(
                [int(row["descriptor_page"]) for row in before["descriptors"]],
                [4, 6, 8, 10, 2],
            )
            trace = store.evacuate_deep_middle_live_retirement_arena_tail_step()
            after = store.retirement_queue_snapshot()
            self.assertFalse(trace.released)
            self.assertEqual(trace.retirement_descriptor_preads, 0)
            self.assertEqual(trace.retirement_descriptor_pwrites, 0)
            self.assertEqual(before, after)

    def test_v044_relocates_five_node_deeper_prefix_with_bounded_direct_work(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dic-v044-relocate-") as tmp:
            path = Path(tmp) / "primary.pages"
            store, _index, before = _prepare_deeper_fixture(
                path, BidirectionalQueuedRetirementDescriptorPrimaryStore
            )
            old_tail_inc = int(before["physical_tail_incarnation"])
            old_dest_inc = int(before["descriptor_free_head_incarnation"])
            self.assertEqual(int(before["physical_tail_page"]), 10)
            self.assertEqual(
                [row["queued_prev_page"] for row in before["descriptors"]],
                [None, 4, 6, 8, 10],
            )

            trace = store.evacuate_bidirectional_queued_live_retirement_arena_tail_step()
            after = store.retirement_queue_snapshot()
            self.assertTrue(trace.released)
            self.assertEqual(trace.retirement_descriptor_preads, 10)
            self.assertEqual(trace.retirement_descriptor_pwrites, 2)
            self.assertEqual(trace.queued_predecessor_preads, 8)
            self.assertEqual(trace.queued_predecessor_pwrites, 2)
            self.assertEqual(trace.retirement_descriptors_scanned, 0)
            self.assertEqual(trace.queued_predecessors_scanned, 0)
            self.assertEqual(trace.live_descriptor_relocations, 1)
            self.assertEqual(trace.retirement_arena_pages_before, 12)
            self.assertEqual(trace.retirement_arena_pages_after, 10)
            self.assertEqual(trace.retirement_arena_bytes_released, 8192)
            self.assertEqual(
                [int(row["descriptor_page"]) for row in after["descriptors"]],
                [4, 6, 8, 0, 2],
            )
            self.assertEqual(
                [row["queued_prev_page"] for row in after["descriptors"]],
                [None, 4, 6, 8, 0],
            )
            self.assertEqual(after["free_descriptors"], [])
            self.assertEqual(int(after["physical_tail_page"]), 8)
            self.assertEqual(int(after["physical_tail_predecessor_page"]), 6)
            self.assertEqual(int(after["tail_page"]), 2)
            self.assertEqual(int(after["tail_predecessor_page"]), 0)
            self.assertTrue(after["queued_predecessor_consistent"])

            with self.assertRaises(RuntimeError):
                store.queued_predecessor_reference(10, old_tail_inc)
            with self.assertRaises(RuntimeError):
                store.queued_predecessor_reference(0, old_dest_inc)

    def test_direct_work_does_not_grow_with_live_prefix_depth(self) -> None:
        rows = []
        for depth in (5, 6, 7, 8):
            with tempfile.TemporaryDirectory(prefix=f"dic-v044-scale-{depth}-") as tmp:
                path = Path(tmp) / "primary.pages"
                store, _index, before = _prepare_deeper_fixture(
                    path,
                    BidirectionalQueuedRetirementDescriptorPrimaryStore,
                    queue_depth=depth,
                )
                trace = store.evacuate_bidirectional_queued_live_retirement_arena_tail_step()
                after = store.retirement_queue_snapshot()
                self.assertTrue(trace.released)
                self.assertTrue(after["queued_predecessor_consistent"])
                rows.append(
                    (
                        depth,
                        trace.retirement_descriptor_preads,
                        trace.retirement_descriptor_pwrites,
                        trace.queued_predecessor_preads,
                        trace.queued_predecessor_pwrites,
                        trace.retirement_descriptors_scanned,
                        trace.queued_predecessors_scanned,
                    )
                )
        self.assertEqual(
            rows,
            [(depth, 10, 2, 8, 2, 0, 0) for depth in (5, 6, 7, 8)],
        )


if __name__ == "__main__":
    unittest.main()
