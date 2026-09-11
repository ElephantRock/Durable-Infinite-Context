from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any, Callable

from storage.fixed_page_primary import PrimaryAdmissionExhausted, _Counters
from storage.reclaiming_radix_primary import (
    LIFECYCLE_COPIES,
    LIFECYCLE_FREE,
    LIFECYCLE_OWNED,
    SEGMENT_DATA_PAGES,
)
from storage.scrubbing_reclaiming_radix_primary import ScrubbingReclaimingRadixPrimaryStore
from storage.segmented_fixed_page_primary import (
    LOGICAL_SEGMENT_BITS,
    SEGMENT_BUCKET_PAGES,
    SegmentedInsertTrace,
)
from storage.fixed_width_radix_primary import MAX_PHYSICAL_PAGE_ID


@dataclass(frozen=True)
class GenerationAlignedInsertTrace:
    base: SegmentedInsertTrace
    generation_alignment_padding_pages: int
    lifecycle_header_preads: int
    lifecycle_header_pwrites: int
    reused_free_extents: int
    fresh_physical_extents: int
    data_page_scrub_pwrites: int

    def __getattr__(self, name: str) -> Any:
        return getattr(self.base, name)

    def to_dict(self) -> dict[str, Any]:
        row = self.base.to_dict()
        row.update(
            {
                "generation_alignment_padding_pages": self.generation_alignment_padding_pages,
                "lifecycle_header_preads": self.lifecycle_header_preads,
                "lifecycle_header_pwrites": self.lifecycle_header_pwrites,
                "reused_free_extents": self.reused_free_extents,
                "fresh_physical_extents": self.fresh_physical_extents,
                "data_page_scrub_pwrites": self.data_page_scrub_pwrites,
            }
        )
        return row


