from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from storage.queued_generation_reclaiming_primary import (
    QueuedGenerationReclaimingPrimaryStore,
    decode_retirement_descriptor,
    encode_retirement_descriptor,
)


class QueuedGenerationReclaimingPrimaryTests(unittest.TestCase):
    def test_retirement_descriptor_round_trip(self) -> None:
        record = encode_retirement_descriptor(
            7,
            generation=3,
            cursor_header_page=101,
            remaining_segments=5,
            next_descriptor_page=211,
        )
        parsed = decode_retirement_descriptor(record)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        epoch, payload = parsed
        self.assertEqual(7, epoch)
        self.assertEqual(3, payload["generation"])
        self.assertEqual(101, payload["cursor_header_page"])
        self.assertEqual(5, payload["remaining_segments"])
        self.assertEqual(211, payload["next_descriptor_page"])

    def test_real_generations_queue_without_draining_previous_retirements(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dic-v034-test-") as tmp:
            path = Path(tmp) / "primary.pages"
            store = QueuedGenerationReclaimingPrimaryStore(str(path))
            store.initialize(
                initial_capacity=32,
                max_load=0.50,
                migration_slot_budget=256,
            )

            migration_traces = []
            for index in range(65):
                trace = store.insert(f"k-{index:03d}")
                if trace.migration_completed:
                    migration_traces.append(trace)

            self.assertEqual(3, len(migration_traces))
            self.assertEqual([64, 128, 256], [t.current_capacity for t in migration_traces])
            queue = store.retirement_queue_snapshot()
            self.assertEqual(3, queue["queue_count"])
            self.assertEqual(
                [0, 1, 2],
                [row["generation"] for row in queue["descriptors"]],
            )
            self.assertEqual(
                [1, 1, 1],
                [t.retirement_descriptors_enqueued for t in migration_traces],
            )
            self.assertEqual(2, migration_traces[0].retirement_descriptor_pages_appended)
            self.assertEqual(1, migration_traces[0].retirement_descriptor_pwrites)
            self.assertEqual(2, migration_traces[1].retirement_descriptor_pages_appended)
            self.assertEqual(2, migration_traces[1].retirement_descriptor_pwrites)
            self.assertLessEqual(migration_traces[1].retirement_descriptor_preads, 2)

            for index in range(65):
                self.assertTrue(store.lookup(f"k-{index:03d}").found)

            reclaimed_generations = []
            max_reclaimed = 0
            while store.retirement_queue_snapshot()["queue_count"]:
                before = store.retirement_queue_snapshot()
                head_generation = before["descriptors"][0]["generation"]
                trace = store.reclaim_step(budget=2)
                reclaimed_generations.append(trace.retired_generation)
                max_reclaimed = max(max_reclaimed, trace.reclaimed_segments)
                self.assertEqual(head_generation, trace.retired_generation)
                self.assertLessEqual(trace.reclaimed_segments, 2)
                self.assertLessEqual(trace.retirement_descriptor_preads, 2)
                self.assertLessEqual(trace.retirement_descriptor_pwrites, 1)
                self.assertEqual(0, trace.physical_pages_appended)
                self.assertEqual(0, trace.retirement_descriptors_scanned)
                self.assertEqual(0, trace.generation_pages_scanned)
                self.assertEqual(0, trace.mapping_nodes_scanned)
                self.assertEqual(0, trace.logical_redo)

            self.assertLessEqual(max_reclaimed, 2)
            self.assertEqual(sorted(set(reclaimed_generations)), [0, 1, 2])
            self.assertGreater(store.retirement_queue_snapshot()["free_count"], 0)
            for index in range(65):
                self.assertTrue(store.lookup(f"k-{index:03d}").found)

            reuse_seen = False
            for index in range(65, 129):
                trace = store.insert(f"k-{index:03d}")
                if trace.reused_free_extents:
                    reuse_seen = True
                    self.assertGreaterEqual(trace.data_page_scrub_pwrites, 32)
            self.assertTrue(reuse_seen)
            for index in range(129):
                self.assertTrue(store.lookup(f"k-{index:03d}").found)


if __name__ == "__main__":
    unittest.main()
