from __future__ import annotations

import os
import struct
import zlib
from dataclasses import asdict, dataclass
from typing import Any, Callable

from storage.aligned_generation_reclaiming_primary import (
    AlignedGenerationReclaimingPrimaryStore,
    GenerationAlignedInsertTrace,
)
from storage.fixed_page_primary import PrimaryAdmissionExhausted, _Counters, PAGE_SIZE
from storage.reclaiming_radix_primary import (
    LIFECYCLE_COPIES,
    LIFECYCLE_FREE,
    LIFECYCLE_OWNED,
    NULL_PAGE,
)
from storage.segmented_fixed_page_primary import SEGMENT_BUCKET_PAGES
from storage.fixed_width_radix_primary import MAX_PHYSICAL_PAGE_ID

RETIREMENT_MAGIC = b"DICRET34"
_RETIREMENT_PREFIX = struct.Struct(">8sQQQQQ")
_RETIREMENT_CRC = struct.Struct(">I")
RETIREMENT_USED_BYTES = _RETIREMENT_PREFIX.size + _RETIREMENT_CRC.size
RETIREMENT_DESCRIPTOR_COPIES = 2


@dataclass(frozen=True)
class QueuedGenerationInsertTrace:
    base: GenerationAlignedInsertTrace
    retirement_descriptors_enqueued: int
    retirement_descriptor_preads: int
    retirement_descriptor_pwrites: int
    retirement_descriptor_pages_appended: int
    retirement_queue_count: int

    def __getattr__(self, name: str) -> Any:
        return getattr(self.base, name)

    def to_dict(self) -> dict[str, Any]:
        row = self.base.to_dict()
        row.update(
            {
                "retirement_descriptors_enqueued": self.retirement_descriptors_enqueued,
                "retirement_descriptor_preads": self.retirement_descriptor_preads,
                "retirement_descriptor_pwrites": self.retirement_descriptor_pwrites,
                "retirement_descriptor_pages_appended": self.retirement_descriptor_pages_appended,
                "retirement_queue_count": self.retirement_queue_count,
            }
        )
        return row


@dataclass(frozen=True)
class QueuedReclaimTrace:
    committed_epoch: int
    requested_budget: int
    reclaimed_segments: int
    remaining_segments_in_head: int
    retirement_queue_count: int
    retired_generation: int | None
    free_count: int
    radix_node_pwrites: int
    lifecycle_header_preads: int
    lifecycle_header_pwrites: int
    retirement_descriptor_preads: int
    retirement_descriptor_pwrites: int
    physical_pages_appended: int
    physical_bytes_appended: int
    fsyncs: int
    generation_pages_scanned: int
    mapping_nodes_scanned: int
    retirement_descriptors_scanned: int
    logical_redo: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _page_or_null(value: int | None) -> int:
    return NULL_PAGE if value is None else int(value)


def _null_or_page(value: int) -> int | None:
    return None if int(value) == NULL_PAGE else int(value)


def encode_retirement_descriptor(
    epoch: int,
    *,
    generation: int,
    cursor_header_page: int | None,
    remaining_segments: int,
    next_descriptor_page: int | None,
) -> bytes:
    if not 0 <= int(epoch) <= NULL_PAGE:
        raise ValueError("retirement epoch exceeds uint64")
    if not 0 <= int(generation) <= NULL_PAGE:
        raise ValueError("retirement generation exceeds uint64")
    if int(remaining_segments) <= 0 or int(remaining_segments) > NULL_PAGE:
        raise ValueError("retirement descriptor requires positive remaining count")
    cursor = _page_or_null(cursor_header_page)
    nxt = _page_or_null(next_descriptor_page)
    for pointer in (cursor, nxt):
        if pointer < 0 or pointer > MAX_PHYSICAL_PAGE_ID:
            raise ValueError("retirement descriptor pointer exceeds uint64")
    prefix = _RETIREMENT_PREFIX.pack(
        RETIREMENT_MAGIC,
        int(epoch),
        int(generation),
        cursor,
        int(remaining_segments),
        nxt,
    )
    crc = zlib.crc32(prefix) & 0xFFFFFFFF
    record = prefix + _RETIREMENT_CRC.pack(crc)
    if len(record) != RETIREMENT_USED_BYTES or len(record) > PAGE_SIZE:
        raise AssertionError("retirement descriptor layout drifted")
    return record + bytes(PAGE_SIZE - len(record))


