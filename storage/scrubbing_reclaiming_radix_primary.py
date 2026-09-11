from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any, Callable

from storage.fixed_page_primary import PAGE_SIZE, _Counters
from storage.reclaiming_radix_primary import (
    LIFECYCLE_COPIES,
    LIFECYCLE_FREE,
    LIFECYCLE_OWNED,
    SEGMENT_DATA_PAGES,
    ReclaimingRadixPrimaryStore,
)


@dataclass(frozen=True)
class ScrubbingReuseTrace:
    committed_epoch: int
    generation: int
    segment_id: int
    reused_header_page: int
    reused_segment_base_page: int
    remaining_free_count: int
    owner_count: int
    radix_node_pwrites: int
    lifecycle_header_preads: int
    lifecycle_header_pwrites: int
    data_page_scrub_pwrites: int
    physical_pages_appended: int
    physical_bytes_appended: int
    fsyncs: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ScrubbingReclaimingRadixPrimaryStore(ReclaimingRadixPrimaryStore):
    """v0.32 repaired reuse path with bounded stale-payload invalidation.

    A reclaimed segment may still contain valid old data-page records. Publishing that
    physical extent under a different logical segment id without invalidating those
    records can expose retired payloads through the new mapping. Because a physical
    segment has a fixed 32-page data footprint, reuse can invalidate every data copy
    with a constant 32 page writes before publishing the new radix edge and OWNED
    lifecycle header.

    Free extents have no live semantic payload. Therefore a crash after invalidation but
    before publication may destroy bytes in a still-FREE extent without changing any
    committed mapping/lifecycle state. The superblock publication point remains the
    visibility boundary for the new owner.
    """

    def reuse_one_mapping(
        self,
        segment_id: int,
        *,
        generation: int | None = None,
        failpoint: Callable[[str], None] | None = None,
    ) -> ScrubbingReuseTrace:
        segment_id = int(segment_id)
        self._reset_operation_state()
        counters = _Counters()
        lifecycle_reads = 0
        lifecycle_writes = 0
        scrub_writes = 0
        fd = self._open()
        try:
            committed_epoch, meta, _slot = self._read_super(fd, counters)
            meta = dict(meta)
            owner_generation = int(meta["owner_generation"])
            if generation is None:
                generation = owner_generation
            if int(generation) != owner_generation:
                raise RuntimeError("reuse may only join the current owner generation")
            header_base = meta["free_head_page"]
            if header_base is None or int(meta["free_count"]) <= 0:
                raise RuntimeError("free list is empty")
            header, _slot, preads = self._read_lifecycle(
                fd, int(header_base), committed_epoch
            )
            lifecycle_reads += preads
            if int(header["status"]) != LIFECYCLE_FREE:
                raise RuntimeError("free-list head is not marked FREE")
            if self._resolve_segment(fd, segment_id, committed_epoch) is not None:
                raise RuntimeError("reuse target mapping already exists")

            new_epoch = committed_epoch + 1
            start = int(self._pending_frontier or 0)
            segment_base = int(header_base) + LIFECYCLE_COPIES

            zero_page = bytes(PAGE_SIZE)
            for physical_page in range(
                segment_base, segment_base + SEGMENT_DATA_PAGES
            ):
                written = os.pwrite(fd, zero_page, physical_page * PAGE_SIZE)
                if written != PAGE_SIZE:
                    raise IOError("short write while scrubbing reclaimed segment")
                scrub_writes += 1
            if failpoint is not None:
                failpoint("data_scrubbed")

            self._stage_mapping_to_base(
                fd, segment_id, segment_base, committed_epoch, new_epoch
            )
            if failpoint is not None:
                failpoint("mapping_written")

            extra_reads, writes = self._write_existing_lifecycle(
                fd,
                int(header_base),
                committed_epoch,
                new_epoch,
                status=LIFECYCLE_OWNED,
                generation=int(generation),
                segment_id=segment_id,
                next_header_page=meta["owner_head_page"],
            )
            lifecycle_reads += extra_reads
            lifecycle_writes += writes
            if failpoint is not None:
                failpoint("owner_header_written")

            meta["free_head_page"] = header["next_header_page"]
            meta["free_count"] = int(meta["free_count"]) - 1
            meta["owner_head_page"] = int(header_base)
            meta["owner_count"] = int(meta["owner_count"]) + 1

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
            return ScrubbingReuseTrace(
                committed_epoch=new_epoch,
                generation=int(generation),
                segment_id=segment_id,
                reused_header_page=int(header_base),
                reused_segment_base_page=segment_base,
                remaining_free_count=int(meta["free_count"]),
                owner_count=int(meta["owner_count"]),
                radix_node_pwrites=self._pending_radix_pwrites,
                lifecycle_header_preads=lifecycle_reads,
                lifecycle_header_pwrites=lifecycle_writes,
                data_page_scrub_pwrites=scrub_writes,
                physical_pages_appended=end - start,
                physical_bytes_appended=(end - start) * PAGE_SIZE,
                fsyncs=counters.fsyncs,
            )
        finally:
            self._active_counters = None
            os.close(fd)
