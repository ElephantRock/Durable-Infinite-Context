from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from storage.fixed_page_primary import (
    PAGE_MAGIC,
    PAGE_SIZE,
    FixedPageInsertTrace,
    FixedPagePrimaryStore,
    _Counters,
    _Txn,
)

NODE_MAGIC = b"DICMAP30"
SEGMENT_BUCKET_PAGES = 16
DATA_PAGE_COPIES = 2
RADIX_BITS_PER_LEVEL = 8
RADIX_FANOUT = 1 << RADIX_BITS_PER_LEVEL
RADIX_LEVELS = 8
RADIX_NODE_COPIES = 2
ROOT_NODE_BASE_PAGE = 2
INITIAL_PHYSICAL_PAGES = 4
LOGICAL_SEGMENT_BITS = RADIX_BITS_PER_LEVEL * RADIX_LEVELS


@dataclass(frozen=True)
class SegmentedLookupTrace:
    key: str
    found: bool
    path: str
    generations_touched: int
    logical_primary_pages: int
    mapped_primary_pages: int
    primary_physical_preads: int
    metadata_physical_preads: int
    total_physical_preads: int
    logical_page_ids: tuple[int, ...]
    logical_segment_ids: tuple[int, ...]
    physical_offsets: tuple[int, ...]

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["logical_page_ids"] = list(self.logical_page_ids)
        row["logical_segment_ids"] = list(self.logical_segment_ids)
        row["physical_offsets"] = list(self.physical_offsets)
        return row


@dataclass(frozen=True)
class SegmentedInsertTrace:
    base: FixedPageInsertTrace
    new_segments_allocated: int
    physical_pages_appended: int
    physical_bytes_appended: int
    radix_node_pwrites: int
    committed_physical_frontier_bytes: int

    def __getattr__(self, name: str) -> Any:
        return getattr(self.base, name)

    def to_dict(self) -> dict[str, Any]:
        row = self.base.to_dict()
        row.update(
            {
                "new_segments_allocated": self.new_segments_allocated,
                "physical_pages_appended": self.physical_pages_appended,
                "physical_bytes_appended": self.physical_bytes_appended,
                "radix_node_pwrites": self.radix_node_pwrites,
                "committed_physical_frontier_bytes": self.committed_physical_frontier_bytes,
            }
        )
        return row


