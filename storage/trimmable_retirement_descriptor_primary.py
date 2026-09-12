from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any, Callable

from storage.fixed_page_primary import PAGE_SIZE, _Counters
from storage.fixed_width_radix_primary import MAX_PHYSICAL_PAGE_ID
from storage.reclaiming_radix_primary import NULL_PAGE
from storage.recyclable_retirement_descriptor_primary import (
    RecyclableRetirementDescriptorPrimaryStore,
)
from storage.retirement_descriptor_pool import (
    RETIREMENT_DESCRIPTOR_COPIES,
    RETIREMENT_STATUS_FREE,
    RETIREMENT_STATUS_QUEUED,
    TaggedDescriptorIO,
)


@dataclass(frozen=True)
class DescriptorTailReleaseTrace:
    committed_epoch: int
    descriptor_free_count_before: int
    descriptor_free_count_after: int
    descriptor_pool_count_before: int
    descriptor_pool_count_after: int
    tail_release_eligible: bool
    released_descriptor_page: int | None
    released_descriptor_incarnation: int | None
    retirement_descriptor_preads: int
    physical_pages_released: int
    physical_bytes_released: int
    physical_truncated_bytes: int
    fsyncs: int
    generation_pages_scanned: int
    mapping_nodes_scanned: int
    retirement_descriptors_scanned: int
    logical_redo: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TrimmableRetirementDescriptorPrimaryStore(
    RecyclableRetirementDescriptorPrimaryStore
):
    """v0.36 bounded descriptor-tail release experiment.

    v0.35 recycles dequeued descriptor pairs internally, which stops storage from
    growing with completed-generation history after sufficient reusable capacity
    exists. The internal free pool still retains the historical peak allocation.

    v0.36 tests a narrower physical-retention mechanism. A maintenance step may
    release exactly one FREE descriptor pair when the committed free-list head is
    also the committed physical append tail. It never scans for a deeper releasable
    descriptor. The superblock publishes the shorter committed frontier first;
    physical truncation is derived cleanup after publication, so process death after
    commit can be repaired by the existing frontier recovery path.

    Releasing a physical address introduces a stronger ABA boundary than v0.35 free-
    list reuse because later generic append allocation can reuse those page numbers.
    Descriptor incarnations are therefore allocated from one committed monotonic
    counter across both fresh and free-list allocations. Re-entering the descriptor
    domain at a previously released page cannot reset identity to incarnation 1.
    """

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
                    "format": 36,
                    "retirement_descriptor_incarnation_counter": 0,
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
    def _claim_descriptor_incarnation(meta: dict[str, Any]) -> int:
        counter = int(meta.get("retirement_descriptor_incarnation_counter", 0))
        if counter < 0 or counter >= NULL_PAGE:
            raise OverflowError("retirement descriptor incarnation counter exhausted uint64")
        incarnation = counter + 1
        meta["retirement_descriptor_incarnation_counter"] = incarnation
        return incarnation

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
        descriptor_incarnation = self._claim_descriptor_incarnation(tx.meta)

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
            if descriptor_incarnation <= int(free_payload["incarnation"]):
                raise RuntimeError("global descriptor incarnation did not advance on reuse")
            descriptor_base = int(free_page)
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

    def trim_retirement_descriptor_tail_step(
        self,
        *,
        failpoint: Callable[[str], None] | None = None,
    ) -> DescriptorTailReleaseTrace:
        """Release one free descriptor pair iff it is the committed physical tail.

        The operation deliberately does not search the free list. If the free-list head
        is not the two-page pair ending at `next_physical_page`, the step is a no-op.
        """

        self._reset_operation_state()
        counters = _Counters()
        descriptor_reads = 0
        fsyncs = 0
        fd = self._open()
        try:
            committed_epoch, meta, _slot = self._read_super(fd, counters)
            meta = dict(meta)
            queue_count = int(meta.get("retirement_queue_count", 0))
            free_count = int(meta.get("retirement_descriptor_free_count", 0))
            pool_count = queue_count + free_count
            head_page = meta.get("retirement_descriptor_free_head_page")
            head_incarnation = meta.get("retirement_descriptor_free_head_incarnation")
            if free_count <= 0:
                if head_page is not None or head_incarnation is not None:
                    raise RuntimeError("empty descriptor free list retains a head identity")
                return DescriptorTailReleaseTrace(
                    committed_epoch=int(committed_epoch),
                    descriptor_free_count_before=0,
                    descriptor_free_count_after=0,
                    descriptor_pool_count_before=pool_count,
                    descriptor_pool_count_after=pool_count,
                    tail_release_eligible=False,
                    released_descriptor_page=None,
                    released_descriptor_incarnation=None,
                    retirement_descriptor_preads=0,
                    physical_pages_released=0,
                    physical_bytes_released=0,
                    physical_truncated_bytes=0,
                    fsyncs=0,
                    generation_pages_scanned=0,
                    mapping_nodes_scanned=0,
                    retirement_descriptors_scanned=0,
                    logical_redo=0,
                )
            if head_page is None or head_incarnation is None:
                raise RuntimeError("descriptor free count is nonzero without a head identity")
            if self._pending_frontier is None:
                raise RuntimeError("physical append frontier was not initialized")

            descriptor, _descriptor_slot, preads = TaggedDescriptorIO.read(
                fd,
                int(head_page),
                int(committed_epoch),
                expected_incarnation=int(head_incarnation),
                expected_status=RETIREMENT_STATUS_FREE,
            )
            descriptor_reads += preads
            eligible = (
                int(head_page) + RETIREMENT_DESCRIPTOR_COPIES
                == int(self._pending_frontier)
            )
            if not eligible:
                return DescriptorTailReleaseTrace(
                    committed_epoch=int(committed_epoch),
                    descriptor_free_count_before=free_count,
                    descriptor_free_count_after=free_count,
                    descriptor_pool_count_before=pool_count,
                    descriptor_pool_count_after=pool_count,
                    tail_release_eligible=False,
                    released_descriptor_page=None,
                    released_descriptor_incarnation=None,
                    retirement_descriptor_preads=descriptor_reads,
                    physical_pages_released=0,
                    physical_bytes_released=0,
                    physical_truncated_bytes=0,
                    fsyncs=0,
                    generation_pages_scanned=0,
                    mapping_nodes_scanned=0,
                    retirement_descriptors_scanned=0,
                    logical_redo=0,
                )

            new_epoch = int(committed_epoch) + 1
            meta["retirement_descriptor_free_head_page"] = descriptor[
                "next_descriptor_page"
            ]
            meta["retirement_descriptor_free_head_incarnation"] = descriptor[
                "next_descriptor_incarnation"
            ]
            meta["retirement_descriptor_free_count"] = free_count - 1
            self._pending_frontier = int(head_page)
            if failpoint is not None:
                failpoint("descriptor_tail_release_selected")

            self._write_super(fd, int(committed_epoch), new_epoch, meta)
            os.fsync(fd)
            fsyncs += 1
            if failpoint is not None:
                failpoint("committed")

            before = os.fstat(fd).st_size
            target = int(head_page) * PAGE_SIZE
            truncated = 0
            if before > target:
                os.ftruncate(fd, target)
                truncated = before - target
                if failpoint is not None:
                    failpoint("descriptor_tail_truncated")
                os.fsync(fd)
                fsyncs += 1
            if failpoint is not None:
                failpoint("trim_synced")

            return DescriptorTailReleaseTrace(
                committed_epoch=new_epoch,
                descriptor_free_count_before=free_count,
                descriptor_free_count_after=free_count - 1,
                descriptor_pool_count_before=pool_count,
                descriptor_pool_count_after=pool_count - 1,
                tail_release_eligible=True,
                released_descriptor_page=int(head_page),
                released_descriptor_incarnation=int(head_incarnation),
                retirement_descriptor_preads=descriptor_reads,
                physical_pages_released=RETIREMENT_DESCRIPTOR_COPIES,
                physical_bytes_released=RETIREMENT_DESCRIPTOR_COPIES * PAGE_SIZE,
                physical_truncated_bytes=truncated,
                fsyncs=fsyncs,
                generation_pages_scanned=0,
                mapping_nodes_scanned=0,
                retirement_descriptors_scanned=0,
                logical_redo=0,
            )
        finally:
            self._active_counters = None
            os.close(fd)

    def retirement_descriptor_reference(
        self,
        base_page: int,
        incarnation: int,
        *,
        expected_status: int = RETIREMENT_STATUS_QUEUED,
    ) -> dict[str, Any]:
        fd = self._open()
        try:
            epoch, meta, _slot = self._read_super(fd)
            if int(base_page) < 0 or (
                int(base_page) + RETIREMENT_DESCRIPTOR_COPIES
                > int(meta["next_physical_page"])
            ):
                raise RuntimeError("descriptor reference is outside committed physical frontier")
            payload, _slot, _preads = TaggedDescriptorIO.read(
                fd,
                int(base_page),
                epoch,
                expected_incarnation=int(incarnation),
                expected_status=int(expected_status),
            )
            return payload
        finally:
            os.close(fd)
