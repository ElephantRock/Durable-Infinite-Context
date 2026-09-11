from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable

from storage.fixed_page_primary import PAGE_SIZE, _Counters
from storage.queued_generation_reclaiming_primary import QueuedReclaimTrace
from storage.reclaiming_radix_primary import LIFECYCLE_COPIES, LIFECYCLE_FREE, LIFECYCLE_OWNED
from storage.retirement_descriptor_pool import (
    RETIREMENT_DESCRIPTOR_COPIES,
    RETIREMENT_STATUS_FREE,
    RETIREMENT_STATUS_QUEUED,
    TaggedDescriptorIO,
)


@dataclass(frozen=True)
class RecyclableRetirementReclaimTrace:
    base: QueuedReclaimTrace
    retirement_descriptor_pages_recycled: int
    retirement_descriptor_free_count: int

    def __getattr__(self, name: str) -> Any:
        return getattr(self.base, name)

    def to_dict(self) -> dict[str, Any]:
        row = self.base.to_dict()
        row.update(
            {
                "retirement_descriptor_pages_recycled": self.retirement_descriptor_pages_recycled,
                "retirement_descriptor_free_count": self.retirement_descriptor_free_count,
            }
        )
        return row


class RecyclableRetirementDescriptorReclaimMixin:
    def reclaim_step(
        self,
        *,
        budget: int,
        failpoint: Callable[[str], None] | None = None,
    ) -> RecyclableRetirementReclaimTrace:
        if budget <= 0:
            raise ValueError("reclaim budget must be positive")
        self._reset_operation_state()
        counters = _Counters()
        lifecycle_reads = 0
        lifecycle_writes = 0
        descriptor_reads = 0
        descriptor_writes = 0
        descriptor_pages_recycled = 0
        fd = self._open()
        try:
            committed_epoch, meta, _slot = self._read_super(fd, counters)
            meta = dict(meta)
            start = int(self._pending_frontier or 0)
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
                base = QueuedReclaimTrace(
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
                return RecyclableRetirementReclaimTrace(
                    base=base,
                    retirement_descriptor_pages_recycled=0,
                    retirement_descriptor_free_count=int(
                        meta.get("retirement_descriptor_free_count", 0)
                    ),
                )
            if head_base is None or head_incarnation is None:
                raise RuntimeError("non-empty retirement queue has no head identity")

            descriptor, descriptor_slot, preads = TaggedDescriptorIO.read(
                fd,
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
                    fd,
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
            else:
                free_page = meta.get("retirement_descriptor_free_head_page")
                free_incarnation = meta.get("retirement_descriptor_free_head_incarnation")
                extra_reads, writes = TaggedDescriptorIO.write_existing(
                    fd,
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

                meta["retirement_queue_head_page"] = descriptor["next_descriptor_page"]
                meta["retirement_queue_head_incarnation"] = descriptor[
                    "next_descriptor_incarnation"
                ]
                meta["retirement_queue_count"] = queue_count - 1
                if int(meta["retirement_queue_count"]) == 0:
                    meta["retirement_queue_tail_page"] = None
                    meta["retirement_queue_tail_incarnation"] = None
                meta["retirement_descriptor_free_head_page"] = int(head_base)
                meta["retirement_descriptor_free_head_incarnation"] = int(head_incarnation)
                meta["retirement_descriptor_free_count"] = int(
                    meta.get("retirement_descriptor_free_count", 0)
                ) + 1
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
            end = int(self._pending_frontier or start)
            base = QueuedReclaimTrace(
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
            return RecyclableRetirementReclaimTrace(
                base=base,
                retirement_descriptor_pages_recycled=descriptor_pages_recycled,
                retirement_descriptor_free_count=int(
                    meta.get("retirement_descriptor_free_count", 0)
                ),
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
            epoch, meta, _slot = self._read_super(fd)
            queue_count = int(meta.get("retirement_queue_count", 0))
            free_count = int(meta.get("retirement_descriptor_free_count", 0))
            if queue_count > max_descriptors or free_count > max_descriptors:
                raise RuntimeError("diagnostic descriptor chain exceeds snapshot limit")

            seen: set[tuple[int, int]] = set()
            queue_rows = self._descriptor_chain_snapshot(
                fd,
                epoch,
                meta.get("retirement_queue_head_page"),
                meta.get("retirement_queue_head_incarnation"),
                queue_count,
                RETIREMENT_STATUS_QUEUED,
                seen,
            )
            free_rows = self._descriptor_chain_snapshot(
                fd,
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
                "free_count": int(meta["free_count"]),
                "owner_generation": int(meta["owner_generation"]),
                "owner_count": int(meta["owner_count"]),
                "diagnostic_descriptor_preads": 2 * (queue_count + free_count),
            }
        finally:
            os.close(fd)

    @staticmethod
    def _descriptor_chain_snapshot(
        fd: int,
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
                fd,
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
        try:
            epoch, _meta, _slot = self._read_super(fd)
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
