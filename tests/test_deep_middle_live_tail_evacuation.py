from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from storage.deep_middle_live_tail_evacuation_retirement_descriptor_primary import (
    DeepMiddleLiveTailEvacuationRetirementDescriptorPrimaryStore,
)
from storage.middle_live_tail_evacuation_retirement_descriptor_primary import (
    MiddleLiveTailEvacuationRetirementDescriptorPrimaryStore,
)
from storage.retirement_descriptor_pool import RETIREMENT_STATUS_QUEUED


class DeepMiddleLiveTailEvacuationTests(unittest.TestCase):
    @staticmethod
    def _insert_until_queue_count(store, *, index: int, target: int) -> int:
        while int(store.retirement_queue_snapshot()["queue_count"]) < target:
            store.insert(f"k-{index:04d}")
            index += 1
            if index > 65536:
                raise AssertionError("fixture did not reach requested retirement queue depth")
        if int(store.retirement_queue_snapshot()["queue_count"]) != target:
            raise AssertionError("fixture overshot requested retirement queue depth")
        return index

    @staticmethod
    def _reclaim_to_depth(store, *, target: int) -> None:
        while int(store.retirement_queue_snapshot()["queue_count"]) > target:
            trace = store.reclaim_step(budget=2)
            if int(trace.retirement_descriptors_scanned) != 0:
                raise AssertionError("fixture reclaim scanned retirement descriptors")
        if int(store.retirement_queue_snapshot()["queue_count"]) != target:
            raise AssertionError("fixture reclaim overshot requested depth")

    def _build_deep_fixture(
        self,
        path: Path,
        store_cls=DeepMiddleLiveTailEvacuationRetirementDescriptorPrimaryStore,
    ):
        store = store_cls(str(path))
        store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=4096)
        index = self._insert_until_queue_count(store, index=0, target=5)
        initial = store.retirement_queue_snapshot()
        self.assertEqual(
            [int(row["descriptor_page"]) for row in initial["descriptors"]],
            [0, 2, 4, 6, 8],
        )

        self._reclaim_to_depth(store, target=3)
        reclaimed = store.retirement_queue_snapshot()
        self.assertEqual(
            [int(row["descriptor_page"]) for row in reclaimed["descriptors"]],
            [4, 6, 8],
        )
        self.assertEqual(
            [int(row["descriptor_page"]) for row in reclaimed["free_descriptors"]],
            [2, 0],
        )

        index = self._insert_until_queue_count(store, index=index, target=4)
        ready = store.retirement_queue_snapshot()
        self.assertEqual(
            [int(row["descriptor_page"]) for row in ready["descriptors"]],
            [4, 6, 8, 2],
        )
        self.assertEqual(
            [int(row["descriptor_page"]) for row in ready["free_descriptors"]],
            [0],
        )
        return store, index

    def test_append_and_reuse_publish_two_hop_physical_tail_authority(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dic-v043-authority-") as tmp:
            store, _index = self._build_deep_fixture(Path(tmp) / "primary.pages")
            row = store.retirement_queue_snapshot()
            self.assertEqual(int(row["physical_tail_page"]), 8)
            self.assertEqual(int(row["physical_tail_predecessor_page"]), 6)
            self.assertEqual(
                int(row["physical_tail_predecessor_predecessor_page"]),
                4,
            )
            self.assertEqual(int(row["head_page"]), 4)
            self.assertEqual(int(row["tail_page"]), 2)
            self.assertEqual(int(row["tail_predecessor_page"]), 8)

    def test_deep_physical_tail_relocates_with_explicit_reverse_hop_validation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dic-v043-relocate-") as tmp:
            store, _index = self._build_deep_fixture(Path(tmp) / "primary.pages")
            before = store.retirement_queue_snapshot()
            old_tail_incarnation = int(before["physical_tail_incarnation"])
            old_destination_incarnation = int(before["descriptor_free_head_incarnation"])
            successor_incarnation = int(before["tail_incarnation"])

            trace = store.evacuate_deep_middle_live_retirement_arena_tail_step()
            after = store.retirement_queue_snapshot()

            self.assertTrue(trace.released)
            self.assertEqual(trace.retirement_descriptor_preads, 10)
            self.assertEqual(trace.retirement_descriptor_pwrites, 2)
            self.assertEqual(trace.retirement_descriptors_scanned, 0)
            self.assertEqual(trace.live_descriptor_relocations, 1)
            self.assertEqual(trace.physical_tail_page, 8)
            self.assertEqual(trace.predecessor_page, 6)
            self.assertEqual(trace.predecessor_predecessor_page, 4)
            self.assertEqual(trace.successor_page, 2)
            self.assertEqual(trace.destination_page, 0)
            self.assertEqual(trace.new_physical_tail_page, 6)
            self.assertEqual(trace.new_physical_tail_predecessor_page, 4)

            self.assertEqual(
                [int(row["descriptor_page"]) for row in after["descriptors"]],
                [4, 6, 0, 2],
            )
            self.assertEqual(after["free_descriptors"], [])
            self.assertEqual(int(after["descriptor_arena_pages"]), 8)
            self.assertEqual(int(after["head_page"]), 4)
            self.assertEqual(int(after["tail_page"]), 2)
            self.assertEqual(int(after["tail_incarnation"]), successor_incarnation)
            self.assertEqual(int(after["tail_predecessor_page"]), 0)
            self.assertEqual(int(after["physical_tail_page"]), 6)
            self.assertEqual(int(after["physical_tail_predecessor_page"]), 4)
            self.assertIsNone(after["physical_tail_predecessor_predecessor_page"])

            with self.assertRaises(RuntimeError):
                store.retirement_descriptor_reference(
                    8,
                    old_tail_incarnation,
                    expected_status=RETIREMENT_STATUS_QUEUED,
                )
            with self.assertRaises(RuntimeError):
                store.retirement_descriptor_reference(
                    0,
                    old_destination_incarnation,
                    expected_status=RETIREMENT_STATUS_QUEUED,
                )

    def test_append_after_relocation_uses_logical_tail_predecessor_for_second_reverse_hop(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dic-v043-post-relocation-append-") as tmp:
            store, index = self._build_deep_fixture(Path(tmp) / "primary.pages")
            trace = store.evacuate_deep_middle_live_retirement_arena_tail_step()
            self.assertTrue(trace.released)
            before = store.retirement_queue_snapshot()
            self.assertEqual(int(before["tail_page"]), 2)
            self.assertEqual(int(before["tail_predecessor_page"]), 0)
            self.assertEqual(int(before["physical_tail_page"]), 6)
            self.assertEqual(int(before["physical_tail_predecessor_page"]), 4)

            index = self._insert_until_queue_count(store, index=index, target=5)
            self.assertGreater(index, 0)
            after = store.retirement_queue_snapshot()
            self.assertEqual(int(after["physical_tail_page"]), 8)
            self.assertEqual(int(after["physical_tail_predecessor_page"]), 2)
            self.assertEqual(
                int(after["physical_tail_predecessor_predecessor_page"]),
                0,
            )

    def test_v042_control_refuses_four_node_interior_geometry_without_descriptor_io(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dic-v043-v042-control-") as tmp:
            store, _index = self._build_deep_fixture(
                Path(tmp) / "primary.pages",
                MiddleLiveTailEvacuationRetirementDescriptorPrimaryStore,
            )
            before = store.retirement_queue_snapshot()
            trace = store.evacuate_middle_live_retirement_arena_tail_step()
            after = store.retirement_queue_snapshot()
            self.assertFalse(trace.released)
            self.assertEqual(trace.retirement_descriptor_preads, 0)
            self.assertEqual(trace.retirement_descriptor_pwrites, 0)
            self.assertEqual(trace.retirement_descriptors_scanned, 0)
            self.assertEqual(trace.live_descriptor_relocations, 0)
            self.assertEqual(before, after)

    def test_deeper_prefix_is_refused_before_descriptor_io(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dic-v043-deeper-refusal-") as tmp:
            path = Path(tmp) / "primary.pages"
            store = DeepMiddleLiveTailEvacuationRetirementDescriptorPrimaryStore(str(path))
            store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=4096)
            index = self._insert_until_queue_count(store, index=0, target=6)
            self._reclaim_to_depth(store, target=4)
            index = self._insert_until_queue_count(store, index=index, target=5)
            self.assertGreater(index, 0)
            before = store.retirement_queue_snapshot()
            self.assertEqual(int(before["queue_count"]), 5)
            self.assertEqual(int(before["descriptor_free_count"]), 1)
            trace = store.evacuate_deep_middle_live_retirement_arena_tail_step()
            after = store.retirement_queue_snapshot()
            self.assertFalse(trace.released)
            self.assertEqual(trace.retirement_descriptor_preads, 0)
            self.assertEqual(trace.retirement_descriptor_pwrites, 0)
            self.assertEqual(trace.retirement_descriptors_scanned, 0)
            self.assertEqual(trace.live_descriptor_relocations, 0)
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
