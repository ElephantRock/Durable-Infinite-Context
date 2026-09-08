from __future__ import annotations

import hashlib
import json
import os
import struct
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

PAGE_SIZE = 4096
SUPER_MAGIC = b"DICSUP24"
PAGE_MAGIC = b"DICPAG24"
HEADER = struct.Struct(">8sQII")
HEADER_SIZE = HEADER.size
MAX_PAYLOAD = PAGE_SIZE - HEADER_SIZE


class PrimaryAdmissionExhausted(RuntimeError):
    pass


@dataclass(frozen=True)
class FixedPageLookupTrace:
    key: str
    found: bool
    path: str
    generations_touched: int
    logical_primary_pages: int
    primary_physical_preads: int
    metadata_physical_preads: int
    total_physical_preads: int
    logical_page_ids: tuple[int, ...]
    physical_offsets: tuple[int, ...]

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["logical_page_ids"] = list(self.logical_page_ids)
        row["physical_offsets"] = list(self.physical_offsets)
        return row


@dataclass(frozen=True)
class FixedPageInsertTrace:
    key: str
    duplicate: bool
    committed_epoch: int
    migration_started: bool
    migration_completed: bool
    migration_source_slots_scanned: int
    migration_rows_moved: int
    placement_work: int
    logical_pages_read: int
    logical_pages_written: int
    data_physical_preads: int
    data_physical_pwrites: int
    metadata_physical_preads: int
    metadata_physical_pwrites: int
    fsyncs: int
    live_size: int
    current_capacity: int
    migration_active: bool
    migration_cursor: int
    file_size_bytes: int
    allocated_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _Counters:
    logical_reads: int = 0
    data_preads: int = 0
    data_pwrites: int = 0
    meta_preads: int = 0
    meta_pwrites: int = 0
    fsyncs: int = 0


class _Txn:
    def __init__(
        self,
        store: "FixedPagePrimaryStore",
        fd: int,
        epoch: int,
        meta: dict[str, Any],
        counters: _Counters,
    ) -> None:
        self.store = store
        self.fd = fd
        self.epoch = epoch
        self.meta = dict(meta)
        self.counters = counters
        self.cache: dict[int, list[str | None]] = {}
        self.changes: dict[int, list[str | None]] = {}

    def read_page(self, page_id: int, slots: int) -> list[str | None]:
        if page_id in self.changes:
            return list(self.changes[page_id])
        if page_id in self.cache:
            return list(self.cache[page_id])
        keys, preads = self.store._read_logical_page(
            self.fd, page_id, self.epoch, slots
        )
        self.counters.logical_reads += 1
        self.counters.data_preads += preads
        self.cache[page_id] = list(keys)
        return list(keys)

    def write_page(self, page_id: int, keys: list[str | None]) -> None:
        self.changes[page_id] = list(keys)
        self.cache[page_id] = list(keys)


