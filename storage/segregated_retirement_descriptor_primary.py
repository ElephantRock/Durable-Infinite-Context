from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from storage.fixed_page_primary import PAGE_SIZE, _Counters
from storage.queued_generation_reclaiming_primary import QueuedReclaimTrace
from storage.reclaiming_radix_primary import (
    LIFECYCLE_COPIES,
    LIFECYCLE_FREE,
    LIFECYCLE_OWNED,
    NULL_PAGE,
)
from storage.recyclable_retirement_descriptor_primary import (
    RecyclableRetirementDescriptorPrimaryStore,
    RecyclableRetirementInsertTrace,
)
from storage.recyclable_retirement_descriptor_reclaim import (
    RecyclableRetirementReclaimTrace,
)
from storage.retirement_descriptor_pool import (
    RETIREMENT_DESCRIPTOR_COPIES,
    RETIREMENT_STATUS_FREE,
    RETIREMENT_STATUS_QUEUED,
    TaggedDescriptorIO,
)


@dataclass(frozen=True)
class SegregatedRetirementInsertTrace:
    base: RecyclableRetirementInsertTrace
    retirement_arena_fsyncs: int
    retirement_arena_pages: int

    def __getattr__(self, name: str) -> Any:
        return getattr(self.base, name)

    def to_dict(self) -> dict[str, Any]:
        row = self.base.to_dict()
        row.update(
            {
                "retirement_arena_fsyncs": self.retirement_arena_fsyncs,
                "retirement_arena_pages": self.retirement_arena_pages,
            }
        )
        return row


@dataclass(frozen=True)
class SegregatedRetirementReclaimTrace:
    base: RecyclableRetirementReclaimTrace
    retirement_arena_fsyncs: int
    retirement_arena_pages_before: int
    retirement_arena_pages_after: int
    retirement_arena_pages_released: int

    def __getattr__(self, name: str) -> Any:
        return getattr(self.base, name)

    def to_dict(self) -> dict[str, Any]:
        row = self.base.to_dict()
        row.update(
            {
                "retirement_arena_fsyncs": self.retirement_arena_fsyncs,
                "retirement_arena_pages_before": self.retirement_arena_pages_before,
                "retirement_arena_pages_after": self.retirement_arena_pages_after,
                "retirement_arena_pages_released": self.retirement_arena_pages_released,
            }
        )
        return row


