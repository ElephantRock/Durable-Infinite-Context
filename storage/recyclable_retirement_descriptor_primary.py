from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable

from storage.fixed_page_primary import PAGE_SIZE
from storage.fixed_width_radix_primary import MAX_PHYSICAL_PAGE_ID
from storage.queued_generation_reclaiming_primary import (
    QueuedGenerationInsertTrace,
    QueuedGenerationReclaimingPrimaryStore,
)
from storage.reclaiming_radix_primary import NULL_PAGE
from storage.recyclable_retirement_descriptor_reclaim import (
    RecyclableRetirementDescriptorReclaimMixin,
)
from storage.retirement_descriptor_pool import (
    RETIREMENT_DESCRIPTOR_COPIES,
    RETIREMENT_STATUS_FREE,
    RETIREMENT_STATUS_QUEUED,
    TaggedDescriptorIO,
)


@dataclass(frozen=True)
class RecyclableRetirementInsertTrace:
    base: QueuedGenerationInsertTrace
    retirement_descriptor_reuses: int
    retirement_descriptor_free_count: int

    def __getattr__(self, name: str) -> Any:
        return getattr(self.base, name)

    def to_dict(self) -> dict[str, Any]:
        row = self.base.to_dict()
        row.update(
            {
                "retirement_descriptor_reuses": self.retirement_descriptor_reuses,
                "retirement_descriptor_free_count": self.retirement_descriptor_free_count,
            }
        )
        return row


class RecyclableRetirementDescriptorPrimaryStore(
    RecyclableRetirementDescriptorReclaimMixin,
    QueuedGenerationReclaimingPrimaryStore,
):
    """v0.35 descriptor-pool experiment with ABA-resistant tagged references."""

    def __init__(self, path: str) -> None:
        super().__init__(path)
        self._pending_retirement_descriptor_reuses = 0

    def _reset_operation_state(self) -> None:
        super()._reset_operation_state()
        self._pending_retirement_descriptor_reuses = 0

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
            epoch, meta, slot = self._read_super(fd)
            meta = dict(meta)
            meta.update(
                {
                    "format": 35,
                    "retirement_queue_head_incarnation": None,
                    "retirement_queue_tail_incarnation": None,
                    "retirement_descriptor_free_head_page": None,
                    "retirement_descriptor_free_head_incarnation": None,
                    "retirement_descriptor_free_count": 0,
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
        free_count = int(tx.meta.get("retirement_descriptor_free_count", 0))
        free_page = tx.meta.get("retirement_descriptor_free_head_page")
        free_incarnation = tx.meta.get("retirement_descriptor_free_head_incarnation")

        if free_count > 0:
            if free_page is None or free_incarnation is None:
                raise RuntimeError("descriptor free count is nonzero without a head identity")
            free_payload, free_slot, preads = TaggedDescriptorIO.read(
                tx.fd,
                int(free_page),
                committed_epoch,
                expected_incarnation=int(free_incarnation),
                expected_status=RETIREMENT_STATUS_FREE,
            )
            self._pending_retirement_descriptor_preads += preads
            if int(free_payload["incarnation"]) >= NULL_PAGE:
                raise OverflowError("retirement descriptor incarnation exhausted uint64")
            descriptor_base = int(free_page)
            descriptor_incarnation = int(free_payload["incarnation"]) + 1
            extra_reads, writes = TaggedDescriptorIO.write_existing(
                tx.fd,
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
            descriptor_base = int(self._pending_frontier)
            descriptor_incarnation = 1
            descriptor_end = descriptor_base + RETIREMENT_DESCRIPTOR_COPIES
            if descriptor_end - 1 > MAX_PHYSICAL_PAGE_ID:
                raise OverflowError("retirement descriptor allocation exceeds uint64")
            os.ftruncate(tx.fd, descriptor_end * PAGE_SIZE)
            self._pending_allocated_pages += RETIREMENT_DESCRIPTOR_COPIES
            self._pending_frontier = descriptor_end
            self._pending_retirement_descriptor_pages += RETIREMENT_DESCRIPTOR_COPIES
            self._pending_retirement_descriptor_pwrites += TaggedDescriptorIO.write_new(
                tx.fd,
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
                self._active_failpoint("retirement_descriptor_written")

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
                tx.fd,
                int(tail),
                committed_epoch,
                expected_incarnation=int(tail_incarnation),
                expected_status=RETIREMENT_STATUS_QUEUED,
            )
            self._pending_retirement_descriptor_preads += preads
            if tail_payload["next_descriptor_page"] is not None:
                raise RuntimeError("committed retirement tail already has a successor")
            extra_reads, writes = TaggedDescriptorIO.write_existing(
                tx.fd,
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

    def insert(
        self,
        key: str,
        failpoint: Callable[[str], None] | None = None,
    ) -> RecyclableRetirementInsertTrace:
        base = super().insert(key, failpoint=failpoint)
        fd = self._open()
        try:
            _epoch, meta, _slot = self._read_super(fd)
            descriptor_free_count = int(
                meta.get("retirement_descriptor_free_count", 0)
            )
        finally:
            os.close(fd)
        return RecyclableRetirementInsertTrace(
            base=base,
            retirement_descriptor_reuses=int(self._pending_retirement_descriptor_reuses),
            retirement_descriptor_free_count=descriptor_free_count,
        )