class AlignedGenerationReclaimingPrimaryStore(ScrubbingReclaimingRadixPrimaryStore):
    """v0.33 real-generation integration over the v0.32 lifecycle mechanism.

    v0.30/v0.31 allocate logical generations back-to-back. A generation can therefore
    begin in the final 16-page mapping segment of its predecessor, which makes v0.32's
    whole-segment ownership unsafe to attach directly to real primary generations.

    This candidate aligns every *new* generation base to the next 16-logical-page
    boundary. At most 15 logical page ids are skipped per generation; the skipped ids
    allocate no physical data because physical placement remains append-local through
    the radix mapper. Every mapped segment then belongs to exactly one logical
    generation, so v0.32's intrusive owner chain can be integrated without a page-mask
    manifest or a capacity-sized discovery pass.

    The current experiment intentionally allows only one retired-generation cleanup
    backlog at a time. A new migration is rejected until that backlog has drained.
    """

    def __init__(self, path: str) -> None:
        super().__init__(path)
        self._pending_generation_meta: dict[str, Any] | None = None
        self._pending_alignment_padding = 0
        self._pending_lifecycle_preads = 0
        self._pending_lifecycle_pwrites = 0
        self._pending_reused_extents = 0
        self._pending_fresh_extents = 0
        self._pending_scrub_pwrites = 0

    def _reset_operation_state(self) -> None:
        super()._reset_operation_state()
        self._pending_generation_meta = None
        self._pending_alignment_padding = 0
        self._pending_lifecycle_preads = 0
        self._pending_lifecycle_pwrites = 0
        self._pending_reused_extents = 0
        self._pending_fresh_extents = 0
        self._pending_scrub_pwrites = 0

    @staticmethod
    def _aligned_generation_base(page_id: int) -> int:
        width = SEGMENT_BUCKET_PAGES
        return ((int(page_id) + width - 1) // width) * width

    @staticmethod
    def _segment_interval(base_page: int, page_count: int) -> tuple[int, int]:
        if page_count <= 0:
            raise ValueError("generation page count must be positive")
        first = int(base_page) // SEGMENT_BUCKET_PAGES
        last = (int(base_page) + int(page_count) - 1) // SEGMENT_BUCKET_PAGES
        return first, last

    def initialize(
        self,
        *,
        initial_capacity: int = 128,
        max_load: float = 0.50,
        bucket_size: int = 4,
        max_kicks: int = 32,
        stash_capacity: int = 8,
        migration_slot_budget: int = 8,
        force_same_pair: bool = False,
    ) -> None:
        super().initialize(
            initial_capacity=initial_capacity,
            max_load=max_load,
            bucket_size=bucket_size,
            max_kicks=max_kicks,
            stash_capacity=stash_capacity,
            migration_slot_budget=migration_slot_budget,
            force_same_pair=force_same_pair,
        )
        fd = self._open()
        try:
            epoch, meta, slot = super()._read_super(fd)
            meta = dict(meta)
            meta.update(
                {
                    "format": 33,
                    "generation_alignment_pages": SEGMENT_BUCKET_PAGES,
                    "last_alignment_padding_pages": 0,
                    "old_owner_generation": None,
                    "old_owner_head_page": None,
                    "old_owner_count": 0,
                }
            )
            os.pwrite(
                fd,
                self._pack_record(self._super_magic(), epoch, meta),
                self._super_offset(slot),
            )
            os.fsync(fd)
        finally:
            os.close(fd)

    def _start_migration_tx(self, tx) -> None:  # type: ignore[no-untyped-def]
        meta = tx.meta
        if int(meta.get("retire_remaining", 0)) != 0:
            raise PrimaryAdmissionExhausted(
                "retired-generation cleanup backlog must drain before another migration"
            )
        if meta.get("old_owner_generation") is not None:
            raise RuntimeError("old owner chain already active before migration start")

        old_generation = int(meta["current_generation"])
        old_capacity = int(meta["current_capacity"])
        old_base = int(meta["current_base_page"])
        new_capacity = old_capacity * 2
        new_generation = int(meta["next_generation"])
        raw_base = int(meta["next_page_id"])
        new_base = self._aligned_generation_base(raw_base)
        padding = new_base - raw_base
        if padding < 0 or padding >= SEGMENT_BUCKET_PAGES:
            raise AssertionError("generation alignment padding escaped fixed bound")
        new_pages = self._generation_pages(new_capacity, int(meta["bucket_size"]))

        old_pages = self._generation_pages(old_capacity, int(meta["bucket_size"]))
        old_first, old_last = self._segment_interval(old_base, old_pages)
        new_first, _new_last = self._segment_interval(new_base, new_pages)
        if new_first <= old_last:
            raise AssertionError("aligned generation still shares a mapping segment")

        if int(meta["owner_generation"]) != old_generation:
            raise RuntimeError("lifecycle owner generation drifted before migration")
        meta["old_owner_generation"] = old_generation
        meta["old_owner_head_page"] = meta["owner_head_page"]
        meta["old_owner_count"] = int(meta["owner_count"])
        meta["owner_generation"] = new_generation
        meta["owner_head_page"] = None
        meta["owner_count"] = 0

        meta["old_generation"] = old_generation
        meta["old_capacity"] = old_capacity
        meta["old_rows"] = int(meta["current_rows"])
        meta["old_base_page"] = old_base
        meta["current_generation"] = new_generation
        meta["current_capacity"] = new_capacity
        meta["current_rows"] = 0
        meta["current_base_page"] = new_base
        meta["migration_cursor"] = 0
        meta["migration_limit"] = old_capacity + int(meta["stash_capacity"])
        meta["next_generation"] = new_generation + 1
        meta["next_page_id"] = new_base + new_pages
        meta["last_alignment_padding_pages"] = padding
        self._pending_alignment_padding = padding

    def _migrate_tx(self, tx) -> tuple[int, int, int, bool]:  # type: ignore[no-untyped-def]
        old_generation = (
            None if tx.meta["old_generation"] is None else int(tx.meta["old_generation"])
        )
        scanned, moved, work, completed = super()._migrate_tx(tx)
        if not completed:
            return scanned, moved, work, completed
        if old_generation is None:
            raise AssertionError("migration completed without an old generation")
        if int(tx.meta.get("retire_remaining", 0)) != 0:
            raise RuntimeError("cannot publish a second retirement backlog")
        if int(tx.meta.get("old_owner_generation", -1)) != old_generation:
            raise RuntimeError("old lifecycle owner generation drifted at retirement")

        retired_count = int(tx.meta.get("old_owner_count", 0))
        if retired_count:
            tx.meta["retire_generation"] = old_generation
            tx.meta["retire_cursor_page"] = tx.meta.get("old_owner_head_page")
            tx.meta["retire_remaining"] = retired_count
        else:
            tx.meta["retire_generation"] = None
            tx.meta["retire_cursor_page"] = None
            tx.meta["retire_remaining"] = 0
        tx.meta["old_owner_generation"] = None
        tx.meta["old_owner_head_page"] = None
        tx.meta["old_owner_count"] = 0
        return scanned, moved, work, completed

    def _prepare_insert(
        self,
        fd: int,
        committed_epoch: int,
        meta: dict[str, Any],
        counters: _Counters,
        key: str,
    ):  # type: ignore[no-untyped-def]
        tx, state = super()._prepare_insert(fd, committed_epoch, meta, counters, key)
        self._pending_generation_meta = tx.meta
        return tx, state

    def _assert_current_segment_owner(self, segment_id: int) -> None:
        meta = self._pending_generation_meta
        if meta is None:
            raise RuntimeError("generation metadata is unavailable during mapping allocation")
        generation = int(meta["current_generation"])
        if int(meta["owner_generation"]) != generation:
            raise RuntimeError("current generation and lifecycle owner generation diverged")
        pages = self._generation_pages(
            int(meta["current_capacity"]), int(meta["bucket_size"])
        )
        first, last = self._segment_interval(int(meta["current_base_page"]), pages)
        if int(segment_id) < first or int(segment_id) > last:
            raise RuntimeError("fresh mapping does not belong to the current generation")

    def _scrub_extent(self, fd: int, segment_base: int) -> int:
        zero_page = bytes(4096)
        writes = 0
        for physical_page in range(
            int(segment_base), int(segment_base) + SEGMENT_DATA_PAGES
        ):
            written = os.pwrite(fd, zero_page, physical_page * 4096)
            if written != 4096:
                raise IOError("short write while scrubbing reclaimed generation segment")
            writes += 1
        return writes

    def _reserve_fresh_mapping(
        self,
        fd: int,
        segment_id: int,
        committed_epoch: int,
        new_epoch: int,
    ) -> int:
        segment_id = int(segment_id)
        if segment_id < 0 or segment_id >= (1 << LOGICAL_SEGMENT_BITS):
            raise ValueError("logical segment id exceeds fixed radix namespace")
        if segment_id in self._pending_segments:
            return int(self._pending_segments[segment_id])
        existing = self._resolve_segment(fd, segment_id, committed_epoch)
        if existing is not None:
            self._pending_segments[segment_id] = int(existing)
            return int(existing)
        if self._pending_frontier is None:
            raise RuntimeError("physical append frontier was not initialized")
        self._assert_current_segment_owner(segment_id)
        meta = self._pending_generation_meta
        if meta is None:
            raise RuntimeError("missing current generation metadata")
        generation = int(meta["owner_generation"])

        free_head = meta.get("free_head_page")
        if free_head is not None and int(meta.get("free_count", 0)) > 0:
            header, _slot, preads = self._read_lifecycle(
                fd, int(free_head), committed_epoch
            )
            self._pending_lifecycle_preads += preads
            if int(header["status"]) != LIFECYCLE_FREE:
                raise RuntimeError("free-list head is not FREE during automatic reuse")
            segment_base = int(free_head) + LIFECYCLE_COPIES
            self._pending_scrub_pwrites += self._scrub_extent(fd, segment_base)
            if self._active_failpoint is not None:
                self._active_failpoint("reused_extent_scrubbed")
            self._stage_mapping_to_base(
                fd, segment_id, segment_base, committed_epoch, new_epoch
            )
            extra_reads, writes = self._write_existing_lifecycle(
                fd,
                int(free_head),
                committed_epoch,
                new_epoch,
                status=LIFECYCLE_OWNED,
                generation=generation,
                segment_id=segment_id,
                next_header_page=meta.get("owner_head_page"),
            )
            self._pending_lifecycle_preads += extra_reads
            self._pending_lifecycle_pwrites += writes
            meta["free_head_page"] = header["next_header_page"]
            meta["free_count"] = int(meta["free_count"]) - 1
            meta["owner_head_page"] = int(free_head)
            meta["owner_count"] = int(meta["owner_count"]) + 1
            self._pending_reused_extents += 1
        else:
            header_base = int(self._pending_frontier)
            segment_base = header_base + LIFECYCLE_COPIES
            data_end = segment_base + SEGMENT_DATA_PAGES
            if data_end - 1 > MAX_PHYSICAL_PAGE_ID:
                raise OverflowError("generation-owned segment allocation exceeds uint64")
            os.ftruncate(fd, data_end * 4096)
            self._pending_allocated_pages += data_end - int(self._pending_frontier)
            self._pending_frontier = data_end
            if self._active_failpoint is not None:
                self._active_failpoint("allocated")
            self._pending_lifecycle_pwrites += self._write_new_lifecycle(
                fd,
                header_base,
                new_epoch,
                status=LIFECYCLE_OWNED,
                generation=generation,
                segment_id=segment_id,
                next_header_page=meta.get("owner_head_page"),
            )
            meta["owner_head_page"] = header_base
            meta["owner_count"] = int(meta["owner_count"]) + 1
            self._stage_mapping_to_base(
                fd, segment_id, segment_base, committed_epoch, new_epoch
            )
            self._pending_fresh_extents += 1

        self._pending_segments[segment_id] = int(segment_base)
        self._pending_new_segments.add(segment_id)
        self._segment_cache[(int(new_epoch), segment_id)] = int(segment_base)
        return int(segment_base)

    def insert(
        self, key: str, failpoint: Callable[[str], None] | None = None
    ) -> GenerationAlignedInsertTrace:
        base = super().insert(key, failpoint=failpoint)
        return GenerationAlignedInsertTrace(
            base=base,
            generation_alignment_padding_pages=int(self._pending_alignment_padding),
            lifecycle_header_preads=int(self._pending_lifecycle_preads),
            lifecycle_header_pwrites=int(self._pending_lifecycle_pwrites),
            reused_free_extents=int(self._pending_reused_extents),
            fresh_physical_extents=int(self._pending_fresh_extents),
            data_page_scrub_pwrites=int(self._pending_scrub_pwrites),
        )

    def retire_owner_generation(self, *, new_generation: int):  # type: ignore[no-untyped-def]
        raise RuntimeError(
            "v0.33 retirement is published automatically when real primary migration completes"
        )

    def generation_layout_snapshot(self) -> dict[str, Any]:
        self._reset_operation_state()
        fd = self._open()
        try:
            epoch, meta, _slot = super()._read_super(fd)
            bucket_size = int(meta["bucket_size"])
            current_pages = self._generation_pages(
                int(meta["current_capacity"]), bucket_size
            )
            current_first, current_last = self._segment_interval(
                int(meta["current_base_page"]), current_pages
            )
            old_row: dict[str, Any] | None = None
            if meta["old_generation"] is not None:
                old_pages = self._generation_pages(int(meta["old_capacity"]), bucket_size)
                old_first, old_last = self._segment_interval(
                    int(meta["old_base_page"]), old_pages
                )
                old_row = {
                    "generation": int(meta["old_generation"]),
                    "base_page": int(meta["old_base_page"]),
                    "page_count": old_pages,
                    "first_segment": old_first,
                    "last_segment": old_last,
                    "owner_count": int(meta.get("old_owner_count", 0)),
                }
            return {
                "committed_epoch": int(epoch),
                "current": {
                    "generation": int(meta["current_generation"]),
                    "base_page": int(meta["current_base_page"]),
                    "page_count": current_pages,
                    "first_segment": current_first,
                    "last_segment": current_last,
                    "owner_count": int(meta["owner_count"]),
                },
                "old": old_row,
                "retire_generation": meta.get("retire_generation"),
                "retire_remaining": int(meta.get("retire_remaining", 0)),
                "free_count": int(meta.get("free_count", 0)),
                "next_page_id": int(meta["next_page_id"]),
                "alignment_pages": int(meta["generation_alignment_pages"]),
                "last_alignment_padding_pages": int(
                    meta.get("last_alignment_padding_pages", 0)
                ),
                "old_owner_generation": meta.get("old_owner_generation"),
                "old_owner_count": int(meta.get("old_owner_count", 0)),
            }
        finally:
            self._active_counters = None
            os.close(fd)