def decode_retirement_descriptor(data: bytes) -> tuple[int, dict[str, Any]] | None:
    if len(data) != PAGE_SIZE:
        return None
    try:
        magic, epoch, generation, cursor, remaining, nxt = _RETIREMENT_PREFIX.unpack(
            data[: _RETIREMENT_PREFIX.size]
        )
        (stored_crc,) = _RETIREMENT_CRC.unpack(
            data[_RETIREMENT_PREFIX.size:RETIREMENT_USED_BYTES]
        )
    except struct.error:
        return None
    prefix = data[: _RETIREMENT_PREFIX.size]
    if magic != RETIREMENT_MAGIC or int(remaining) <= 0:
        return None
    if (zlib.crc32(prefix) & 0xFFFFFFFF) != stored_crc:
        return None
    return int(epoch), {
        "generation": int(generation),
        "cursor_header_page": _null_or_page(int(cursor)),
        "remaining_segments": int(remaining),
        "next_descriptor_page": _null_or_page(int(nxt)),
    }


class QueuedGenerationReclaimingPrimaryStore(AlignedGenerationReclaimingPrimaryStore):
    """v0.34 multiple-retirement-backlog experiment.

    v0.33 safely aligns real primary generations to mapping-segment boundaries, but it
    permits only one retired generation to await cleanup. This variant replaces the
    scalar retirement cursor with a FIFO of fixed-size, dual-copy retirement
    descriptors. The committed superblock carries only queue head/tail/count scalars.

    Completing a migration appends exactly one two-page descriptor and, when the queue
    is already non-empty, rewrites only the alternate copy of the current tail. Enqueue
    therefore does not walk retirement history. Reclamation touches only the committed
    head descriptor plus at most the caller's segment budget. Recovery still derives
    the physical frontier from the superblock and does not traverse the descriptor
    queue.

    Descriptor pages are append-only in this experiment. Total descriptor storage grows
    with the number of completed generations; v0.34 tests bounded foreground work, not
    constant total historical storage.
    """

    def __init__(self, path: str) -> None:
        super().__init__(path)
        self._pending_retirement_descriptor_preads = 0
        self._pending_retirement_descriptor_pwrites = 0
        self._pending_retirement_descriptor_pages = 0
        self._pending_retirement_enqueues = 0
        self._last_retirement_queue_count = 0

    def _reset_operation_state(self) -> None:
        super()._reset_operation_state()
        self._pending_retirement_descriptor_preads = 0
        self._pending_retirement_descriptor_pwrites = 0
        self._pending_retirement_descriptor_pages = 0
        self._pending_retirement_enqueues = 0
        self._last_retirement_queue_count = 0

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
                    "format": 34,
                    "retirement_queue_head_page": None,
                    "retirement_queue_tail_page": None,
                    "retirement_queue_count": 0,
                    # v0.32/v0.33 scalar retirement fields remain neutral for
                    # cross-version introspection but are not the v0.34 authority.
                    "retire_generation": None,
                    "retire_cursor_page": None,
                    "retire_remaining": 0,
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

    @staticmethod
    def _retirement_descriptor_offset(base_page: int, copy_slot: int) -> int:
        return (int(base_page) + int(copy_slot)) * PAGE_SIZE

    def _read_retirement_descriptor(
        self, fd: int, base_page: int, committed_epoch: int
    ) -> tuple[dict[str, Any], int, int]:
        candidates: list[tuple[int, dict[str, Any], int]] = []
        for copy_slot in (0, 1):
            data = os.pread(
                fd,
                PAGE_SIZE,
                self._retirement_descriptor_offset(base_page, copy_slot),
            )
            parsed = decode_retirement_descriptor(data)
            if parsed is None:
                continue
            epoch, payload = parsed
            if epoch <= committed_epoch:
                candidates.append((epoch, payload, copy_slot))
        if not candidates:
            raise RuntimeError(
                f"no valid retirement descriptor at physical page {base_page}"
            )
        _epoch, payload, slot = max(candidates, key=lambda row: row[0])
        return dict(payload), int(slot), 2

    def _write_new_retirement_descriptor(
        self,
        fd: int,
        base_page: int,
        new_epoch: int,
        *,
        generation: int,
        cursor_header_page: int,
        remaining_segments: int,
        next_descriptor_page: int | None,
    ) -> int:
        os.pwrite(
            fd,
            encode_retirement_descriptor(
                new_epoch,
                generation=generation,
                cursor_header_page=cursor_header_page,
                remaining_segments=remaining_segments,
                next_descriptor_page=next_descriptor_page,
            ),
            self._retirement_descriptor_offset(base_page, 0),
        )
        return 1

    def _write_existing_retirement_descriptor(
        self,
        fd: int,
        base_page: int,
        committed_epoch: int,
        new_epoch: int,
        *,
        generation: int,
        cursor_header_page: int,
        remaining_segments: int,
        next_descriptor_page: int | None,
        committed_slot: int | None = None,
    ) -> tuple[int, int]:
        preads = 0
        if committed_slot is None:
            _payload, committed_slot, preads = self._read_retirement_descriptor(
                fd, base_page, committed_epoch
            )
        target_slot = 1 - int(committed_slot)
        os.pwrite(
            fd,
            encode_retirement_descriptor(
                new_epoch,
                generation=generation,
                cursor_header_page=cursor_header_page,
                remaining_segments=remaining_segments,
                next_descriptor_page=next_descriptor_page,
            ),
            self._retirement_descriptor_offset(base_page, target_slot),
        )
        return preads, 1

    def _enqueue_retirement_tx(
        self,
        tx,
        *,
        generation: int,
        owner_head_page: int | None,
        owner_count: int,
    ) -> None:  # type: ignore[no-untyped-def]
        if owner_count <= 0:
            return
        if owner_head_page is None:
            raise RuntimeError("non-empty owner generation has no ownership head")
        if self._pending_frontier is None:
            raise RuntimeError("retirement descriptor frontier is unavailable")

        committed_epoch = int(tx.epoch)
        new_epoch = committed_epoch + 1
        descriptor_base = int(self._pending_frontier)
        descriptor_end = descriptor_base + RETIREMENT_DESCRIPTOR_COPIES
        if descriptor_end - 1 > MAX_PHYSICAL_PAGE_ID:
            raise OverflowError("retirement descriptor allocation exceeds uint64")
        os.ftruncate(tx.fd, descriptor_end * PAGE_SIZE)
        self._pending_allocated_pages += RETIREMENT_DESCRIPTOR_COPIES
        self._pending_frontier = descriptor_end
        self._pending_retirement_descriptor_pages += RETIREMENT_DESCRIPTOR_COPIES
        self._pending_retirement_descriptor_pwrites += self._write_new_retirement_descriptor(
            tx.fd,
            descriptor_base,
            new_epoch,
            generation=int(generation),
            cursor_header_page=int(owner_head_page),
            remaining_segments=int(owner_count),
            next_descriptor_page=None,
        )
        if self._active_failpoint is not None:
            self._active_failpoint("retirement_descriptor_written")

        tail = tx.meta.get("retirement_queue_tail_page")
        if tail is None:
            if int(tx.meta.get("retirement_queue_count", 0)) != 0:
                raise RuntimeError("retirement queue count is nonzero without a tail")
            tx.meta["retirement_queue_head_page"] = descriptor_base
        else:
            tail_payload, tail_slot, preads = self._read_retirement_descriptor(
                tx.fd, int(tail), committed_epoch
            )
            self._pending_retirement_descriptor_preads += preads
            if tail_payload["next_descriptor_page"] is not None:
                raise RuntimeError("committed retirement tail already has a successor")
            extra_reads, writes = self._write_existing_retirement_descriptor(
                tx.fd,
                int(tail),
                committed_epoch,
                new_epoch,
                generation=int(tail_payload["generation"]),
                cursor_header_page=int(tail_payload["cursor_header_page"]),
                remaining_segments=int(tail_payload["remaining_segments"]),
                next_descriptor_page=descriptor_base,
                committed_slot=tail_slot,
            )
            self._pending_retirement_descriptor_preads += extra_reads
            self._pending_retirement_descriptor_pwrites += writes
            if self._active_failpoint is not None:
                self._active_failpoint("retirement_tail_linked")

        tx.meta["retirement_queue_tail_page"] = descriptor_base
        tx.meta["retirement_queue_count"] = int(
            tx.meta.get("retirement_queue_count", 0)
        ) + 1
        self._pending_retirement_enqueues += 1
        self._last_retirement_queue_count = int(tx.meta["retirement_queue_count"])

    def _start_migration_tx(self, tx) -> None:  # type: ignore[no-untyped-def]
        meta = tx.meta
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
        _old_first, old_last = self._segment_interval(old_base, old_pages)
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
        scanned, moved, work, completed = super(
            AlignedGenerationReclaimingPrimaryStore, self
        )._migrate_tx(tx)
        if not completed:
            return scanned, moved, work, completed
        if old_generation is None:
            raise AssertionError("migration completed without an old generation")
        if int(tx.meta.get("old_owner_generation", -1)) != old_generation:
            raise RuntimeError("old lifecycle owner generation drifted at retirement")

        self._enqueue_retirement_tx(
            tx,
            generation=old_generation,
            owner_head_page=tx.meta.get("old_owner_head_page"),
            owner_count=int(tx.meta.get("old_owner_count", 0)),
        )
        tx.meta["old_owner_generation"] = None
        tx.meta["old_owner_head_page"] = None
        tx.meta["old_owner_count"] = 0
        return scanned, moved, work, completed

    def insert(
        self, key: str, failpoint: Callable[[str], None] | None = None
    ) -> QueuedGenerationInsertTrace:
        base = super().insert(key, failpoint=failpoint)
        queue_count = self._last_retirement_queue_count
        if queue_count == 0:
            fd = self._open()
            try:
                _epoch, meta, _slot = super()._read_super(fd)
                queue_count = int(meta.get("retirement_queue_count", 0))
            finally:
                os.close(fd)
        return QueuedGenerationInsertTrace(
            base=base,
            retirement_descriptors_enqueued=int(self._pending_retirement_enqueues),
            retirement_descriptor_preads=int(self._pending_retirement_descriptor_preads),
            retirement_descriptor_pwrites=int(self._pending_retirement_descriptor_pwrites),
            retirement_descriptor_pages_appended=int(self._pending_retirement_descriptor_pages),
            retirement_queue_count=int(queue_count),
        )

    def reclaim_step(
        self,
        *,
        budget: int,
        failpoint: Callable[[str], None] | None = None,
    ) -> QueuedReclaimTrace:
        if budget <= 0:
            raise ValueError("reclaim budget must be positive")
        self._reset_operation_state()
        counters = _Counters()
        lifecycle_reads = 0
        lifecycle_writes = 0
        descriptor_reads = 0
        descriptor_writes = 0
        fd = self._open()
        try:
            committed_epoch, meta, _slot = self._read_super(fd, counters)
            meta = dict(meta)
            start = int(self._pending_frontier or 0)
            queue_count = int(meta.get("retirement_queue_count", 0))
            head_base = meta.get("retirement_queue_head_page")
            if queue_count <= 0:
                if head_base is not None or meta.get("retirement_queue_tail_page") is not None:
                    raise RuntimeError("empty retirement queue retains head/tail pointers")
                return QueuedReclaimTrace(
                    committed_epoch=committed_epoch,
                    requested_budget=int(budget),
                    reclaimed_segments=0,
                    remaining_segments_in_head=0,
                    retirement_queue_count=0,
                    retired_generation=None,
                    free_count=int(meta["free_count"]),
                    radix_node_pwrites=0,
                    lifecycle_header_preads=0,
                    lifecycle_header_pwrites=0,
                    retirement_descriptor_preads=0,
                    retirement_descriptor_pwrites=0,
                    physical_pages_appended=0,
                    physical_bytes_appended=0,
                    fsyncs=0,
                    generation_pages_scanned=0,
                    mapping_nodes_scanned=0,
                    retirement_descriptors_scanned=0,
                    logical_redo=0,
                )
            if head_base is None:
                raise RuntimeError("non-empty retirement queue has no head")

            descriptor, descriptor_slot, preads = self._read_retirement_descriptor(
                fd, int(head_base), committed_epoch
            )
            descriptor_reads += preads
            generation = int(descriptor["generation"])
            cursor = descriptor["cursor_header_page"]
            remaining = int(descriptor["remaining_segments"])
            if cursor is None or remaining <= 0:
                raise RuntimeError("retirement head descriptor is malformed")

            new_epoch = committed_epoch + 1
            reclaimed = 0
            for _ in range(min(int(budget), remaining)):
                if cursor is None:
                    raise RuntimeError("retirement owner chain ended before descriptor count")
                header, _slot, preads = self._read_lifecycle(
                    fd, int(cursor), committed_epoch
                )
                lifecycle_reads += preads
                if int(header["status"]) != LIFECYCLE_OWNED:
                    raise RuntimeError("retirement cursor does not reference OWNED storage")
                if int(header["generation"]) != generation:
                    raise RuntimeError("queued retirement generation ownership drifted")
                segment_id = int(header["segment_id"])
                segment_base = self._stage_unmap(
                    fd, segment_id, committed_epoch, new_epoch
                )
                if segment_base != int(cursor) + LIFECYCLE_COPIES:
                    raise RuntimeError("lifecycle header does not own mapped segment")
                if failpoint is not None:
                    failpoint("mapping_unlinked")
                extra_reads, writes = self._write_existing_lifecycle(
                    fd,
                    int(cursor),
                    committed_epoch,
                    new_epoch,
                    status=LIFECYCLE_FREE,
                    generation=generation,
                    segment_id=segment_id,
                    next_header_page=meta["free_head_page"],
                )
                lifecycle_reads += extra_reads
                lifecycle_writes += writes
                if failpoint is not None:
                    failpoint("free_header_written")
                meta["free_head_page"] = int(cursor)
                meta["free_count"] = int(meta["free_count"]) + 1
                cursor = header["next_header_page"]
                remaining -= 1
                reclaimed += 1

            if remaining > 0:
                if cursor is None:
                    raise RuntimeError("partial retirement lost its next ownership cursor")
                extra_reads, writes = self._write_existing_retirement_descriptor(
                    fd,
                    int(head_base),
                    committed_epoch,
                    new_epoch,
                    generation=generation,
                    cursor_header_page=int(cursor),
                    remaining_segments=remaining,
                    next_descriptor_page=descriptor["next_descriptor_page"],
                    committed_slot=descriptor_slot,
                )
                descriptor_reads += extra_reads
                descriptor_writes += writes
                if failpoint is not None:
                    failpoint("retirement_descriptor_updated")
            else:
                next_descriptor = descriptor["next_descriptor_page"]
                meta["retirement_queue_head_page"] = next_descriptor
                meta["retirement_queue_count"] = queue_count - 1
                if int(meta["retirement_queue_count"]) == 0:
                    meta["retirement_queue_tail_page"] = None
                if failpoint is not None:
                    failpoint("retirement_dequeued")

            os.fsync(fd)
            counters.fsyncs += 1
            if failpoint is not None:
                failpoint("dependencies_synced")
            extra_reads, extra_writes = self._write_super(
                fd, committed_epoch, new_epoch, meta
            )
            counters.meta_preads += extra_reads
            counters.meta_pwrites += extra_writes
            os.fsync(fd)
            counters.fsyncs += 1
            if failpoint is not None:
                failpoint("committed")
            end = int(self._pending_frontier or start)
            return QueuedReclaimTrace(
                committed_epoch=new_epoch,
                requested_budget=int(budget),
                reclaimed_segments=reclaimed,
                remaining_segments_in_head=remaining,
                retirement_queue_count=int(meta["retirement_queue_count"]),
                retired_generation=generation,
                free_count=int(meta["free_count"]),
                radix_node_pwrites=self._pending_radix_pwrites,
                lifecycle_header_preads=lifecycle_reads,
                lifecycle_header_pwrites=lifecycle_writes,
                retirement_descriptor_preads=descriptor_reads,
                retirement_descriptor_pwrites=descriptor_writes,
                physical_pages_appended=end - start,
                physical_bytes_appended=(end - start) * PAGE_SIZE,
                fsyncs=counters.fsyncs,
                generation_pages_scanned=0,
                mapping_nodes_scanned=0,
                retirement_descriptors_scanned=0,
                logical_redo=0,
            )
        finally:
            self._active_counters = None
            os.close(fd)

    def retirement_queue_snapshot(self, *, max_descriptors: int = 1024) -> dict[str, Any]:
        if max_descriptors <= 0:
            raise ValueError("max_descriptors must be positive")
        self._reset_operation_state()
        fd = self._open()
        try:
            epoch, meta, _slot = super()._read_super(fd)
            count = int(meta.get("retirement_queue_count", 0))
            if count > max_descriptors:
                raise RuntimeError("diagnostic retirement queue exceeds snapshot limit")
            rows: list[dict[str, Any]] = []
            base = meta.get("retirement_queue_head_page")
            seen: set[int] = set()
            while base is not None:
                base = int(base)
                if base in seen:
                    raise RuntimeError("retirement descriptor queue contains a cycle")
                seen.add(base)
                payload, _slot, _preads = self._read_retirement_descriptor(
                    fd, base, epoch
                )
                rows.append({"descriptor_page": base, **payload})
                base = payload["next_descriptor_page"]
                if len(rows) > count:
                    raise RuntimeError("retirement queue contains more descriptors than committed count")
            if len(rows) != count:
                raise RuntimeError("retirement queue committed count does not match chain")
            expected_tail = None if not rows else int(rows[-1]["descriptor_page"])
            actual_tail = meta.get("retirement_queue_tail_page")
            if actual_tail != expected_tail:
                raise RuntimeError("retirement queue tail pointer drifted")
            return {
                "committed_epoch": int(epoch),
                "queue_count": count,
                "head_page": meta.get("retirement_queue_head_page"),
                "tail_page": actual_tail,
                "descriptors": rows,
                "free_count": int(meta["free_count"]),
                "owner_generation": int(meta["owner_generation"]),
                "owner_count": int(meta["owner_count"]),
                "diagnostic_descriptor_preads": 2 * len(rows),
            }
        finally:
            os.close(fd)

    def recover(self) -> dict[str, Any]:
        row = super().recover()
        row["retirement_descriptors_scanned"] = 0
        return row
