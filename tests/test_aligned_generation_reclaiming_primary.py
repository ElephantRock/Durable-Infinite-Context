from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from storage.aligned_generation_reclaiming_primary import (
    AlignedGenerationReclaimingPrimaryStore,
)
from storage.segmented_fixed_page_primary import SEGMENT_BUCKET_PAGES


class AlignedGenerationReclaimingPrimaryTests(unittest.TestCase):
    def test_alignment_skips_at_most_one_segment_minus_one_pages(self) -> None:
        align = AlignedGenerationReclaimingPrimaryStore._aligned_generation_base
        for page_id in range(0, 4 * SEGMENT_BUCKET_PAGES + 1):
            aligned = align(page_id)
            self.assertGreaterEqual(aligned, page_id)
            self.assertEqual(0, aligned % SEGMENT_BUCKET_PAGES)
            self.assertLess(aligned - page_id, SEGMENT_BUCKET_PAGES)

    def test_real_migration_retires_exclusive_segments_and_preserves_keys(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dic-v033-test-") as tmp:
            path = Path(tmp) / "primary.pages"
            store = AlignedGenerationReclaimingPrimaryStore(path)
            store.initialize(
                initial_capacity=32,
                max_load=0.50,
                migration_slot_budget=64,
            )

            traces = [store.insert(f"k-{index:03d}") for index in range(17)]
            trigger = traces[-1]
            self.assertTrue(trigger.migration_started)
            self.assertTrue(trigger.migration_completed)
            self.assertEqual(7, trigger.generation_alignment_padding_pages)

            snapshot = store.generation_layout_snapshot()
            self.assertEqual(1, snapshot["current"]["generation"])
            self.assertEqual(16, snapshot["current"]["base_page"])
            self.assertIsNone(snapshot["old"])
            self.assertEqual(0, snapshot["retire_generation"])
            self.assertGreater(snapshot["retire_remaining"], 0)

            while store.generation_layout_snapshot()["retire_remaining"]:
                trace = store.reclaim_step(budget=2)
                self.assertLessEqual(trace.reclaimed_segments, 2)
                self.assertEqual(0, trace.physical_pages_appended)

            for index in range(17):
                self.assertTrue(store.lookup(f"k-{index:03d}").found)

            first_free = store.generation_layout_snapshot()["free_count"]
            self.assertGreater(first_free, 0)

            second_phase = []
            next_index = 17
            while True:
                trace = store.insert(f"k-{next_index:03d}")
                second_phase.append(trace)
                next_index += 1
                snap = store.generation_layout_snapshot()
                if snap["current"]["generation"] == 2 and snap["old"] is None:
                    break
                self.assertLess(next_index, 48, "second migration failed to converge")

            second_trigger = next(trace for trace in second_phase if trace.migration_started)
            self.assertEqual(15, second_trigger.generation_alignment_padding_pages)
            self.assertTrue(any(trace.migration_completed for trace in second_phase))
            self.assertGreaterEqual(
                sum(trace.reused_free_extents for trace in second_phase), 1
            )
            self.assertGreaterEqual(
                sum(trace.data_page_scrub_pwrites for trace in second_phase), 32
            )

            snapshot = store.generation_layout_snapshot()
            self.assertEqual(2, snapshot["current"]["generation"])
            self.assertEqual(48, snapshot["current"]["base_page"])
            self.assertEqual(1, snapshot["retire_generation"])
            self.assertGreater(snapshot["retire_remaining"], 0)

            while store.generation_layout_snapshot()["retire_remaining"]:
                trace = store.reclaim_step(budget=3)
                self.assertLessEqual(trace.reclaimed_segments, 3)
                self.assertEqual(0, trace.physical_pages_appended)
            for index in range(next_index):
                self.assertTrue(store.lookup(f"k-{index:03d}").found)


if __name__ == "__main__":
    unittest.main()