class SegmentedFixedPagePrimaryStore(FixedPagePrimaryStore):
    """v0.30 process-crash experiment: v0.24 logical primary over a radix extent map.

    Logical bucket/stash page ids retain the v0.24 migration semantics. Physical data
    segments are append-allocated in fixed 16-logical-page units and published through
    an eight-level, dual-copy radix map. The fixed superblock stores the committed
    physical append frontier. Uncommitted append residue can therefore be re-derived
    and truncated without scanning a generation or replaying logical mutations.

    This remains a single-writer experimental store. It does not establish hardware
    power-loss behavior, torn-sector immunity, multi-writer correctness, device-I/O
    counts, or mathematically unbounded logical identifiers.
    """

    def __init__(self, path: str | Path) -> None:
        super().__init__(path)
        self._active_counters: _Counters | None = None
        self._active_failpoint: Callable[[str], None] | None = None
        self._pending_epoch: int | None = None
        self._pending_frontier_start: int | None = None
        self._pending_frontier: int | None = None
        self._pending_nodes: dict[int, dict[str, Any]] = {}
        self._pending_node_slots: dict[int, int] = {}
        self._pending_segments: dict[int, int] = {}
        self._pending_new_segments: set[int] = set()
        self._pending_allocated_pages = 0
        self._pending_radix_pwrites = 0
        self._segment_cache: dict[tuple[int, int], int | None] = {}
        self._lookup_records: dict[int, tuple[int, int | None, tuple[int, ...]]] = {}

    def _reset_operation_state(self) -> None:
        self._active_counters = None
        self._pending_epoch = None
        self._pending_frontier_start = None
        self._pending_frontier = None
        self._pending_nodes = {}
        self._pending_node_slots = {}
        self._pending_segments = {}
        self._pending_new_segments = set()
        self._pending_allocated_pages = 0
        self._pending_radix_pwrites = 0
        self._segment_cache = {}
        self._lookup_records = {}

    @staticmethod
    def _radix_digits(segment_id: int) -> tuple[int, ...]:
        if segment_id < 0 or segment_id >= (1 << LOGICAL_SEGMENT_BITS):
            raise ValueError("logical segment id exceeds fixed 64-bit radix namespace")
        return tuple(
            (segment_id >> shift) & (RADIX_FANOUT - 1)
            for shift in range(
                LOGICAL_SEGMENT_BITS - RADIX_BITS_PER_LEVEL,
                -1,
                -RADIX_BITS_PER_LEVEL,
            )
        )

    @staticmethod
    def _logical_segment(page_id: int) -> tuple[int, int]:
        if page_id < 0:
            raise ValueError("logical page id must be non-negative")
        return divmod(page_id, SEGMENT_BUCKET_PAGES)

    @staticmethod
    def _physical_data_offset(segment_base_page: int, local_page: int, copy_slot: int) -> int:
        return (
            segment_base_page + DATA_PAGE_COPIES * local_page + copy_slot
        ) * PAGE_SIZE

    @staticmethod
    def _node_offset(node_base_page: int, copy_slot: int) -> int:
        return (node_base_page + copy_slot) * PAGE_SIZE

    def _bump_meta_preads(self, count: int) -> None:
        if self._active_counters is not None:
            self._active_counters.meta_preads += count

    def _bump_meta_pwrites(self, count: int) -> None:
        if self._active_counters is not None:
            self._active_counters.meta_pwrites += count

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
        if self.path.exists():
            raise RuntimeError("segmented fixed-page primary already exists")
        if initial_capacity <= 0 or initial_capacity & (initial_capacity - 1):
            raise ValueError("initial_capacity must be a positive power of two")
        if bucket_size <= 0 or initial_capacity % bucket_size:
            raise ValueError("bucket_size must divide initial_capacity")
        bucket_count = initial_capacity // bucket_size
        if bucket_count <= 1 or bucket_count & (bucket_count - 1):
            raise ValueError("bucket count must be a power of two greater than one")
        if not (0.0 < max_load < 1.0):
            raise ValueError("max_load must be between zero and one")
        if max_kicks <= 0 or stash_capacity <= 0 or migration_slot_budget <= 0:
            raise ValueError("invalid bounded placement configuration")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        try:
            first_pages = self._generation_pages(initial_capacity, bucket_size)
            meta = {
                "format": 30,
                "current_generation": 0,
                "current_capacity": initial_capacity,
                "current_rows": 0,
                "current_base_page": 0,
                "old_generation": None,
                "old_capacity": None,
                "old_rows": 0,
                "old_base_page": None,
                "migration_cursor": 0,
                "migration_limit": 0,
                "next_generation": 1,
                "next_page_id": first_pages,
                "live_size": 0,
                "max_load": max_load,
                "bucket_size": bucket_size,
                "max_kicks": max_kicks,
                "stash_capacity": stash_capacity,
                "migration_slot_budget": migration_slot_budget,
                "force_same_pair": int(force_same_pair),
                "radix_levels": RADIX_LEVELS,
                "segment_bucket_pages": SEGMENT_BUCKET_PAGES,
                "root_node_base_page": ROOT_NODE_BASE_PAGE,
                "next_physical_page": INITIAL_PHYSICAL_PAGES,
            }
            os.ftruncate(fd, INITIAL_PHYSICAL_PAGES * PAGE_SIZE)
            os.pwrite(
                fd,
                self._pack_record(NODE_MAGIC, 1, {"depth": 0, "entries": {}}),
                self._node_offset(ROOT_NODE_BASE_PAGE, 0),
            )
            os.pwrite(
                fd,
                self._pack_record(self._super_magic(), 1, meta),
                self._super_offset(0),
            )
            os.fsync(fd)
        finally:
            os.close(fd)

    @staticmethod
    def _super_magic() -> bytes:
        from storage.fixed_page_primary import SUPER_MAGIC

        return SUPER_MAGIC

    def _read_super(
        self, fd: int, counters: _Counters | None = None
    ) -> tuple[int, dict[str, Any], int]:
        row = super()._read_super(fd, counters)
        if counters is not None:
            self._active_counters = counters
            self._segment_cache = {}
            self._lookup_records = {}
            epoch, meta, _slot = row
            if self._pending_frontier is None:
                frontier = int(meta["next_physical_page"])
                self._pending_epoch = int(epoch) + 1
                self._pending_frontier_start = frontier
                self._pending_frontier = frontier
        return row

    def _read_node(
        self,
        fd: int,
        node_base_page: int,
        committed_epoch: int,
    ) -> tuple[dict[str, Any], int]:
        candidates: list[tuple[int, dict[str, Any], int]] = []
        for copy_slot in (0, 1):
            data = os.pread(fd, PAGE_SIZE, self._node_offset(node_base_page, copy_slot))
            parsed = self._unpack_record(data, NODE_MAGIC)
            if parsed is None:
                continue
            epoch, payload = parsed
            if epoch <= committed_epoch:
                candidates.append((epoch, payload, copy_slot))
        self._bump_meta_preads(2)
        if not candidates:
            raise RuntimeError(f"no valid radix node at physical page {node_base_page}")
        _epoch, payload, slot = max(candidates, key=lambda row: row[0])
        if not isinstance(payload.get("entries"), dict):
            raise RuntimeError("radix node entries are malformed")
        return {"depth": int(payload["depth"]), "entries": dict(payload["entries"])}, slot

    def _node_for_pending_write(
        self,
        fd: int,
        node_base_page: int,
        committed_epoch: int,
    ) -> tuple[dict[str, Any], int | None]:
        if node_base_page in self._pending_nodes:
            return (
                {
                    "depth": int(self._pending_nodes[node_base_page]["depth"]),
                    "entries": dict(self._pending_nodes[node_base_page]["entries"]),
                },
                self._pending_node_slots[node_base_page],
            )
        payload, slot = self._read_node(fd, node_base_page, committed_epoch)
        return payload, slot

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
            self._pack_record(NODE_MAGIC, new_epoch, payload),
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
            self._pack_record(NODE_MAGIC, new_epoch, payload),
            self._node_offset(node_base_page, target_slot),
        )
        self._pending_node_slots[node_base_page] = target_slot
        self._pending_nodes[node_base_page] = {
            "depth": int(payload["depth"]),
            "entries": dict(payload["entries"]),
        }
        self._pending_radix_pwrites += 1
        self._bump_meta_pwrites(1)

    def _resolve_segment(
        self,
        fd: int,
        segment_id: int,
        committed_epoch: int,
    ) -> int | None:
        cache_key = (int(committed_epoch), int(segment_id))
        if cache_key in self._segment_cache:
            return self._segment_cache[cache_key]
        digits = self._radix_digits(segment_id)
        node_base = ROOT_NODE_BASE_PAGE
        for depth in range(RADIX_LEVELS):
            payload, _slot = self._read_node(fd, node_base, committed_epoch)
            if int(payload["depth"]) != depth:
                raise RuntimeError("radix node depth drifted")
            value = payload["entries"].get(str(digits[depth]))
            if value is None:
                self._segment_cache[cache_key] = None
                return None
            if depth == RADIX_LEVELS - 1:
                segment_base = int(value)
                self._segment_cache[cache_key] = segment_base
                return segment_base
            node_base = int(value)
        raise AssertionError("radix traversal fell through")

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
            payload, slot = self._node_for_pending_write(
                fd, node_base, committed_epoch
            )
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
        self._pending_segments[segment_id] = segment_base
        self._segment_cache[(int(new_epoch), int(segment_id))] = segment_base
        return segment_base

    def _read_logical_page(
        self,
        fd: int,
        page_id: int,
        committed_epoch: int,
        slots: int,
    ) -> tuple[list[str | None], int]:
        segment_id, local_page = self._logical_segment(page_id)
        segment_base = self._resolve_segment(fd, segment_id, committed_epoch)
        if segment_base is None:
            self._lookup_records[page_id] = (segment_id, None, tuple())
            return [None] * slots, 0
        candidates: list[tuple[int, list[str | None]]] = []
        offsets: list[int] = []
        for copy_slot in (0, 1):
            offset = self._physical_data_offset(segment_base, local_page, copy_slot)
            offsets.append(offset)
            data = os.pread(fd, PAGE_SIZE, offset)
            parsed = self._unpack_record(data, PAGE_MAGIC)
            if parsed is None:
                continue
            epoch, payload = parsed
            if epoch > committed_epoch:
                continue
            keys = payload.get("keys")
            if not isinstance(keys, list) or len(keys) != slots:
                continue
            candidates.append(
                (epoch, [None if key is None else str(key) for key in keys])
            )
        self._lookup_records[page_id] = (segment_id, segment_base, tuple(offsets))
        if not candidates:
            return [None] * slots, 2
        return list(max(candidates, key=lambda row: row[0])[1]), 2

    def _write_logical_page(
        self,
        fd: int,
        page_id: int,
        committed_epoch: int,
        new_epoch: int,
        keys: list[str | None],
    ) -> tuple[int, int]:
        segment_id, local_page = self._logical_segment(page_id)
        if segment_id in self._pending_segments:
            segment_base = self._pending_segments[segment_id]
        else:
            segment_base = self._resolve_segment(fd, segment_id, committed_epoch)
            if segment_base is None:
                segment_base = self._reserve_fresh_mapping(
                    fd, segment_id, committed_epoch, new_epoch
                )
            else:
                self._pending_segments[segment_id] = segment_base

        valid: list[tuple[int, int]] = []
        for copy_slot in (0, 1):
            data = os.pread(
                fd,
                PAGE_SIZE,
                self._physical_data_offset(segment_base, local_page, copy_slot),
            )
            parsed = self._unpack_record(data, PAGE_MAGIC)
            if parsed is not None:
                valid.append((parsed[0], copy_slot))
        committed = [row for row in valid if row[0] <= committed_epoch]
        if committed:
            active_slot = max(committed, key=lambda row: row[0])[1]
            target_slot = 1 - active_slot
        else:
            future_slots = [slot for epoch, slot in valid if epoch > committed_epoch]
            target_slot = future_slots[0] if future_slots else 0
        os.pwrite(
            fd,
            self._pack_record(PAGE_MAGIC, new_epoch, {"keys": keys}),
            self._physical_data_offset(segment_base, local_page, target_slot),
        )
        return 2, 1

    def _write_super(
        self,
        fd: int,
        committed_epoch: int,
        new_epoch: int,
        meta: dict[str, Any],
    ) -> tuple[int, int]:
        committed_meta = dict(meta)
        if self._pending_frontier is not None:
            committed_meta["next_physical_page"] = int(self._pending_frontier)
        return super()._write_super(fd, committed_epoch, new_epoch, committed_meta)

    def _start_migration_tx(self, tx: _Txn) -> None:
        meta = tx.meta
        old_generation = int(meta["current_generation"])
        old_capacity = int(meta["current_capacity"])
        old_base = int(meta["current_base_page"])
        new_capacity = old_capacity * 2
        new_generation = int(meta["next_generation"])
        new_base = int(meta["next_page_id"])
        new_pages = self._generation_pages(new_capacity, int(meta["bucket_size"]))
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

    def insert(
        self, key: str, failpoint: Callable[[str], None] | None = None
    ) -> SegmentedInsertTrace:
        self._reset_operation_state()
        self._active_failpoint = failpoint
        try:
            base = super().insert(key, failpoint=failpoint)
            start = int(self._pending_frontier_start or 0)
            end = int(self._pending_frontier or start)
            return SegmentedInsertTrace(
                base=base,
                new_segments_allocated=len(self._pending_new_segments),
                physical_pages_appended=end - start,
                physical_bytes_appended=(end - start) * PAGE_SIZE,
                radix_node_pwrites=self._pending_radix_pwrites,
                committed_physical_frontier_bytes=end * PAGE_SIZE,
            )
        finally:
            self._active_failpoint = None
            self._active_counters = None

    def lookup(self, key: str) -> SegmentedLookupTrace:
        self._reset_operation_state()
        counters = _Counters()
        fd = self._open()
        try:
            epoch, meta, _slot = self._read_super(fd, counters)
            tx = _Txn(self, fd, epoch, meta, counters)
            pages: list[int] = []
            found = self._lookup_generation_tx(
                tx,
                int(meta["current_generation"]),
                int(meta["current_capacity"]),
                key,
                pages,
            )
            path = "current" if found else "missing"
            generations = 1
            if not found and meta["old_generation"] is not None:
                generations = 2
                found = self._lookup_generation_tx(
                    tx,
                    int(meta["old_generation"]),
                    int(meta["old_capacity"]),
                    key,
                    pages,
                )
                if found:
                    path = "old"
            segment_ids: list[int] = []
            offsets: list[int] = []
            mapped_pages = 0
            for page_id in pages:
                record = self._lookup_records.get(page_id)
                if record is None:
                    continue
                segment_id, segment_base, page_offsets = record
                segment_ids.append(segment_id)
                if segment_base is not None:
                    mapped_pages += 1
                    offsets.extend(page_offsets)
            return SegmentedLookupTrace(
                key=key,
                found=found,
                path=path,
                generations_touched=generations,
                logical_primary_pages=len(pages),
                mapped_primary_pages=mapped_pages,
                primary_physical_preads=counters.data_preads,
                metadata_physical_preads=counters.meta_preads,
                total_physical_preads=counters.data_preads + counters.meta_preads,
                logical_page_ids=tuple(pages),
                logical_segment_ids=tuple(segment_ids),
                physical_offsets=tuple(offsets),
            )
        finally:
            self._active_counters = None
            os.close(fd)

    def meta_snapshot(self) -> dict[str, Any]:
        out = super().meta_snapshot()
        frontier = int(out["next_physical_page"]) * PAGE_SIZE
        out["committed_physical_frontier_bytes"] = frontier
        out["uncommitted_tail_bytes"] = max(0, int(out["file_size_bytes"]) - frontier)
        return out

    @staticmethod
    def _semantic_meta(meta: dict[str, Any]) -> dict[str, Any]:
        excluded = {
            "file_size_bytes",
            "allocated_bytes",
            "committed_physical_frontier_bytes",
            "uncommitted_tail_bytes",
        }
        return {key: value for key, value in meta.items() if key not in excluded}

    def committed_state(self, keys: list[str] | tuple[str, ...]) -> dict[str, Any]:
        meta = self.meta_snapshot()
        return {
            "epoch": int(meta["committed_epoch"]),
            "meta": self._semantic_meta(meta),
            "visibility": {key: bool(self.lookup(key).found) for key in keys},
        }

    def recover(self) -> dict[str, Any]:
        fd = self._open()
        try:
            epoch, meta, _slot = super()._read_super(fd)
            target_bytes = int(meta["next_physical_page"]) * PAGE_SIZE
            before = os.fstat(fd).st_size
            if before > target_bytes:
                os.ftruncate(fd, target_bytes)
                os.fsync(fd)
            after = os.fstat(fd).st_size
            return {
                "logical_work": 0,
                "generation_pages_scanned": 0,
                "mapping_nodes_scanned": 0,
                "frontier_superblock_preads": 2,
                "committed_epoch": int(epoch),
                "tail_before_bytes": max(0, before - target_bytes),
                "tail_after_bytes": max(0, after - target_bytes),
                "physical_truncated_bytes": max(0, before - after),
            }
        finally:
            os.close(fd)

    def address_formula(self) -> dict[str, Any]:
        return {
            "page_size": PAGE_SIZE,
            "superblock_copies": 2,
            "logical_page_copies": 2,
            "segment_bucket_pages": SEGMENT_BUCKET_PAGES,
            "segment_data_physical_pages": DATA_PAGE_COPIES * SEGMENT_BUCKET_PAGES,
            "radix_levels": RADIX_LEVELS,
            "radix_fanout": RADIX_FANOUT,
            "radix_node_copies": RADIX_NODE_COPIES,
            "logical_segment_id": "floor(logical_page_id/16)",
            "local_page": "logical_page_id mod 16",
            "physical_data_offset": (
                "PAGE_SIZE * (segment_physical_base + 2*local_page + copy_slot)"
            ),
            "physical_segment_placement": "append allocated at committed next_physical_page frontier",
            "primary_index_structure": "fixed-depth radix extent map; no comparison-tree traversal",
        }
