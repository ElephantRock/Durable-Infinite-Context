from __future__ import annotations

import os
import struct
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from storage.fixed_page_primary import FixedPagePrimaryStore, PAGE_SIZE, SUPER_MAGIC, _Counters
from storage.fixed_width_radix_primary import (
    MAX_PHYSICAL_PAGE_ID,
    FixedWidthRadixPrimaryStore,
)
from storage.segmented_fixed_page_primary import (
    DATA_PAGE_COPIES,
    LOGICAL_SEGMENT_BITS,
    RADIX_LEVELS,
    ROOT_NODE_BASE_PAGE,
    SEGMENT_BUCKET_PAGES,
)

LIFECYCLE_MAGIC = b"DICLIF32"
LIFECYCLE_OWNED = 1
LIFECYCLE_FREE = 2
NULL_PAGE = (1 << 64) - 1
_LIFECYCLE_PREFIX = struct.Struct(">8sQBQQQ")
_LIFECYCLE_CRC = struct.Struct(">I")
LIFECYCLE_USED_BYTES = _LIFECYCLE_PREFIX.size + _LIFECYCLE_CRC.size
LIFECYCLE_COPIES = 2
SEGMENT_DATA_PAGES = DATA_PAGE_COPIES * SEGMENT_BUCKET_PAGES
FRESH_SEGMENT_PAGES = LIFECYCLE_COPIES + SEGMENT_DATA_PAGES
MAX_FRESH_PATH_PAGES = FRESH_SEGMENT_PAGES + 2 * (RADIX_LEVELS - 1)
MAX_FRESH_PATH_BYTES = MAX_FRESH_PATH_PAGES * PAGE_SIZE


@dataclass(frozen=True)
class OwnedMappingBatchTrace:
    committed_epoch: int
    generation: int
    mappings_requested: int
    new_segments_allocated: int
    lifecycle_header_pwrites: int
    radix_node_pwrites: int
    physical_pages_appended: int
    physical_bytes_appended: int
    fsyncs: int
    owner_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RetirementTrace:
    committed_epoch: int
    retired_generation: int
    retired_segments: int
    new_owner_generation: int
    fsyncs: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReclaimTrace:
    committed_epoch: int
    requested_budget: int
    reclaimed_segments: int
    remaining_segments: int
    free_count: int
    radix_node_pwrites: int
    lifecycle_header_preads: int
    lifecycle_header_pwrites: int
    physical_pages_appended: int
    physical_bytes_appended: int
    fsyncs: int
    generation_pages_scanned: int
    mapping_nodes_scanned: int
    logical_redo: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReuseTrace:
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
    physical_pages_appended: int
    physical_bytes_appended: int
    fsyncs: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _page_or_null(value: int | None) -> int:
    return NULL_PAGE if value is None else int(value)


def _null_or_page(value: int) -> int | None:
    return None if int(value) == NULL_PAGE else int(value)


def encode_lifecycle_header(
    epoch: int,
    *,
    status: int,
    generation: int,
    segment_id: int,
    next_header_page: int | None,
) -> bytes:
    if status not in (LIFECYCLE_OWNED, LIFECYCLE_FREE):
        raise ValueError("invalid lifecycle status")
    if not 0 <= int(epoch) <= NULL_PAGE:
        raise ValueError("epoch exceeds uint64")
    if not 0 <= int(generation) <= NULL_PAGE:
        raise ValueError("generation exceeds uint64")
    if not 0 <= int(segment_id) < (1 << LOGICAL_SEGMENT_BITS):
        raise ValueError("segment id exceeds fixed radix namespace")
    pointer = _page_or_null(next_header_page)
    if pointer < 0 or pointer > MAX_PHYSICAL_PAGE_ID:
        raise ValueError("lifecycle next pointer exceeds uint64")
    prefix = _LIFECYCLE_PREFIX.pack(
        LIFECYCLE_MAGIC,
        int(epoch),
        int(status),
        int(generation),
        int(segment_id),
        pointer,
    )
    crc = zlib.crc32(prefix) & 0xFFFFFFFF
    record = prefix + _LIFECYCLE_CRC.pack(crc)
    if len(record) != LIFECYCLE_USED_BYTES or len(record) > PAGE_SIZE:
        raise AssertionError("lifecycle header layout drifted")
    return record + bytes(PAGE_SIZE - len(record))


