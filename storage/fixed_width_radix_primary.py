from __future__ import annotations

import os
import struct
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from storage.fixed_page_primary import FixedPagePrimaryStore, PAGE_SIZE, SUPER_MAGIC, _Counters
from storage.segmented_fixed_page_primary import (
    DATA_PAGE_COPIES,
    INITIAL_PHYSICAL_PAGES,
    LOGICAL_SEGMENT_BITS,
    RADIX_BITS_PER_LEVEL,
    RADIX_FANOUT,
    RADIX_LEVELS,
    RADIX_NODE_COPIES,
    ROOT_NODE_BASE_PAGE,
    SEGMENT_BUCKET_PAGES,
)
from storage.transactional_segmented_primary import (
    SegmentedFixedPagePrimaryStore as _TransactionalSegmentedStore,
)

NODE31_MAGIC = b"DICMAP31"
_NODE_HEADER = struct.Struct(">8sQBH")
_NODE_CRC = struct.Struct(">I")
_NODE_BITMAP_BYTES = RADIX_FANOUT // 8
_NODE_POINTER_BYTES = RADIX_FANOUT * 8
FIXED_NODE_USED_BYTES = (
    _NODE_HEADER.size + _NODE_CRC.size + _NODE_BITMAP_BYTES + _NODE_POINTER_BYTES
)
MAX_PHYSICAL_PAGE_ID = (1 << 64) - 1


@dataclass(frozen=True)
class MappingBatchTrace:
    committed_epoch: int
    mappings_requested: int
    new_segments_allocated: int
    physical_pages_appended: int
    physical_bytes_appended: int
    radix_node_pwrites: int
    fsyncs: int
    committed_physical_frontier_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalize_entries(entries: dict[str, Any]) -> dict[int, int]:
    normalized: dict[int, int] = {}
    for raw_key, raw_value in entries.items():
        key = int(raw_key)
        value = int(raw_value)
        if key < 0 or key >= RADIX_FANOUT:
            raise ValueError(f"radix digit out of range: {key}")
        if value < 0 or value > MAX_PHYSICAL_PAGE_ID:
            raise ValueError(f"radix pointer exceeds uint64: {value}")
        if key in normalized:
            raise ValueError(f"duplicate radix digit: {key}")
        normalized[key] = value
    return normalized


