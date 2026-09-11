from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from storage.fixed_page_primary import PAGE_MAGIC, FixedPagePrimaryStore
from storage.reclaiming_radix_primary import (
    LIFECYCLE_FREE,
    SEGMENT_DATA_PAGES,
    ReclaimingRadixPrimaryStore,
    decode_lifecycle_header,
    encode_lifecycle_header,
)
from storage.scrubbing_reclaiming_radix_primary import (
    ScrubbingReclaimingRadixPrimaryStore,
)
from storage.segmented_fixed_page_primary import SEGMENT_BUCKET_PAGES

PREFIX = 0x0102030405060700


class ReclaimingRadixPrimaryTests(unittest.TestCase):
    def _seed_stale_payload(
        self,
        store: ReclaimingRadixPrimaryStore,
        segment_id: int,
        marker: str = "stale-retired-payload",
    ) -> int:
        snapshot = store.reclamation_snapshot([segment_id])
        segment_base = snapshot["mappings"][str(segment_id)]
        self.assertIsNotNone(segment_base)
        fd = store._open()
        try:
            payload = store._pack_record(
                PAGE_MAGIC,
                int(snapshot["committed_epoch"]),
                {"keys": [marker, None, None, None]},
            )
            written = os.pwrite(
                fd,
                payload,
                store._physical_data_offset(int(segment_base), 0, 0),
            )
            self.assertEqual(len(payload), written)
            os.fsync(fd)
        finally:
            os.close(fd)
        return int(segment_base)

    def _read_first_logical_page(
        self,
        store: ReclaimingRadixPrimaryStore,
        segment_id: int,
    ) -> list[str | None]:
        store._reset_operation_state()
        fd = store._open()
        try:
            epoch, _meta, _slot = FixedPagePrimaryStore._read_super(store, fd)
            keys, _reads = store._read_logical_page(
                fd,
                segment_id * SEGMENT_BUCKET_PAGES,
                epoch,
                4,
            )
            return keys
        finally:
            store._active_counters = None
            os.close(fd)

    def test_lifecycle_header_round_trip_and_crc(self) -> None:
        encoded = encode_lifecycle_header(
            7,
            status=LIFECYCLE_FREE,
            generation=3,
            segment_id=PREFIX + 9,
            next_header_page=1234,
        )
        parsed = decode_lifecycle_header(encoded)
        self.assertIsNotNone(parsed)
        epoch, payload = parsed or (None, None)
        self.assertEqual(7, epoch)
        self.assertEqual(LIFECYCLE_FREE, payload["status"])
        self.assertEqual(3, payload["generation"])
        self.assertEqual(PREFIX + 9, payload["segment_id"])
        self.assertEqual(1234, payload["next_header_page"])

        corrupted = bytearray(encoded)
        corrupted[20] ^= 0x01
        self.assertIsNone(decode_lifecycle_header(bytes(corrupted)))

    def test_reclaim_step_obeys_budget_without_append_or_scan(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dic-v032-unit-") as tmp:
            path = Path(tmp) / "store.pages"
            ids = [PREFIX + 1, PREFIX + 2, PREFIX + 3]
            store = ScrubbingReclaimingRadixPrimaryStore(path)
            store.initialize(initial_capacity=32)
            store.materialize_owned_segment_mappings(ids)
            store.retire_owner_generation(new_generation=1)

            first = store.reclaim_step(budget=2)
            self.assertEqual(2, first.reclaimed_segments)
            self.assertEqual(1, first.remaining_segments)
            self.assertEqual(2, first.free_count)
            self.assertEqual(0, first.physical_pages_appended)
            self.assertEqual(0, first.generation_pages_scanned)
            self.assertEqual(0, first.mapping_nodes_scanned)
            self.assertEqual(0, first.logical_redo)
            self.assertLessEqual(first.radix_node_pwrites, 2)

            snapshot = store.reclamation_snapshot(ids)
            self.assertIsNotNone(snapshot["mappings"][str(ids[0])])
            self.assertIsNone(snapshot["mappings"][str(ids[1])])
            self.assertIsNone(snapshot["mappings"][str(ids[2])])

            second = store.reclaim_step(budget=2)
            self.assertEqual(1, second.reclaimed_segments)
            self.assertEqual(0, second.remaining_segments)
            self.assertEqual(3, second.free_count)
            self.assertEqual(0, second.physical_pages_appended)

            recovery = store.recover()
            self.assertEqual(0, recovery["generation_pages_scanned"])
            self.assertEqual(0, recovery["mapping_nodes_scanned"])
            self.assertEqual(0, recovery["logical_work"])

    def test_unscrubbed_reuse_exposes_stale_payload_control(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dic-v032-control-") as tmp:
            path = Path(tmp) / "control.pages"
            old_id = PREFIX + 11
            new_id = PREFIX + 12
            store = ReclaimingRadixPrimaryStore(path)
            store.initialize(initial_capacity=32)
            store.materialize_owned_segment_mappings([old_id])
            old_base = self._seed_stale_payload(store, old_id)
            store.retire_owner_generation(new_generation=1)
            store.reclaim_step(budget=1)
            reuse = store.reuse_one_mapping(new_id)
            self.assertEqual(old_base, reuse.reused_segment_base_page)
            self.assertEqual(
                "stale-retired-payload",
                self._read_first_logical_page(store, new_id)[0],
            )

    def test_scrubbed_reuse_reuses_extent_without_stale_payload(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dic-v032-candidate-") as tmp:
            path = Path(tmp) / "candidate.pages"
            old_id = PREFIX + 21
            new_id = PREFIX + 22
            store = ScrubbingReclaimingRadixPrimaryStore(path)
            store.initialize(initial_capacity=32)
            store.materialize_owned_segment_mappings([old_id])
            old_base = self._seed_stale_payload(store, old_id)
            store.retire_owner_generation(new_generation=1)
            store.reclaim_step(budget=1)
            reuse = store.reuse_one_mapping(new_id)

            self.assertEqual(old_base, reuse.reused_segment_base_page)
            self.assertEqual(SEGMENT_DATA_PAGES, reuse.data_page_scrub_pwrites)
            self.assertEqual(0, reuse.physical_pages_appended)
            self.assertEqual(1, reuse.radix_node_pwrites)
            self.assertEqual(2, reuse.fsyncs)
            self.assertEqual([None, None, None, None], self._read_first_logical_page(store, new_id))

            snapshot = store.reclamation_snapshot([old_id, new_id])
            self.assertIsNone(snapshot["mappings"][str(old_id)])
            self.assertEqual(old_base, snapshot["mappings"][str(new_id)])
            self.assertEqual(0, snapshot["free_count"])
            self.assertEqual(1, snapshot["owner_count"])


if __name__ == "__main__":
    unittest.main()