class FixedPagePrimaryStore:
    """Single-writer v0.24 fixed-page primary experiment.

    Bucket and stash pages have arithmetic byte addresses, not comparison-index keys.
    Every logical page has two physical copies. A transaction writes alternate data-page
    copies at epoch T, fsyncs them, then commits T by writing and fsyncing the alternate
    fixed-size superblock. Reads select the newest valid data copy with epoch <= the
    committed superblock epoch.

    This is a process-crash experiment. It does not claim device I/O counts, power-loss
    torn-write immunity, multi-writer correctness, or integrated exceptional overflow.
    """

    HASH_KEY_1 = b"dic-v024-fixed-h1"
    HASH_KEY_2 = b"dic-v024-fixed-h2"
    CHOICE_KEY = b"dic-v024-fixed-choice"
    VICTIM_KEY = b"dic-v024-fixed-victim"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @staticmethod
    def _hash(key: str, hash_key: bytes) -> int:
        digest = hashlib.blake2b(
            key.encode("utf-8"), digest_size=8, key=hash_key
        ).digest()
        return int.from_bytes(digest, "big")

    @staticmethod
    def _pack_record(magic: bytes, epoch: int, payload: dict[str, Any]) -> bytes:
        raw = json.dumps(
            payload, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if len(raw) > MAX_PAYLOAD:
            raise ValueError(f"fixed page payload too large: {len(raw)}")
        prefix = magic + struct.pack(">QI", epoch, len(raw))
        crc = zlib.crc32(prefix + raw) & 0xFFFFFFFF
        return (
            HEADER.pack(magic, epoch, len(raw), crc)
            + raw
            + bytes(MAX_PAYLOAD - len(raw))
        )

    @staticmethod
    def _unpack_record(
        data: bytes, magic: bytes
    ) -> tuple[int, dict[str, Any]] | None:
        if len(data) != PAGE_SIZE:
            return None
        try:
            got_magic, epoch, length, crc = HEADER.unpack(data[:HEADER_SIZE])
        except struct.error:
            return None
        if got_magic != magic or length > MAX_PAYLOAD:
            return None
        raw = data[HEADER_SIZE : HEADER_SIZE + length]
        prefix = got_magic + struct.pack(">QI", epoch, length)
        if (zlib.crc32(prefix + raw) & 0xFFFFFFFF) != crc:
            return None
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        return int(epoch), payload

    @staticmethod
    def _super_offset(slot: int) -> int:
        return slot * PAGE_SIZE

    @staticmethod
    def _physical_page_index(page_id: int, copy_slot: int) -> int:
        return 2 + 2 * page_id + copy_slot

    @classmethod
    def _page_offset(cls, page_id: int, copy_slot: int) -> int:
        return cls._physical_page_index(page_id, copy_slot) * PAGE_SIZE

    @staticmethod
    def _generation_pages(capacity: int, bucket_size: int) -> int:
        return capacity // bucket_size + 1

    def _open(self) -> int:
        return os.open(self.path, os.O_RDWR)

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
            raise RuntimeError("fixed-page primary already exists")
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
                "format": 1,
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
            }
            os.ftruncate(fd, (2 + 2 * first_pages) * PAGE_SIZE)
            os.pwrite(
                fd,
                self._pack_record(SUPER_MAGIC, 1, meta),
                self._super_offset(0),
            )
            os.fsync(fd)
        finally:
            os.close(fd)

    def _read_super(
        self, fd: int, counters: _Counters | None = None
    ) -> tuple[int, dict[str, Any], int]:
        candidates: list[tuple[int, dict[str, Any], int]] = []
        for slot in (0, 1):
            data = os.pread(fd, PAGE_SIZE, self._super_offset(slot))
            if counters is not None:
                counters.meta_preads += 1
            parsed = self._unpack_record(data, SUPER_MAGIC)
            if parsed is not None:
                epoch, payload = parsed
                candidates.append((epoch, payload, slot))
        if not candidates:
            raise RuntimeError("no valid fixed-page superblock")
        return max(candidates, key=lambda row: row[0])

    def _read_logical_page(
        self,
        fd: int,
        page_id: int,
        committed_epoch: int,
        slots: int,
    ) -> tuple[list[str | None], int]:
        candidates: list[tuple[int, list[str | None]]] = []
        preads = 0
        for copy_slot in (0, 1):
            data = os.pread(fd, PAGE_SIZE, self._page_offset(page_id, copy_slot))
            preads += 1
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
        if not candidates:
            return [None] * slots, preads
        return list(max(candidates, key=lambda row: row[0])[1]), preads

    def _write_logical_page(
        self,
        fd: int,
        page_id: int,
        committed_epoch: int,
        new_epoch: int,
        keys: list[str | None],
    ) -> tuple[int, int]:
        valid: list[tuple[int, int]] = []
        for copy_slot in (0, 1):
            data = os.pread(fd, PAGE_SIZE, self._page_offset(page_id, copy_slot))
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
            self._page_offset(page_id, target_slot),
        )
        return 2, 1

    def _write_super(
        self,
        fd: int,
        committed_epoch: int,
        new_epoch: int,
        meta: dict[str, Any],
    ) -> tuple[int, int]:
        _epoch, _payload, active_slot = self._read_super(fd)
        target_slot = 1 - active_slot
        os.pwrite(
            fd,
            self._pack_record(SUPER_MAGIC, new_epoch, meta),
            self._super_offset(target_slot),
        )
        return 2, 1

    def _descriptor(self, meta: dict[str, Any], generation: int) -> tuple[int, int]:
        if generation == int(meta["current_generation"]):
            return int(meta["current_base_page"]), int(meta["current_capacity"])
        if (
            meta["old_generation"] is not None
            and generation == int(meta["old_generation"])
        ):
            return int(meta["old_base_page"]), int(meta["old_capacity"])
        raise KeyError(generation)

    def _bucket_pair(
        self, meta: dict[str, Any], key: str, capacity: int
    ) -> tuple[int, int]:
        bucket_count = capacity // int(meta["bucket_size"])
        mask = bucket_count - 1
        if int(meta["force_same_pair"]):
            first, second = 0, 1
        else:
            first = self._hash(key, self.HASH_KEY_1) & mask
            second = self._hash(key, self.HASH_KEY_2) & mask
        if second == first:
            second = (second + 1) & mask
        return first, second

    def _bucket_page_id(
        self,
        meta: dict[str, Any],
        generation: int,
        capacity: int,
        bucket: int,
    ) -> int:
        base, actual_capacity = self._descriptor(meta, generation)
        if actual_capacity != capacity:
            raise AssertionError("capacity descriptor drift")
        return base + bucket

    def _stash_page_id(
        self, meta: dict[str, Any], generation: int, capacity: int
    ) -> int:
        base, actual_capacity = self._descriptor(meta, generation)
        if actual_capacity != capacity:
            raise AssertionError("capacity descriptor drift")
        return base + capacity // int(meta["bucket_size"])

    def _lookup_generation_tx(
        self,
        tx: _Txn,
        generation: int,
        capacity: int,
        key: str,
        trace_pages: list[int] | None = None,
    ) -> bool:
        bucket_size = int(tx.meta["bucket_size"])
        first, second = self._bucket_pair(tx.meta, key, capacity)
        for bucket in (first, second):
            page_id = self._bucket_page_id(
                tx.meta, generation, capacity, bucket
            )
            if trace_pages is not None:
                trace_pages.append(page_id)
            if key in tx.read_page(page_id, bucket_size):
                return True
        stash_page = self._stash_page_id(tx.meta, generation, capacity)
        if trace_pages is not None:
            trace_pages.append(stash_page)
        return key in tx.read_page(stash_page, int(tx.meta["stash_capacity"]))

    def _victim_slot(self, key: str, kick: int, bucket_size: int) -> int:
        return self._hash(f"{key}|{kick}", self.VICTIM_KEY) % bucket_size

    def _place_primary_tx(
        self, tx: _Txn, generation: int, capacity: int, key: str
    ) -> tuple[bool, int]:
        before = {page_id: list(keys) for page_id, keys in tx.changes.items()}
        bucket_size = int(tx.meta["bucket_size"])
        stash_capacity = int(tx.meta["stash_capacity"])
        max_kicks = int(tx.meta["max_kicks"])
        first, second = self._bucket_pair(tx.meta, key, capacity)
        work = 0

        for bucket in (first, second):
            page_id = self._bucket_page_id(
                tx.meta, generation, capacity, bucket
            )
            keys = tx.read_page(page_id, bucket_size)
            for slot in range(bucket_size):
                work += 1
                if keys[slot] is None:
                    keys[slot] = key
                    tx.write_page(page_id, keys)
                    return True, work

        current = key
        target = (
            first
            if self._hash(key, self.CHOICE_KEY) & 1 == 0
            else second
        )
        for kick in range(max_kicks):
            page_id = self._bucket_page_id(
                tx.meta, generation, capacity, target
            )
            keys = tx.read_page(page_id, bucket_size)
            victim_slot = self._victim_slot(current, kick, bucket_size)
            victim = keys[victim_slot]
            if victim is None:
                keys[victim_slot] = current
                tx.write_page(page_id, keys)
                work += 1
                return True, work
            keys[victim_slot] = current
            tx.write_page(page_id, keys)
            work += bucket_size + 1
            current = victim
            victim_first, victim_second = self._bucket_pair(
                tx.meta, current, capacity
            )
            if victim_first == target:
                target = victim_second
            elif victim_second == target:
                target = victim_first
            else:
                raise AssertionError("evicted key outside candidate buckets")

        stash_page = self._stash_page_id(tx.meta, generation, capacity)
        stash = tx.read_page(stash_page, stash_capacity)
        for slot in range(stash_capacity):
            work += 1
            if stash[slot] is None:
                stash[slot] = current
                tx.write_page(stash_page, stash)
                return True, work

        tx.changes = {page_id: list(keys) for page_id, keys in before.items()}
        return False, work

    def _delete_virtual_slot_tx(
        self,
        tx: _Txn,
        generation: int,
        capacity: int,
        cursor: int,
    ) -> str | None:
        bucket_size = int(tx.meta["bucket_size"])
        stash_capacity = int(tx.meta["stash_capacity"])
        if cursor < capacity:
            bucket, slot = divmod(cursor, bucket_size)
            page_id = self._bucket_page_id(
                tx.meta, generation, capacity, bucket
            )
            keys = tx.read_page(page_id, bucket_size)
            key = keys[slot]
            if key is not None:
                keys[slot] = None
                tx.write_page(page_id, keys)
            return key
        stash_slot = cursor - capacity
        if stash_slot >= stash_capacity:
            raise IndexError(cursor)
        page_id = self._stash_page_id(tx.meta, generation, capacity)
        keys = tx.read_page(page_id, stash_capacity)
        key = keys[stash_slot]
        if key is not None:
            keys[stash_slot] = None
            tx.write_page(page_id, keys)
        return key

    def _start_migration_tx(self, tx: _Txn) -> None:
        meta = tx.meta
        old_generation = int(meta["current_generation"])
        old_capacity = int(meta["current_capacity"])
        old_base = int(meta["current_base_page"])
        new_capacity = old_capacity * 2
        new_generation = int(meta["next_generation"])
        new_base = int(meta["next_page_id"])
        new_pages = self._generation_pages(
            new_capacity, int(meta["bucket_size"])
        )
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
        os.ftruncate(tx.fd, (2 + 2 * int(meta["next_page_id"])) * PAGE_SIZE)

    def _migrate_tx(self, tx: _Txn) -> tuple[int, int, int, bool]:
        meta = tx.meta
        if meta["old_generation"] is None:
            return 0, 0, 0, False
        scanned = 0
        moved = 0
        work = 0
        budget = int(meta["migration_slot_budget"])
        old_generation = int(meta["old_generation"])
        old_capacity = int(meta["old_capacity"])
        while (
            scanned < budget
            and int(meta["migration_cursor"]) < int(meta["migration_limit"])
        ):
            cursor = int(meta["migration_cursor"])
            key = self._delete_virtual_slot_tx(
                tx, old_generation, old_capacity, cursor
            )
            meta["migration_cursor"] = cursor + 1
            scanned += 1
            if key is None:
                continue
            success, placement_work = self._place_primary_tx(
                tx,
                int(meta["current_generation"]),
                int(meta["current_capacity"]),
                key,
            )
            work += placement_work
            if not success:
                raise PrimaryAdmissionExhausted(
                    "migration destination primary exhausted"
                )
            meta["old_rows"] = int(meta["old_rows"]) - 1
            meta["current_rows"] = int(meta["current_rows"]) + 1
            moved += 1

        completed = int(meta["migration_cursor"]) >= int(meta["migration_limit"])
        if completed:
            if int(meta["old_rows"]) != 0:
                raise AssertionError(
                    f"migration completed with {meta['old_rows']} old rows"
                )
            meta["old_generation"] = None
            meta["old_capacity"] = None
            meta["old_base_page"] = None
            meta["migration_cursor"] = 0
            meta["migration_limit"] = 0
        return scanned, moved, work, completed

    def _prepare_insert(
        self,
        fd: int,
        committed_epoch: int,
        meta: dict[str, Any],
        counters: _Counters,
        key: str,
    ) -> tuple[_Txn, dict[str, Any]]:
        if len(key.encode("utf-8")) > 256:
            raise ValueError("fixed-page keys are capped at 256 UTF-8 bytes")
        tx = _Txn(self, fd, committed_epoch, meta, counters)
        if self._lookup_generation_tx(
            tx,
            int(tx.meta["current_generation"]),
            int(tx.meta["current_capacity"]),
            key,
        ):
            return tx, {
                "duplicate": True,
                "migration_started": False,
                "migration_completed": False,
                "scanned": 0,
                "moved": 0,
                "work": 0,
            }
        if tx.meta["old_generation"] is not None and self._lookup_generation_tx(
            tx,
            int(tx.meta["old_generation"]),
            int(tx.meta["old_capacity"]),
            key,
        ):
            return tx, {
                "duplicate": True,
                "migration_started": False,
                "migration_completed": False,
                "scanned": 0,
                "moved": 0,
                "work": 0,
            }

        migration_started = False
        threshold = int(
            int(tx.meta["current_capacity"]) * float(tx.meta["max_load"])
        )
        if tx.meta["old_generation"] is None:
            if int(tx.meta["current_rows"]) + 1 > threshold:
                self._start_migration_tx(tx)
                migration_started = True
        elif int(tx.meta["current_rows"]) + 1 > threshold:
            raise PrimaryAdmissionExhausted(
                "migration backlog reached current generation threshold"
            )

        success, work = self._place_primary_tx(
            tx,
            int(tx.meta["current_generation"]),
            int(tx.meta["current_capacity"]),
            key,
        )
        if not success:
            raise PrimaryAdmissionExhausted("new key primary placement exhausted")
        tx.meta["current_rows"] = int(tx.meta["current_rows"]) + 1
        tx.meta["live_size"] = int(tx.meta["live_size"]) + 1
        scanned, moved, migration_work, migration_completed = self._migrate_tx(tx)
        return tx, {
            "duplicate": False,
            "migration_started": migration_started,
            "migration_completed": migration_completed,
            "scanned": scanned,
            "moved": moved,
            "work": work + migration_work,
        }

    def insert(
        self, key: str, failpoint: Callable[[str], None] | None = None
    ) -> FixedPageInsertTrace:
        counters = _Counters()
        fd = self._open()
        try:
            committed_epoch, meta, _slot = self._read_super(fd, counters)
            tx, state = self._prepare_insert(
                fd, committed_epoch, meta, counters, key
            )
            if state["duplicate"]:
                stat = os.fstat(fd)
                return FixedPageInsertTrace(
                    key=key,
                    duplicate=True,
                    committed_epoch=committed_epoch,
                    migration_started=False,
                    migration_completed=False,
                    migration_source_slots_scanned=0,
                    migration_rows_moved=0,
                    placement_work=0,
                    logical_pages_read=counters.logical_reads,
                    logical_pages_written=0,
                    data_physical_preads=counters.data_preads,
                    data_physical_pwrites=0,
                    metadata_physical_preads=counters.meta_preads,
                    metadata_physical_pwrites=0,
                    fsyncs=0,
                    live_size=int(meta["live_size"]),
                    current_capacity=int(meta["current_capacity"]),
                    migration_active=meta["old_generation"] is not None,
                    migration_cursor=int(meta["migration_cursor"]),
                    file_size_bytes=stat.st_size,
                    allocated_bytes=stat.st_blocks * 512,
                )

            new_epoch = committed_epoch + 1
            for page_id in sorted(tx.changes):
                extra_reads, extra_writes = self._write_logical_page(
                    fd,
                    page_id,
                    committed_epoch,
                    new_epoch,
                    tx.changes[page_id],
                )
                counters.data_preads += extra_reads
                counters.data_pwrites += extra_writes
            if failpoint is not None:
                failpoint("pages_written")
            os.fsync(fd)
            counters.fsyncs += 1
            if failpoint is not None:
                failpoint("data_synced")
            extra_reads, extra_writes = self._write_super(
                fd, committed_epoch, new_epoch, tx.meta
            )
            counters.meta_preads += extra_reads
            counters.meta_pwrites += extra_writes
            os.fsync(fd)
            counters.fsyncs += 1
            if failpoint is not None:
                failpoint("committed")
            stat = os.fstat(fd)
            return FixedPageInsertTrace(
                key=key,
                duplicate=False,
                committed_epoch=new_epoch,
                migration_started=bool(state["migration_started"]),
                migration_completed=bool(state["migration_completed"]),
                migration_source_slots_scanned=int(state["scanned"]),
                migration_rows_moved=int(state["moved"]),
                placement_work=int(state["work"]),
                logical_pages_read=counters.logical_reads,
                logical_pages_written=len(tx.changes),
                data_physical_preads=counters.data_preads,
                data_physical_pwrites=counters.data_pwrites,
                metadata_physical_preads=counters.meta_preads,
                metadata_physical_pwrites=counters.meta_pwrites,
                fsyncs=counters.fsyncs,
                live_size=int(tx.meta["live_size"]),
                current_capacity=int(tx.meta["current_capacity"]),
                migration_active=tx.meta["old_generation"] is not None,
                migration_cursor=int(tx.meta["migration_cursor"]),
                file_size_bytes=stat.st_size,
                allocated_bytes=stat.st_blocks * 512,
            )
        finally:
            os.close(fd)

    def lookup(self, key: str) -> FixedPageLookupTrace:
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
            offsets: list[int] = []
            for page_id in pages:
                offsets.extend(
                    [self._page_offset(page_id, 0), self._page_offset(page_id, 1)]
                )
            return FixedPageLookupTrace(
                key=key,
                found=found,
                path=path,
                generations_touched=generations,
                logical_primary_pages=len(pages),
                primary_physical_preads=counters.data_preads,
                metadata_physical_preads=counters.meta_preads,
                total_physical_preads=counters.data_preads + counters.meta_preads,
                logical_page_ids=tuple(pages),
                physical_offsets=tuple(offsets),
            )
        finally:
            os.close(fd)

    def meta_snapshot(self) -> dict[str, Any]:
        fd = self._open()
        try:
            epoch, meta, _slot = self._read_super(fd)
            out = dict(meta)
            stat = os.fstat(fd)
            out["committed_epoch"] = epoch
            out["file_size_bytes"] = stat.st_size
            out["allocated_bytes"] = stat.st_blocks * 512
            return out
        finally:
            os.close(fd)

    def _generation_keys(
        self, tx: _Txn, generation: int, capacity: int
    ) -> list[str]:
        bucket_size = int(tx.meta["bucket_size"])
        base, _capacity = self._descriptor(tx.meta, generation)
        keys: list[str] = []
        for bucket in range(capacity // bucket_size):
            keys.extend(
                key
                for key in tx.read_page(base + bucket, bucket_size)
                if key is not None
            )
        stash_id = base + capacity // bucket_size
        keys.extend(
            key
            for key in tx.read_page(stash_id, int(tx.meta["stash_capacity"]))
            if key is not None
        )
        return keys

    def logical_snapshot(self) -> dict[str, Any]:
        fd = self._open()
        try:
            epoch, meta, _slot = self._read_super(fd)
            tx = _Txn(self, fd, epoch, meta, _Counters())
            current = self._generation_keys(
                tx,
                int(meta["current_generation"]),
                int(meta["current_capacity"]),
            )
            old: list[str] = []
            if meta["old_generation"] is not None:
                old = self._generation_keys(
                    tx,
                    int(meta["old_generation"]),
                    int(meta["old_capacity"]),
                )
            return {
                "epoch": epoch,
                "meta": dict(meta),
                "current_keys": sorted(current),
                "old_keys": sorted(old),
                "all_keys": sorted(current + old),
            }
        finally:
            os.close(fd)

    def audit(self) -> dict[str, Any]:
        snapshot = self.logical_snapshot()
        current = snapshot["current_keys"]
        old = snapshot["old_keys"]
        all_keys = snapshot["all_keys"]
        meta = snapshot["meta"]
        duplicates = len(all_keys) - len(set(all_keys))
        valid = (
            duplicates == 0
            and len(current) == int(meta["current_rows"])
            and len(old) == int(meta["old_rows"])
            and len(all_keys) == int(meta["live_size"])
        )
        return {
            "valid": valid,
            "duplicates": duplicates,
            "current_count": len(current),
            "old_count": len(old),
            "live_count": len(all_keys),
        }

    def recover(self) -> dict[str, Any]:
        meta = self.meta_snapshot()
        return {
            "logical_work": 0,
            "committed_epoch": int(meta["committed_epoch"]),
            "audit_valid": bool(self.audit()["valid"]),
        }

    def address_formula(self) -> dict[str, Any]:
        return {
            "page_size": PAGE_SIZE,
            "superblock_copies": 2,
            "logical_page_copies": 2,
            "physical_page_index": "2 + 2*logical_page_id + copy_slot",
            "byte_offset": "PAGE_SIZE * (2 + 2*logical_page_id + copy_slot)",
            "primary_index_structure": "none",
        }