def encode_fixed_radix_node(epoch: int, payload: dict[str, Any]) -> bytes:
    depth = int(payload["depth"])
    if depth < 0 or depth >= RADIX_LEVELS:
        raise ValueError(f"radix depth out of range: {depth}")
    entries = _normalize_entries(dict(payload.get("entries", {})))
    if len(entries) > RADIX_FANOUT:
        raise ValueError("radix node exceeds semantic fanout")

    bitmap = bytearray(_NODE_BITMAP_BYTES)
    pointers = bytearray(_NODE_POINTER_BYTES)
    for digit, value in entries.items():
        bitmap[digit // 8] |= 1 << (digit % 8)
        struct.pack_into(">Q", pointers, digit * 8, value)

    prefix = _NODE_HEADER.pack(NODE31_MAGIC, int(epoch), depth, len(entries))
    body = bytes(bitmap) + bytes(pointers)
    crc = zlib.crc32(prefix + body) & 0xFFFFFFFF
    record = prefix + _NODE_CRC.pack(crc) + body
    if len(record) != FIXED_NODE_USED_BYTES:
        raise AssertionError("fixed radix node layout drifted")
    if len(record) > PAGE_SIZE:
        raise AssertionError("fixed radix node no longer fits one physical page")
    return record + bytes(PAGE_SIZE - len(record))


def decode_fixed_radix_node(data: bytes) -> tuple[int, dict[str, Any]] | None:
    if len(data) != PAGE_SIZE:
        return None
    if FIXED_NODE_USED_BYTES > len(data):
        return None
    try:
        magic, epoch, depth, count = _NODE_HEADER.unpack(
            data[: _NODE_HEADER.size]
        )
        crc_offset = _NODE_HEADER.size
        (stored_crc,) = _NODE_CRC.unpack(
            data[crc_offset : crc_offset + _NODE_CRC.size]
        )
    except struct.error:
        return None
    if magic != NODE31_MAGIC or depth >= RADIX_LEVELS or count > RADIX_FANOUT:
        return None

    body_offset = _NODE_HEADER.size + _NODE_CRC.size
    body = data[body_offset:FIXED_NODE_USED_BYTES]
    prefix = data[: _NODE_HEADER.size]
    if (zlib.crc32(prefix + body) & 0xFFFFFFFF) != stored_crc:
        return None

    bitmap = body[:_NODE_BITMAP_BYTES]
    pointers = body[_NODE_BITMAP_BYTES:]
    entries: dict[str, int] = {}
    for digit in range(RADIX_FANOUT):
        if bitmap[digit // 8] & (1 << (digit % 8)):
            try:
                (value,) = struct.unpack_from(">Q", pointers, digit * 8)
            except struct.error:
                return None
            entries[str(digit)] = int(value)
    if len(entries) != count:
        return None
    return int(epoch), {"depth": int(depth), "entries": entries}


class FixedWidthRadixPrimaryStore(_TransactionalSegmentedStore):
    """v0.31 fixed-width radix-node experiment.

    v0.30's logical radix fanout is exactly 256, but its JSON node representation has
    a variable physical size. At sufficiently large physical page numbers, a legal
    256-entry node can exceed one 4096-byte record even though no semantic fanout
    overflow occurred. This variant encodes every node as a fixed 256-slot uint64
    pointer array plus a 256-bit occupancy bitmap. Every legal radix node therefore
    occupies exactly one physical page, independent of pointer magnitude.

    The mapping protocol remains single-writer and dual-copy. A node update rewrites
    one alternate physical copy; fixed radix fanout requires no split or recursive
    split cascade.
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
            epoch, meta, super_slot = FixedPagePrimaryStore._read_super(self, fd)
            meta = dict(meta)
            meta["format"] = 31
            os.pwrite(
                fd,
                self._pack_record(SUPER_MAGIC, epoch, meta),
                self._super_offset(super_slot),
            )
            os.pwrite(
                fd,
                encode_fixed_radix_node(epoch, {"depth": 0, "entries": {}}),
                self._node_offset(ROOT_NODE_BASE_PAGE, 0),
            )
            os.pwrite(
                fd,
                bytes(PAGE_SIZE),
                self._node_offset(ROOT_NODE_BASE_PAGE, 1),
            )
            os.fsync(fd)
        finally:
            os.close(fd)

    def _read_node(
        self,
        fd: int,
        node_base_page: int,
        committed_epoch: int,
    ) -> tuple[dict[str, Any], int]:
        candidates: list[tuple[int, dict[str, Any], int]] = []
        for copy_slot in (0, 1):
            data = os.pread(fd, PAGE_SIZE, self._node_offset(node_base_page, copy_slot))
            parsed = decode_fixed_radix_node(data)
            if parsed is None:
                continue
            epoch, payload = parsed
            if epoch <= committed_epoch:
                candidates.append((epoch, payload, copy_slot))
        self._bump_meta_preads(2)
        if not candidates:
            raise RuntimeError(
                f"no valid fixed-width radix node at physical page {node_base_page}"
            )
        _epoch, payload, slot = max(candidates, key=lambda row: row[0])
        return {
            "depth": int(payload["depth"]),
            "entries": dict(payload["entries"]),
        }, slot

    def _write_existing_node(
        self,
        fd: int,
        node_base_page: int,
        committed_epoch: int,
        new_epoch: int,
        payload: dict[str, Any],
        committed_slot: int | None = None,
    ) -> None:
        if node_base_page in self._pending_node_slots:
            target_slot = self._pending_node_slots[node_base_page]
        else:
            if committed_slot is None:
                _payload, committed_slot = self._read_node(
                    fd, node_base_page, committed_epoch
                )
            target_slot = 1 - int(committed_slot)
            self._pending_node_slots[node_base_page] = target_slot
        os.pwrite(
            fd,
            encode_fixed_radix_node(new_epoch, payload),
            self._node_offset(node_base_page, target_slot),
        )
        self._pending_nodes[node_base_page] = {
            "depth": int(payload["depth"]),
            "entries": dict(payload["entries"]),
        }
        self._pending_radix_pwrites += 1
        self._bump_meta_pwrites(1)

    def _write_new_node(
        self,
        fd: int,
        node_base_page: int,
        new_epoch: int,
        payload: dict[str, Any],
    ) -> None:
        target_slot = 0
        os.pwrite(
            fd,
            encode_fixed_radix_node(new_epoch, payload),
            self._node_offset(node_base_page, target_slot),
        )
        self._pending_node_slots[node_base_page] = target_slot
        self._pending_nodes[node_base_page] = {
            "depth": int(payload["depth"]),
            "entries": dict(payload["entries"]),
        }
        self._pending_radix_pwrites += 1
        self._bump_meta_pwrites(1)

    def _reserve_fresh_mapping(
        self,
        fd: int,
        segment_id: int,
        committed_epoch: int,
        new_epoch: int,
    ) -> int:
        if segment_id in self._pending_segments:
            return self._pending_segments[segment_id]
        existing = self._resolve_segment(fd, segment_id, committed_epoch)
        if existing is not None:
            self._pending_segments[segment_id] = existing
            return existing
        if self._pending_frontier is None:
            raise RuntimeError("physical append frontier was not initialized")

        digits = self._radix_digits(segment_id)
        node_base = ROOT_NODE_BASE_PAGE
        missing_depth: int | None = None
        parent_payload: dict[str, Any] | None = None
        parent_slot: int | None = None
        for depth in range(RADIX_LEVELS):
            payload, slot = self._node_for_pending_write(fd, node_base, committed_epoch)
            if int(payload["depth"]) != depth:
                raise RuntimeError("radix node depth drifted during allocation")
            value = payload["entries"].get(str(digits[depth]))
            if value is None:
                missing_depth = depth
                parent_payload = payload
                parent_slot = slot
                break
            if depth == RADIX_LEVELS - 1:
                segment_base = int(value)
                self._pending_segments[segment_id] = segment_base
                return segment_base
            node_base = int(value)
        if missing_depth is None or parent_payload is None:
            raise AssertionError("fresh segment mapping had no missing radix edge")

        segment_base = int(self._pending_frontier)
        frontier = segment_base + DATA_PAGE_COPIES * SEGMENT_BUCKET_PAGES
        new_node_bases: list[int] = []
        for _depth in range(missing_depth + 1, RADIX_LEVELS):
            new_node_bases.append(frontier)
            frontier += RADIX_NODE_COPIES
        if frontier - 1 > MAX_PHYSICAL_PAGE_ID:
            raise OverflowError("v0.31 physical page pointer exceeds fixed uint64 encoding")
        os.ftruncate(fd, frontier * PAGE_SIZE)
        appended = frontier - int(self._pending_frontier)
        self._pending_allocated_pages += appended
        self._pending_frontier = frontier
        self._pending_new_segments.add(segment_id)
        if self._active_failpoint is not None:
            self._active_failpoint("allocated")

        for index, child_depth in enumerate(range(missing_depth + 1, RADIX_LEVELS)):
            if child_depth == RADIX_LEVELS - 1:
                entries = {str(digits[child_depth]): segment_base}
            else:
                entries = {str(digits[child_depth]): new_node_bases[index + 1]}
            self._write_new_node(
                fd,
                new_node_bases[index],
                new_epoch,
                {"depth": child_depth, "entries": entries},
            )
        if self._active_failpoint is not None:
            self._active_failpoint("children_written")

        parent_entries = dict(parent_payload["entries"])
        parent_key = str(digits[missing_depth])
        if missing_depth == RADIX_LEVELS - 1:
            parent_entries[parent_key] = segment_base
        else:
            parent_entries[parent_key] = new_node_bases[0]
        self._write_existing_node(
            fd,
            node_base,
            committed_epoch,
            new_epoch,
            {"depth": missing_depth, "entries": parent_entries},
            committed_slot=parent_slot,
        )
        if self._active_failpoint is not None:
            self._active_failpoint("parent_written")
        self._pending_segments[segment_id] = segment_base
        self._segment_cache[(int(new_epoch), int(segment_id))] = segment_base
        return segment_base

    def materialize_segment_mappings(
        self,
        segment_ids: Iterable[int],
        failpoint: Callable[[str], None] | None = None,
    ) -> MappingBatchTrace:
        requested = tuple(int(segment_id) for segment_id in segment_ids)
        if not requested:
            raise ValueError("at least one segment id is required")
        if len(set(requested)) != len(requested):
            raise ValueError("duplicate segment ids are not permitted in one batch")
        for segment_id in requested:
            if segment_id < 0 or segment_id >= (1 << LOGICAL_SEGMENT_BITS):
                raise ValueError("logical segment id exceeds fixed 64-bit radix namespace")

        self._reset_operation_state()
        self._active_failpoint = failpoint
        counters = _Counters()
        fd = self._open()
        try:
            committed_epoch, meta, _slot = self._read_super(fd, counters)
            new_epoch = committed_epoch + 1
            for segment_id in requested:
                self._reserve_fresh_mapping(
                    fd, segment_id, committed_epoch, new_epoch
                )
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
            start = int(self._pending_frontier_start or 0)
            end = int(self._pending_frontier or start)
            return MappingBatchTrace(
                committed_epoch=new_epoch,
                mappings_requested=len(requested),
                new_segments_allocated=len(self._pending_new_segments),
                physical_pages_appended=end - start,
                physical_bytes_appended=(end - start) * PAGE_SIZE,
                radix_node_pwrites=self._pending_radix_pwrites,
                fsyncs=counters.fsyncs,
                committed_physical_frontier_bytes=end * PAGE_SIZE,
            )
        finally:
            self._active_failpoint = None
            self._active_counters = None
            os.close(fd)

    def mapping_snapshot(self, segment_ids: Iterable[int] = ()) -> dict[str, Any]:
        requested = tuple(int(segment_id) for segment_id in segment_ids)
        self._reset_operation_state()
        fd = self._open()
        try:
            epoch, meta, _slot = FixedPagePrimaryStore._read_super(self, fd)
            root, _root_slot = self._read_node(fd, ROOT_NODE_BASE_PAGE, epoch)
            mappings: dict[str, int | None] = {}
            for segment_id in requested:
                mappings[str(segment_id)] = self._resolve_segment(
                    fd, segment_id, epoch
                )
            frontier = int(meta["next_physical_page"]) * PAGE_SIZE
            size = os.fstat(fd).st_size
            return {
                "committed_epoch": int(epoch),
                "root_entry_count": len(root["entries"]),
                "committed_physical_frontier_bytes": frontier,
                "file_size_bytes": size,
                "uncommitted_tail_bytes": max(0, size - frontier),
                "mappings": mappings,
            }
        finally:
            self._active_counters = None
            os.close(fd)

    def fixed_node_layout(self) -> dict[str, Any]:
        return {
            "magic": NODE31_MAGIC.decode("ascii"),
            "page_size": PAGE_SIZE,
            "radix_fanout": RADIX_FANOUT,
            "pointer_bits": 64,
            "occupancy_bitmap_bytes": _NODE_BITMAP_BYTES,
            "pointer_array_bytes": _NODE_POINTER_BYTES,
            "used_bytes": FIXED_NODE_USED_BYTES,
            "padding_bytes": PAGE_SIZE - FIXED_NODE_USED_BYTES,
            "node_split_required": False,
        }