class SegregatedRetirementDescriptorPrimaryStore(
    RecyclableRetirementDescriptorPrimaryStore
):
    """v0.37 segregated retirement-descriptor arena experiment.

    Retirement descriptors live in a sidecar arena rather than the primary append
    stream. The primary superblock remains the only publication authority for queue
    identities, free-list identities, committed arena length, and the next globally
    monotonic descriptor incarnation.

    When the committed retirement queue becomes empty, every descriptor in the arena
    is unreachable. The transaction therefore publishes an empty queue/free-list and
    committed arena length zero in O(1) metadata work, then truncates the sidecar after
    the primary superblock commit. A crash after publication but before truncation can
    leave only physical residue; recover() derives the target arena length from the
    committed superblock and truncates without walking descriptor history.
    """

    ARENA_SUFFIX = ".retire37"

    def __init__(self, path: str | Path) -> None:
        super().__init__(str(path))
        self._pending_retirement_arena_fsyncs = 0

    @property
    def arena_path(self) -> Path:
        return Path(f"{self.path}{self.ARENA_SUFFIX}")

    @classmethod
    def arena_path_for(cls, path: str | Path) -> Path:
        return Path(f"{path}{cls.ARENA_SUFFIX}")

    def _reset_operation_state(self) -> None:
        super()._reset_operation_state()
        self._pending_retirement_arena_fsyncs = 0

    def _open_arena(self) -> int:
        return os.open(self.arena_path, os.O_RDWR)

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
        if self.arena_path.exists():
            raise RuntimeError("segregated retirement descriptor arena already exists")
        super().initialize(
            initial_capacity=initial_capacity,
            max_load=max_load,
            bucket_size=bucket_size,
            max_kicks=max_kicks,
            stash_capacity=stash_capacity,
            migration_slot_budget=migration_slot_budget,
            force_same_pair=force_same_pair,
        )
        arena_fd = os.open(self.arena_path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        try:
            os.fsync(arena_fd)
        finally:
            os.close(arena_fd)

        fd = self._open()
        try:
            epoch, meta, slot = self._read_super(fd)
            meta = dict(meta)
            meta.update(
                {
                    "format": 37,
                    "retirement_descriptor_arena_pages": 0,
                    "retirement_descriptor_next_incarnation": 1,
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
    def _take_incarnation(meta: dict[str, Any]) -> int:
        value = int(meta.get("retirement_descriptor_next_incarnation", 1))
        if value <= 0 or value >= NULL_PAGE:
            raise OverflowError("retirement descriptor global incarnation exhausted uint64")
        meta["retirement_descriptor_next_incarnation"] = value + 1
        return value

    @staticmethod
    def _committed_arena_pages(meta: dict[str, Any]) -> int:
        pages = int(meta.get("retirement_descriptor_arena_pages", 0))
        if pages < 0 or pages % RETIREMENT_DESCRIPTOR_COPIES:
            raise RuntimeError("retirement descriptor arena length is malformed")
        return pages

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

        committed_epoch = int(tx.epoch)
        new_epoch = committed_epoch + 1
        free_count = int(tx.meta.get("retirement_descriptor_free_count", 0))
        free_page = tx.meta.get("retirement_descriptor_free_head_page")
        free_incarnation = tx.meta.get("retirement_descriptor_free_head_incarnation")
        arena_pages = self._committed_arena_pages(tx.meta)
        descriptor_incarnation = self._take_incarnation(tx.meta)
        arena_fd = self._open_arena()
        try:
            if free_count > 0:
                if free_page is None or free_incarnation is None:
                    raise RuntimeError("descriptor free count is nonzero without a head identity")
                free_payload, free_slot, preads = TaggedDescriptorIO.read(
                    arena_fd,
                    int(free_page),
                    committed_epoch,
                    expected_incarnation=int(free_incarnation),
                    expected_status=RETIREMENT_STATUS_FREE,
                )
                self._pending_retirement_descriptor_preads += preads
                descriptor_base = int(free_page)
                extra_reads, writes = TaggedDescriptorIO.write_existing(
                    arena_fd,
                    descriptor_base,
                    committed_epoch,
                    new_epoch,
                    committed_slot=free_slot,
                    incarnation=descriptor_incarnation,
                    status=RETIREMENT_STATUS_QUEUED,
                    generation=int(generation),
                    cursor_header_page=int(owner_head_page),
                    remaining_segments=int(owner_count),
                    next_descriptor_page=None,
                    next_descriptor_incarnation=None,
                )
                self._pending_retirement_descriptor_preads += extra_reads
                self._pending_retirement_descriptor_pwrites += writes
                self._pending_retirement_descriptor_reuses += 1
                tx.meta["retirement_descriptor_free_head_page"] = free_payload[
                    "next_descriptor_page"
                ]
                tx.meta["retirement_descriptor_free_head_incarnation"] = free_payload[
                    "next_descriptor_incarnation"
                ]
                tx.meta["retirement_descriptor_free_count"] = free_count - 1
                if self._active_failpoint is not None:
                    self._active_failpoint("retirement_descriptor_reused")
            else:
                if free_page is not None or free_incarnation is not None:
                    raise RuntimeError("empty descriptor free list retains a head identity")
                descriptor_base = arena_pages
                descriptor_end = descriptor_base + RETIREMENT_DESCRIPTOR_COPIES
                if descriptor_end - 1 >= NULL_PAGE:
                    raise OverflowError("retirement descriptor arena exceeds uint64")
                os.ftruncate(arena_fd, descriptor_end * PAGE_SIZE)
                tx.meta["retirement_descriptor_arena_pages"] = descriptor_end
                self._pending_retirement_descriptor_pages += RETIREMENT_DESCRIPTOR_COPIES
                self._pending_retirement_descriptor_pwrites += TaggedDescriptorIO.write_new(
                    arena_fd,
                    descriptor_base,
                    new_epoch,
                    incarnation=descriptor_incarnation,
                    status=RETIREMENT_STATUS_QUEUED,
                    generation=int(generation),
                    cursor_header_page=int(owner_head_page),
                    remaining_segments=int(owner_count),
                    next_descriptor_page=None,
                    next_descriptor_incarnation=None,
                )
                if self._active_failpoint is not None:
                    self._active_failpoint("retirement_arena_descriptor_written")

            tail = tx.meta.get("retirement_queue_tail_page")
            tail_incarnation = tx.meta.get("retirement_queue_tail_incarnation")
            if tail is None:
                if tail_incarnation is not None:
                    raise RuntimeError("null retirement tail has an incarnation")
                if int(tx.meta.get("retirement_queue_count", 0)) != 0:
                    raise RuntimeError("retirement queue count is nonzero without a tail")
                tx.meta["retirement_queue_head_page"] = descriptor_base
                tx.meta["retirement_queue_head_incarnation"] = descriptor_incarnation
            else:
                if tail_incarnation is None:
                    raise RuntimeError("retirement tail lacks an incarnation")
                tail_payload, tail_slot, preads = TaggedDescriptorIO.read(
                    arena_fd,
                    int(tail),
                    committed_epoch,
                    expected_incarnation=int(tail_incarnation),
                    expected_status=RETIREMENT_STATUS_QUEUED,
                )
                self._pending_retirement_descriptor_preads += preads
                if tail_payload["next_descriptor_page"] is not None:
                    raise RuntimeError("committed retirement tail already has a successor")
                extra_reads, writes = TaggedDescriptorIO.write_existing(
                    arena_fd,
                    int(tail),
                    committed_epoch,
                    new_epoch,
                    committed_slot=tail_slot,
                    incarnation=int(tail_payload["incarnation"]),
                    status=RETIREMENT_STATUS_QUEUED,
                    generation=int(tail_payload["generation"]),
                    cursor_header_page=int(tail_payload["cursor_header_page"]),
                    remaining_segments=int(tail_payload["remaining_segments"]),
                    next_descriptor_page=descriptor_base,
                    next_descriptor_incarnation=descriptor_incarnation,
                )
                self._pending_retirement_descriptor_preads += extra_reads
                self._pending_retirement_descriptor_pwrites += writes
                if self._active_failpoint is not None:
                    self._active_failpoint("retirement_tail_linked")

            tx.meta["retirement_queue_tail_page"] = descriptor_base
            tx.meta["retirement_queue_tail_incarnation"] = descriptor_incarnation
            tx.meta["retirement_queue_count"] = int(tx.meta.get("retirement_queue_count", 0)) + 1
            self._pending_retirement_enqueues += 1
            self._last_retirement_queue_count = int(tx.meta["retirement_queue_count"])

            os.fsync(arena_fd)
            self._pending_retirement_arena_fsyncs += 1
            if self._active_failpoint is not None:
                self._active_failpoint("retirement_arena_synced")
        finally:
            os.close(arena_fd)

    def insert(
        self,
        key: str,
        failpoint: Callable[[str], None] | None = None,
    ) -> SegregatedRetirementInsertTrace:
        base = super().insert(key, failpoint=failpoint)
        fd = self._open()
        try:
            _epoch, meta, _slot = self._read_super(fd)
            arena_pages = self._committed_arena_pages(meta)
        finally:
            os.close(fd)
        return SegregatedRetirementInsertTrace(
            base=base,
            retirement_arena_fsyncs=int(self._pending_retirement_arena_fsyncs),
            retirement_arena_pages=arena_pages,
        )

    def reclaim_step(
        self,
        *,
        budget: int,
        failpoint: Callable[[str], None] | None = None,
    ) -> SegregatedRetirementReclaimTrace:
        if budget <= 0:
            raise ValueError("reclaim budget must be positive")
        self._reset_operation_state()
        counters = _Counters()
        lifecycle_reads = 0
        lifecycle_writes = 0
        descriptor_reads = 0
        descriptor_writes = 0
        descriptor_pages_recycled = 0
        descriptor_pages_released = 0
        arena_reset = False
        fd = self._open()
        arena_fd = self._open_arena()
        try:
            committed_epoch, meta, _slot = self._read_super(fd, counters)
            meta = dict(meta)
            start = int(self._pending_frontier or 0)
            arena_pages_before = self._committed_arena_pages(meta)
            queue_count = int(meta.get("retirement_queue_count", 0))
            head_base = meta.get("retirement_queue_head_page")
            head_incarnation = meta.get("retirement_queue_head_incarnation")
            if queue_count <= 0:
                if any(
                    value is not None
                    for value in (
                        head_base,
                        head_incarnation,
                        meta.get("retirement_queue_tail_page"),
                        meta.get("retirement_queue_tail_incarnation"),
                    )
                ):
                    raise RuntimeError("empty retirement queue retains head/tail identity")
                base_trace = QueuedReclaimTrace(
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
                recycled = RecyclableRetirementReclaimTrace(
                    base=base_trace,
                    retirement_descriptor_pages_recycled=0,
                    retirement_descriptor_free_count=int(
                        meta.get("retirement_descriptor_free_count", 0)
                    ),
                )
                return SegregatedRetirementReclaimTrace(
                    base=recycled,
                    retirement_arena_fsyncs=0,
                    retirement_arena_pages_before=arena_pages_before,
                    retirement_arena_pages_after=arena_pages_before,
                    retirement_arena_pages_released=0,
                )
            if head_base is None or head_incarnation is None:
                raise RuntimeError("non-empty retirement queue has no head identity")

            descriptor, descriptor_slot, preads = TaggedDescriptorIO.read(
                arena_fd,
                int(head_base),
                committed_epoch,
                expected_incarnation=int(head_incarnation),
                expected_status=RETIREMENT_STATUS_QUEUED,
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
                header, _slot, preads = self._read_lifecycle(fd, int(cursor), committed_epoch)
                lifecycle_reads += preads
                if int(header["status"]) != LIFECYCLE_OWNED:
                    raise RuntimeError("retirement cursor does not reference OWNED storage")
                if int(header["generation"]) != generation:
                    raise RuntimeError("queued retirement generation ownership drifted")
                segment_id = int(header["segment_id"])
                segment_base = self._stage_unmap(fd, segment_id, committed_epoch, new_epoch)
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
                    raise RuntimeError("partial retirement lost ownership cursor")
                extra_reads, writes = TaggedDescriptorIO.write_existing(
                    arena_fd,
                    int(head_base),
                    committed_epoch,
                    new_epoch,
                    committed_slot=descriptor_slot,
                    incarnation=int(head_incarnation),
                    status=RETIREMENT_STATUS_QUEUED,
                    generation=generation,
                    cursor_header_page=int(cursor),
                    remaining_segments=remaining,
                    next_descriptor_page=descriptor["next_descriptor_page"],
                    next_descriptor_incarnation=descriptor["next_descriptor_incarnation"],
                )
                descriptor_reads += extra_reads
                descriptor_writes += writes
                if failpoint is not None:
                    failpoint("retirement_descriptor_updated")
                os.fsync(arena_fd)
                self._pending_retirement_arena_fsyncs += 1
                if failpoint is not None:
                    failpoint("retirement_arena_synced")
            else:
                next_queue_count = queue_count - 1
                meta["retirement_queue_head_page"] = descriptor["next_descriptor_page"]
                meta["retirement_queue_head_incarnation"] = descriptor[
                    "next_descriptor_incarnation"
                ]
                meta["retirement_queue_count"] = next_queue_count
                if next_queue_count == 0:
                    meta["retirement_queue_tail_page"] = None
                    meta["retirement_queue_tail_incarnation"] = None
                    meta["retirement_descriptor_free_head_page"] = None
                    meta["retirement_descriptor_free_head_incarnation"] = None
                    meta["retirement_descriptor_free_count"] = 0
                    meta["retirement_descriptor_arena_pages"] = 0
                    descriptor_pages_released = arena_pages_before
                    arena_reset = True
                    if failpoint is not None:
                        failpoint("retirement_arena_reset_staged")
                else:
                    free_page = meta.get("retirement_descriptor_free_head_page")
                    free_incarnation = meta.get("retirement_descriptor_free_head_incarnation")
                    extra_reads, writes = TaggedDescriptorIO.write_existing(
                        arena_fd,
                        int(head_base),
                        committed_epoch,
                        new_epoch,
                        committed_slot=descriptor_slot,
                        incarnation=int(head_incarnation),
                        status=RETIREMENT_STATUS_FREE,
                        generation=None,
                        cursor_header_page=None,
                        remaining_segments=0,
                        next_descriptor_page=free_page,
                        next_descriptor_incarnation=free_incarnation,
                    )
                    descriptor_reads += extra_reads
                    descriptor_writes += writes
                    descriptor_pages_recycled = RETIREMENT_DESCRIPTOR_COPIES
                    if failpoint is not None:
                        failpoint("retirement_descriptor_freed")
                    meta["retirement_descriptor_free_head_page"] = int(head_base)
                    meta["retirement_descriptor_free_head_incarnation"] = int(head_incarnation)
                    meta["retirement_descriptor_free_count"] = int(
                        meta.get("retirement_descriptor_free_count", 0)
                    ) + 1
                    os.fsync(arena_fd)
                    self._pending_retirement_arena_fsyncs += 1
                    if failpoint is not None:
                        failpoint("retirement_arena_synced")
                if failpoint is not None:
                    failpoint("retirement_dequeued")

            os.fsync(fd)
            counters.fsyncs += 1
            if failpoint is not None:
                failpoint("dependencies_synced")
            extra_reads, extra_writes = self._write_super(fd, committed_epoch, new_epoch, meta)
            counters.meta_preads += extra_reads
            counters.meta_pwrites += extra_writes
            os.fsync(fd)
            counters.fsyncs += 1
            if failpoint is not None:
                failpoint("committed")

            if arena_reset:
                if failpoint is not None:
                    failpoint("retirement_arena_reset_committed")
                os.ftruncate(arena_fd, 0)
                if failpoint is not None:
                    failpoint("retirement_arena_truncated")
                os.fsync(arena_fd)
                self._pending_retirement_arena_fsyncs += 1
                if failpoint is not None:
                    failpoint("retirement_arena_reset_synced")

            end = int(self._pending_frontier or start)
            base_trace = QueuedReclaimTrace(
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
            recycled = RecyclableRetirementReclaimTrace(
                base=base_trace,
                retirement_descriptor_pages_recycled=descriptor_pages_recycled,
                retirement_descriptor_free_count=int(
                    meta.get("retirement_descriptor_free_count", 0)
                ),
            )
            return SegregatedRetirementReclaimTrace(
                base=recycled,
                retirement_arena_fsyncs=int(self._pending_retirement_arena_fsyncs),
                retirement_arena_pages_before=arena_pages_before,
                retirement_arena_pages_after=self._committed_arena_pages(meta),
                retirement_arena_pages_released=descriptor_pages_released,
            )
        finally:
            self._active_counters = None
            os.close(arena_fd)
            os.close(fd)

    def retirement_queue_snapshot(self, *, max_descriptors: int = 1024) -> dict[str, Any]:
        if max_descriptors <= 0:
            raise ValueError("max_descriptors must be positive")
        self._reset_operation_state()
        fd = self._open()
        arena_fd = self._open_arena()
        try:
            epoch, meta, _slot = self._read_super(fd)
            queue_count = int(meta.get("retirement_queue_count", 0))
            free_count = int(meta.get("retirement_descriptor_free_count", 0))
            if queue_count > max_descriptors or free_count > max_descriptors:
                raise RuntimeError("diagnostic descriptor chain exceeds snapshot limit")
            arena_pages = self._committed_arena_pages(meta)
            if queue_count + free_count > arena_pages // RETIREMENT_DESCRIPTOR_COPIES:
                raise RuntimeError("descriptor references exceed committed arena capacity")

            seen: set[tuple[int, int]] = set()
            queue_rows = self._arena_chain_snapshot(
                arena_fd,
                epoch,
                meta.get("retirement_queue_head_page"),
                meta.get("retirement_queue_head_incarnation"),
                queue_count,
                RETIREMENT_STATUS_QUEUED,
                seen,
            )
            free_rows = self._arena_chain_snapshot(
                arena_fd,
                epoch,
                meta.get("retirement_descriptor_free_head_page"),
                meta.get("retirement_descriptor_free_head_incarnation"),
                free_count,
                RETIREMENT_STATUS_FREE,
                seen,
            )
            expected_tail_page = None if not queue_rows else queue_rows[-1]["descriptor_page"]
            expected_tail_inc = None if not queue_rows else queue_rows[-1]["descriptor_incarnation"]
            if meta.get("retirement_queue_tail_page") != expected_tail_page:
                raise RuntimeError("retirement queue tail page drifted")
            if meta.get("retirement_queue_tail_incarnation") != expected_tail_inc:
                raise RuntimeError("retirement queue tail incarnation drifted")
            if queue_count == 0 and free_count == 0 and arena_pages != 0:
                raise RuntimeError("empty descriptor arena retained committed capacity")
            return {
                "committed_epoch": int(epoch),
                "queue_count": queue_count,
                "head_page": meta.get("retirement_queue_head_page"),
                "head_incarnation": meta.get("retirement_queue_head_incarnation"),
                "tail_page": meta.get("retirement_queue_tail_page"),
                "tail_incarnation": meta.get("retirement_queue_tail_incarnation"),
                "descriptors": queue_rows,
                "descriptor_free_count": free_count,
                "descriptor_free_head_page": meta.get("retirement_descriptor_free_head_page"),
                "descriptor_free_head_incarnation": meta.get(
                    "retirement_descriptor_free_head_incarnation"
                ),
                "free_descriptors": free_rows,
                "descriptor_pool_count": queue_count + free_count,
                "descriptor_arena_pages": arena_pages,
                "descriptor_arena_capacity": arena_pages // RETIREMENT_DESCRIPTOR_COPIES,
                "descriptor_next_incarnation": int(
                    meta.get("retirement_descriptor_next_incarnation", 1)
                ),
                "free_count": int(meta["free_count"]),
                "owner_generation": int(meta["owner_generation"]),
                "owner_count": int(meta["owner_count"]),
                "diagnostic_descriptor_preads": 2 * (queue_count + free_count),
            }
        finally:
            os.close(arena_fd)
            os.close(fd)

    @staticmethod
    def _arena_chain_snapshot(
        arena_fd: int,
        epoch: int,
        page: int | None,
        incarnation: int | None,
        count: int,
        status: int,
        seen: set[tuple[int, int]],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        while page is not None:
            if incarnation is None:
                raise RuntimeError("descriptor page lacks incarnation")
            ref = (int(page), int(incarnation))
            if ref in seen:
                raise RuntimeError("descriptor chains overlap or cycle")
            seen.add(ref)
            payload, _slot, _preads = TaggedDescriptorIO.read(
                arena_fd,
                ref[0],
                epoch,
                expected_incarnation=ref[1],
                expected_status=status,
            )
            rows.append(
                {
                    "descriptor_page": ref[0],
                    "descriptor_incarnation": ref[1],
                    **payload,
                }
            )
            page = payload["next_descriptor_page"]
            incarnation = payload["next_descriptor_incarnation"]
            if len(rows) > count:
                raise RuntimeError("descriptor chain exceeds committed count")
        if len(rows) != count:
            raise RuntimeError("descriptor count does not match chain")
        return rows

    def retirement_descriptor_reference(
        self,
        base_page: int,
        incarnation: int,
        *,
        expected_status: int = RETIREMENT_STATUS_QUEUED,
    ) -> dict[str, Any]:
        fd = self._open()
        arena_fd = self._open_arena()
        try:
            epoch, _meta, _slot = self._read_super(fd)
            payload, _slot, _preads = TaggedDescriptorIO.read(
                arena_fd,
                int(base_page),
                epoch,
                expected_incarnation=int(incarnation),
                expected_status=int(expected_status),
            )
            return payload
        finally:
            os.close(arena_fd)
            os.close(fd)

    def descriptor_arena_diagnostic(self) -> dict[str, Any]:
        fd = self._open()
        try:
            epoch, meta, _slot = self._read_super(fd)
            committed_pages = self._committed_arena_pages(meta)
        finally:
            os.close(fd)
        before = self.arena_path.stat().st_size
        target = committed_pages * PAGE_SIZE
        return {
            "committed_epoch": int(epoch),
            "committed_arena_pages": committed_pages,
            "committed_arena_bytes": target,
            "arena_file_bytes": before,
            "uncommitted_arena_tail_bytes": max(0, before - target),
        }

    def recover(self) -> dict[str, Any]:
        primary = dict(super().recover())
        fd = self._open()
        try:
            epoch, meta, _slot = self._read_super(fd)
            target_bytes = self._committed_arena_pages(meta) * PAGE_SIZE
        finally:
            os.close(fd)

        if not self.arena_path.exists():
            if target_bytes:
                raise RuntimeError("committed retirement descriptor arena is missing")
            arena_fd = os.open(self.arena_path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        else:
            arena_fd = self._open_arena()
        try:
            before = os.fstat(arena_fd).st_size
            if before < target_bytes:
                raise RuntimeError("retirement descriptor arena is shorter than committed frontier")
            if before > target_bytes:
                os.ftruncate(arena_fd, target_bytes)
                os.fsync(arena_fd)
            after = os.fstat(arena_fd).st_size
        finally:
            os.close(arena_fd)

        primary.update(
            {
                "committed_epoch": int(epoch),
                "retirement_descriptors_scanned": 0,
                "retirement_descriptor_arena_target_bytes": target_bytes,
                "retirement_descriptor_arena_tail_before_bytes": max(
                    0, before - target_bytes
                ),
                "retirement_descriptor_arena_tail_after_bytes": max(
                    0, after - target_bytes
                ),
                "retirement_descriptor_arena_truncated_bytes": max(0, before - after),
            }
        )
        return primary