def decode_lifecycle_header(data: bytes) -> tuple[int, dict[str, Any]] | None:
    if len(data) != PAGE_SIZE:
        return None
    try:
        magic, epoch, status, generation, segment_id, next_page = _LIFECYCLE_PREFIX.unpack(
            data[: _LIFECYCLE_PREFIX.size]
        )
        (stored_crc,) = _LIFECYCLE_CRC.unpack(
            data[_LIFECYCLE_PREFIX.size:LIFECYCLE_USED_BYTES]
        )
    except struct.error:
        return None
    prefix = data[: _LIFECYCLE_PREFIX.size]
    if magic != LIFECYCLE_MAGIC or status not in (LIFECYCLE_OWNED, LIFECYCLE_FREE):
        return None
    if (zlib.crc32(prefix) & 0xFFFFFFFF) != stored_crc:
        return None
    return int(epoch), {
        "status": int(status),
        "generation": int(generation),
        "segment_id": int(segment_id),
        "next_header_page": _null_or_page(int(next_page)),
    }


class ReclaimingRadixPrimaryStore(FixedWidthRadixPrimaryStore):
    """v0.32 mapping-lifecycle experiment over the v0.31 fixed-width radix mapper.

    Each owned physical segment receives a dual-copy lifecycle header. Headers form an
    intrusive per-generation ownership chain while a separate committed head forms the
    reusable free list. Retirement publishes only chain-head metadata. Cleanup follows
    that chain under a fixed budget, unlinks exactly those mappings, and publishes FREE
    headers in the same epoch. Reuse reverses the transition without scanning the radix.

    This class intentionally isolates mapping lifecycle first. It does not yet wire the
    ownership chain into every v0.24 primary-generation transition; that integration and
    logical-generation boundary alignment remain a later falsification target.
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
            epoch, meta, slot = FixedPagePrimaryStore._read_super(self, fd)
            meta = dict(meta)
            meta.update(
                {
                    "format": 32,
                    "owner_generation": 0,
                    "owner_head_page": None,
                    "owner_count": 0,
                    "retire_generation": None,
                    "retire_cursor_page": None,
                    "retire_remaining": 0,
                    "free_head_page": None,
                    "free_count": 0,
                }
            )
            os.pwrite(
                fd,
                self._pack_record(SUPER_MAGIC, epoch, meta),
                self._super_offset(slot),
            )
            os.fsync(fd)
        finally:
            os.close(fd)

    @staticmethod
    def _lifecycle_offset(header_base_page: int, copy_slot: int) -> int:
        return (int(header_base_page) + int(copy_slot)) * PAGE_SIZE

    def _read_lifecycle(
        self, fd: int, header_base_page: int, committed_epoch: int
    ) -> tuple[dict[str, Any], int, int]:
        candidates: list[tuple[int, dict[str, Any], int]] = []
        for copy_slot in (0, 1):
            data = os.pread(
                fd, PAGE_SIZE, self._lifecycle_offset(header_base_page, copy_slot)
            )
            parsed = decode_lifecycle_header(data)
            if parsed is None:
                continue
            epoch, payload = parsed
            if epoch <= committed_epoch:
                candidates.append((epoch, payload, copy_slot))
        if not candidates:
            raise RuntimeError(
                f"no valid lifecycle header at physical page {header_base_page}"
            )
        epoch, payload, slot = max(candidates, key=lambda row: row[0])
        return dict(payload), int(slot), 2

    def _write_new_lifecycle(
        self,
        fd: int,
        header_base_page: int,
        new_epoch: int,
        *,
        status: int,
        generation: int,
        segment_id: int,
        next_header_page: int | None,
    ) -> int:
        os.pwrite(
            fd,
            encode_lifecycle_header(
                new_epoch,
                status=status,
                generation=generation,
                segment_id=segment_id,
                next_header_page=next_header_page,
            ),
            self._lifecycle_offset(header_base_page, 0),
        )
        return 1

    def _write_existing_lifecycle(
        self,
        fd: int,
        header_base_page: int,
        committed_epoch: int,
        new_epoch: int,
        *,
        status: int,
        generation: int,
        segment_id: int,
        next_header_page: int | None,
    ) -> tuple[int, int]:
        _payload, committed_slot, preads = self._read_lifecycle(
            fd, header_base_page, committed_epoch
        )
        target_slot = 1 - committed_slot
        os.pwrite(
            fd,
            encode_lifecycle_header(
                new_epoch,
                status=status,
                generation=generation,
                segment_id=segment_id,
                next_header_page=next_header_page,
            ),
            self._lifecycle_offset(header_base_page, target_slot),
        )
        return preads, 1

    def _stage_mapping_to_base(
        self,
        fd: int,
        segment_id: int,
        segment_base: int,
        committed_epoch: int,
        new_epoch: int,
    ) -> None:
        existing = self._resolve_segment(fd, segment_id, committed_epoch)
        if existing is not None:
            if int(existing) != int(segment_base):
                raise RuntimeError("mapping already exists at a different physical base")
            return
        if self._pending_frontier is None:
            raise RuntimeError("physical append frontier was not initialized")

        digits = self._radix_digits(segment_id)
        node_base = ROOT_NODE_BASE_PAGE
        missing_depth: int | None = None
        parent_payload: dict[str, Any] | None = None
        parent_slot: int | None = None
        for depth in range(RADIX_LEVELS):
            payload, slot = self._node_for_pending_write(fd, node_base, committed_epoch)
            value = payload["entries"].get(str(digits[depth]))
            if value is None:
                missing_depth = depth
                parent_payload = payload
                parent_slot = slot
                break
            if depth == RADIX_LEVELS - 1:
                raise RuntimeError("mapping appeared while staging a fresh edge")
            node_base = int(value)
        if missing_depth is None or parent_payload is None:
            raise AssertionError("fresh mapping had no missing radix edge")

        new_node_bases: list[int] = []
        frontier = int(self._pending_frontier)
        for _depth in range(missing_depth + 1, RADIX_LEVELS):
            new_node_bases.append(frontier)
            frontier += 2
        if frontier - 1 > MAX_PHYSICAL_PAGE_ID:
            raise OverflowError("radix node allocation exceeds uint64 page pointer")
        if frontier > int(self._pending_frontier):
            os.ftruncate(fd, frontier * PAGE_SIZE)
            self._pending_allocated_pages += frontier - int(self._pending_frontier)
            self._pending_frontier = frontier

        for index, child_depth in enumerate(range(missing_depth + 1, RADIX_LEVELS)):
            entries = (
                {str(digits[child_depth]): int(segment_base)}
                if child_depth == RADIX_LEVELS - 1
                else {str(digits[child_depth]): int(new_node_bases[index + 1])}
            )
            self._write_new_node(
                fd,
                new_node_bases[index],
                new_epoch,
                {"depth": child_depth, "entries": entries},
            )

        parent_entries = dict(parent_payload["entries"])
        parent_entries[str(digits[missing_depth])] = (
            int(segment_base)
            if missing_depth == RADIX_LEVELS - 1
            else int(new_node_bases[0])
        )
        self._write_existing_node(
            fd,
            node_base,
            committed_epoch,
            new_epoch,
            {"depth": missing_depth, "entries": parent_entries},
            committed_slot=parent_slot,
        )
        self._pending_segments[segment_id] = int(segment_base)
        self._segment_cache[(int(new_epoch), int(segment_id))] = int(segment_base)

    def _stage_unmap(
        self,
        fd: int,
        segment_id: int,
        committed_epoch: int,
        new_epoch: int,
    ) -> int:
        digits = self._radix_digits(segment_id)
        node_base = ROOT_NODE_BASE_PAGE
        for depth in range(RADIX_LEVELS):
            payload, slot = self._node_for_pending_write(fd, node_base, committed_epoch)
            if int(payload["depth"]) != depth:
                raise RuntimeError("radix node depth drifted during reclaim")
            key = str(digits[depth])
            value = payload["entries"].get(key)
            if value is None:
                raise RuntimeError("retired ownership chain points at an absent mapping")
            if depth == RADIX_LEVELS - 1:
                segment_base = int(value)
                entries = dict(payload["entries"])
                del entries[key]
                self._write_existing_node(
                    fd,
                    node_base,
                    committed_epoch,
                    new_epoch,
                    {"depth": depth, "entries": entries},
                    committed_slot=slot,
                )
                self._pending_segments.pop(segment_id, None)
                self._segment_cache.clear()
                return segment_base
            node_base = int(value)
        raise AssertionError("radix traversal fell through during reclaim")

    def materialize_owned_segment_mappings(
        self,
        segment_ids: Iterable[int],
        *,
        generation: int | None = None,
    ) -> OwnedMappingBatchTrace:
        requested = tuple(int(segment_id) for segment_id in segment_ids)
        if not requested or len(set(requested)) != len(requested):
            raise ValueError("owned mapping batch requires unique segment ids")
        self._reset_operation_state()
        counters = _Counters()
        lifecycle_writes = 0
        fd = self._open()
        try:
            committed_epoch, meta, _slot = self._read_super(fd, counters)
            meta = dict(meta)
            owner_generation = int(meta["owner_generation"])
            if generation is None:
                generation = owner_generation
            if int(generation) != owner_generation:
                raise RuntimeError("owned mappings may only join the current owner generation")
            new_epoch = committed_epoch + 1
            start = int(self._pending_frontier or 0)
            for segment_id in requested:
                if self._resolve_segment(fd, segment_id, committed_epoch) is not None:
                    raise RuntimeError("owned mapping already exists")
                if self._pending_frontier is None:
                    raise RuntimeError("physical append frontier was not initialized")
                header_base = int(self._pending_frontier)
                segment_base = header_base + LIFECYCLE_COPIES
                data_end = segment_base + SEGMENT_DATA_PAGES
                if data_end - 1 > MAX_PHYSICAL_PAGE_ID:
                    raise OverflowError("owned segment allocation exceeds uint64 page pointer")
                os.ftruncate(fd, data_end * PAGE_SIZE)
                self._pending_allocated_pages += data_end - int(self._pending_frontier)
                self._pending_frontier = data_end
                lifecycle_writes += self._write_new_lifecycle(
                    fd,
                    header_base,
                    new_epoch,
                    status=LIFECYCLE_OWNED,
                    generation=int(generation),
                    segment_id=segment_id,
                    next_header_page=meta["owner_head_page"],
                )
                meta["owner_head_page"] = header_base
                meta["owner_count"] = int(meta["owner_count"]) + 1
                self._stage_mapping_to_base(
                    fd, segment_id, segment_base, committed_epoch, new_epoch
                )
            os.fsync(fd)
            counters.fsyncs += 1
            extra_reads, extra_writes = self._write_super(
                fd, committed_epoch, new_epoch, meta
            )
            counters.meta_preads += extra_reads
            counters.meta_pwrites += extra_writes
            os.fsync(fd)
            counters.fsyncs += 1
            end = int(self._pending_frontier or start)
            return OwnedMappingBatchTrace(
                committed_epoch=new_epoch,
                generation=int(generation),
                mappings_requested=len(requested),
                new_segments_allocated=len(requested),
                lifecycle_header_pwrites=lifecycle_writes,
                radix_node_pwrites=self._pending_radix_pwrites,
                physical_pages_appended=end - start,
                physical_bytes_appended=(end - start) * PAGE_SIZE,
                fsyncs=counters.fsyncs,
                owner_count=int(meta["owner_count"]),
            )
        finally:
            self._active_counters = None
            os.close(fd)

    def retire_owner_generation(self, *, new_generation: int) -> RetirementTrace:
        self._reset_operation_state()
        counters = _Counters()
        fd = self._open()
        try:
            committed_epoch, meta, _slot = self._read_super(fd, counters)
            meta = dict(meta)
            if int(meta["retire_remaining"]) != 0:
                raise RuntimeError("v0.32 supports one retirement backlog at a time")
            retired_count = int(meta["owner_count"])
            if retired_count <= 0:
                raise RuntimeError("current owner generation has no segments to retire")
            retired_generation = int(meta["owner_generation"])
            if int(new_generation) <= retired_generation:
                raise ValueError("new owner generation must increase")
            meta["retire_generation"] = retired_generation
            meta["retire_cursor_page"] = meta["owner_head_page"]
            meta["retire_remaining"] = retired_count
            meta["owner_generation"] = int(new_generation)
            meta["owner_head_page"] = None
            meta["owner_count"] = 0
            new_epoch = committed_epoch + 1
            os.fsync(fd)
            counters.fsyncs += 1
            extra_reads, extra_writes = self._write_super(
                fd, committed_epoch, new_epoch, meta
            )
            counters.meta_preads += extra_reads
            counters.meta_pwrites += extra_writes
            os.fsync(fd)
            counters.fsyncs += 1
            return RetirementTrace(
                committed_epoch=new_epoch,
                retired_generation=retired_generation,
                retired_segments=retired_count,
                new_owner_generation=int(new_generation),
                fsyncs=counters.fsyncs,
            )
        finally:
            self._active_counters = None
            os.close(fd)

    def reclaim_step(
        self,
        *,
        budget: int,
        failpoint: Callable[[str], None] | None = None,
    ) -> ReclaimTrace:
        if budget <= 0:
            raise ValueError("reclaim budget must be positive")
        self._reset_operation_state()
        counters = _Counters()
        lifecycle_reads = 0
        lifecycle_writes = 0
        fd = self._open()
        try:
            committed_epoch, meta, _slot = self._read_super(fd, counters)
            meta = dict(meta)
            start = int(self._pending_frontier or 0)
            remaining = int(meta["retire_remaining"])
            if remaining <= 0:
                return ReclaimTrace(
                    committed_epoch=committed_epoch,
                    requested_budget=int(budget),
                    reclaimed_segments=0,
                    remaining_segments=0,
                    free_count=int(meta["free_count"]),
                    radix_node_pwrites=0,
                    lifecycle_header_preads=0,
                    lifecycle_header_pwrites=0,
                    physical_pages_appended=0,
                    physical_bytes_appended=0,
                    fsyncs=0,
                    generation_pages_scanned=0,
                    mapping_nodes_scanned=0,
                    logical_redo=0,
                )
            new_epoch = committed_epoch + 1
            reclaimed = 0
            for _ in range(min(int(budget), remaining)):
                header_base = meta["retire_cursor_page"]
                if header_base is None:
                    raise RuntimeError("retirement cursor ended before retire_remaining")
                header, _slot, preads = self._read_lifecycle(
                    fd, int(header_base), committed_epoch
                )
                lifecycle_reads += preads
                if int(header["status"]) != LIFECYCLE_OWNED:
                    raise RuntimeError("retirement cursor does not reference an OWNED header")
                if int(header["generation"]) != int(meta["retire_generation"]):
                    raise RuntimeError("retirement ownership generation drifted")
                segment_id = int(header["segment_id"])
                segment_base = self._stage_unmap(
                    fd, segment_id, committed_epoch, new_epoch
                )
                if segment_base != int(header_base) + LIFECYCLE_COPIES:
                    raise RuntimeError("lifecycle header does not own the mapped physical segment")
                if failpoint is not None:
                    failpoint("mapping_unlinked")
                extra_reads, writes = self._write_existing_lifecycle(
                    fd,
                    int(header_base),
                    committed_epoch,
                    new_epoch,
                    status=LIFECYCLE_FREE,
                    generation=int(header["generation"]),
                    segment_id=segment_id,
                    next_header_page=meta["free_head_page"],
                )
                lifecycle_reads += extra_reads
                lifecycle_writes += writes
                if failpoint is not None:
                    failpoint("free_header_written")
                meta["free_head_page"] = int(header_base)
                meta["free_count"] = int(meta["free_count"]) + 1
                meta["retire_cursor_page"] = header["next_header_page"]
                meta["retire_remaining"] = int(meta["retire_remaining"]) - 1
                reclaimed += 1
            if int(meta["retire_remaining"]) == 0:
                meta["retire_generation"] = None
                meta["retire_cursor_page"] = None
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
            return ReclaimTrace(
                committed_epoch=new_epoch,
                requested_budget=int(budget),
                reclaimed_segments=reclaimed,
                remaining_segments=int(meta["retire_remaining"]),
                free_count=int(meta["free_count"]),
                radix_node_pwrites=self._pending_radix_pwrites,
                lifecycle_header_preads=lifecycle_reads,
                lifecycle_header_pwrites=lifecycle_writes,
                physical_pages_appended=end - start,
                physical_bytes_appended=(end - start) * PAGE_SIZE,
                fsyncs=counters.fsyncs,
                generation_pages_scanned=0,
                mapping_nodes_scanned=0,
                logical_redo=0,
            )
        finally:
            self._active_counters = None
            os.close(fd)

    def reuse_one_mapping(
        self,
        segment_id: int,
        *,
        generation: int | None = None,
        failpoint: Callable[[str], None] | None = None,
    ) -> ReuseTrace:
        segment_id = int(segment_id)
        self._reset_operation_state()
        counters = _Counters()
        lifecycle_reads = 0
        lifecycle_writes = 0
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
            return ReuseTrace(
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
                physical_pages_appended=end - start,
                physical_bytes_appended=(end - start) * PAGE_SIZE,
                fsyncs=counters.fsyncs,
            )
        finally:
            self._active_counters = None
            os.close(fd)

    def reclamation_snapshot(
        self, segment_ids: Iterable[int] = ()
    ) -> dict[str, Any]:
        requested = tuple(int(segment_id) for segment_id in segment_ids)
        self._reset_operation_state()
        fd = self._open()
        try:
            epoch, meta, _slot = FixedPagePrimaryStore._read_super(self, fd)
            mappings = {
                str(segment_id): self._resolve_segment(fd, segment_id, epoch)
                for segment_id in requested
            }
            heads: dict[str, dict[str, Any] | None] = {}
            for name in ("owner_head_page", "retire_cursor_page", "free_head_page"):
                page = meta.get(name)
                if page is None:
                    heads[name] = None
                    continue
                payload, _slot, _preads = self._read_lifecycle(fd, int(page), epoch)
                heads[name] = {"header_page": int(page), **payload}
            frontier = int(meta["next_physical_page"]) * PAGE_SIZE
            size = os.fstat(fd).st_size
            return {
                "committed_epoch": int(epoch),
                "owner_generation": int(meta["owner_generation"]),
                "owner_count": int(meta["owner_count"]),
                "retire_generation": meta["retire_generation"],
                "retire_remaining": int(meta["retire_remaining"]),
                "free_count": int(meta["free_count"]),
                "heads": heads,
                "mappings": mappings,
                "committed_physical_frontier_bytes": frontier,
                "file_size_bytes": size,
                "uncommitted_tail_bytes": max(0, size - frontier),
            }
        finally:
            self._active_counters = None
            os.close(fd)

    def lifecycle_layout(self) -> dict[str, Any]:
        return {
            "magic": LIFECYCLE_MAGIC.decode("ascii"),
            "header_copies": LIFECYCLE_COPIES,
            "header_used_bytes": LIFECYCLE_USED_BYTES,
            "header_page_bytes": PAGE_SIZE,
            "segment_data_pages": SEGMENT_DATA_PAGES,
            "fresh_segment_pages": FRESH_SEGMENT_PAGES,
            "max_fresh_path_pages": MAX_FRESH_PATH_PAGES,
            "max_fresh_path_bytes": MAX_FRESH_PATH_BYTES,
            "ownership_manifest": "intrusive dual-copy lifecycle-header chain",
            "reclaim_metadata_pruning": False,
        }
