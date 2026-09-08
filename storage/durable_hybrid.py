from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from storage.page_locality import btree_stats


@dataclass(frozen=True)
class DurableLookupTrace:
    key: str
    found: bool
    path: str
    generations_touched: int
    primary_slot_work: int
    primary_page_probes: int
    metadata_rows_read: int
    overflow_checked: bool
    overflow_btree_height: int
    modeled_cold_pages: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DurableInsertTrace:
    key: str
    path: str
    migration_started: bool
    migration_completed: bool
    migration_source_slots_scanned: int
    migration_rows_moved: int
    migration_rows_to_overflow: int
    new_key_primary_work: int
    migration_primary_work: int
    primary_mutation_work: int
    primary_sql_row_writes: int
    overflow_row_writes: int
    metadata_row_writes: int
    duplicate_check_overflow: bool
    current_generation: int
    old_generation: int | None
    migration_cursor: int
    live_size: int
    overflow_rows: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DurableHybridStore:
    """Persistent v0.23 hybrid experiment.

    One bounded bucketized-cuckoo generation receives new writes. When its configured
    load threshold is crossed, a new doubled generation is installed and every later
    insertion scans at most ``migration_slot_budget`` virtual source slots. Live source
    keys are moved transactionally into the new bounded primary or, if bounded primary
    placement fails, into an exact-key SQLite ``WITHOUT ROWID`` overflow B-tree.

    Every logical insert, its bounded migration step, and all migration metadata are in
    one SQLite transaction. Real-process SIGKILL tests therefore ask whether SQLite WAL
    atomicity is sufficient to leave either the complete pre-operation state or the
    complete post-operation state with no application-level redo.

    The primary ``page_probes`` are logical bucket/stash pages inherited from the v0.20
    model. The persistent primary tables themselves are SQLite B-trees; this class does
    *not* claim direct-address physical page locality. Overflow B-tree height comes from
    ``dbstat`` and is likewise index geometry rather than OS/device I/O.
    """

    HASH_KEY_1 = b"dic-v020-cuckoo-h1"
    HASH_KEY_2 = b"dic-v020-cuckoo-h2"
    CHOICE_KEY = b"dic-v020-cuckoo-choice"
    VICTIM_KEY = b"dic-v020-cuckoo-victim"

    META_COLUMNS = (
        "current_generation",
        "current_capacity",
        "current_rows",
        "old_generation",
        "old_capacity",
        "old_rows",
        "migration_cursor",
        "migration_limit",
        "next_generation",
        "live_size",
        "overflow_rows",
        "max_load",
        "bucket_size",
        "max_kicks",
        "stash_capacity",
        "migration_slot_budget",
        "force_same_pair",
    )

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        return conn

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
        if initial_capacity <= 0 or initial_capacity & (initial_capacity - 1):
            raise ValueError("initial_capacity must be a positive power of two")
        if bucket_size <= 0 or initial_capacity % bucket_size:
            raise ValueError("bucket_size must divide initial_capacity")
        bucket_count = initial_capacity // bucket_size
        if bucket_count <= 1 or bucket_count & (bucket_count - 1):
            raise ValueError("initial bucket count must be a power of two greater than one")
        if not (0.0 < max_load < 1.0):
            raise ValueError("max_load must be between zero and one")
        if max_kicks <= 0 or stash_capacity < 0 or migration_slot_budget <= 0:
            raise ValueError("invalid bounded-placement or migration configuration")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS hybrid_meta(
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    current_generation INTEGER NOT NULL,
                    current_capacity INTEGER NOT NULL,
                    current_rows INTEGER NOT NULL,
                    old_generation INTEGER,
                    old_capacity INTEGER,
                    old_rows INTEGER NOT NULL,
                    migration_cursor INTEGER NOT NULL,
                    migration_limit INTEGER NOT NULL,
                    next_generation INTEGER NOT NULL,
                    live_size INTEGER NOT NULL,
                    overflow_rows INTEGER NOT NULL,
                    max_load REAL NOT NULL,
                    bucket_size INTEGER NOT NULL,
                    max_kicks INTEGER NOT NULL,
                    stash_capacity INTEGER NOT NULL,
                    migration_slot_budget INTEGER NOT NULL,
                    force_same_pair INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS primary_slots(
                    generation INTEGER NOT NULL,
                    bucket INTEGER NOT NULL,
                    slot INTEGER NOT NULL,
                    key TEXT NOT NULL,
                    PRIMARY KEY(generation,bucket,slot)
                ) WITHOUT ROWID;
                CREATE TABLE IF NOT EXISTS primary_stash(
                    generation INTEGER NOT NULL,
                    stash_slot INTEGER NOT NULL,
                    key TEXT NOT NULL,
                    PRIMARY KEY(generation,stash_slot)
                ) WITHOUT ROWID;
                CREATE TABLE IF NOT EXISTS overflow(
                    key TEXT PRIMARY KEY
                ) WITHOUT ROWID;
                """
            )
            existing = conn.execute(
                "SELECT COUNT(*) FROM hybrid_meta WHERE singleton=1"
            ).fetchone()[0]
            if existing:
                raise RuntimeError("durable hybrid store is already initialized")
            conn.execute(
                """
                INSERT INTO hybrid_meta(
                    singleton,current_generation,current_capacity,current_rows,
                    old_generation,old_capacity,old_rows,migration_cursor,migration_limit,
                    next_generation,live_size,overflow_rows,max_load,bucket_size,max_kicks,
                    stash_capacity,migration_slot_budget,force_same_pair
                ) VALUES (1,0,?,0,NULL,NULL,0,0,0,1,0,0,?,?,?,?,?,?)
                """,
                (
                    initial_capacity,
                    max_load,
                    bucket_size,
                    max_kicks,
                    stash_capacity,
                    migration_slot_budget,
                    int(force_same_pair),
                ),
            )

    @staticmethod
    def _hash(key: str, hash_key: bytes) -> int:
        digest = hashlib.blake2b(
            key.encode("utf-8"), digest_size=8, key=hash_key
        ).digest()
        return int.from_bytes(digest, "big")

    def _meta_tx(self, conn: sqlite3.Connection) -> dict[str, Any]:
        row = conn.execute("SELECT * FROM hybrid_meta WHERE singleton=1").fetchone()
        if row is None:
            raise RuntimeError("durable hybrid store is not initialized")
        return {column: row[column] for column in self.META_COLUMNS}

    def _write_meta_tx(self, conn: sqlite3.Connection, meta: dict[str, Any]) -> None:
        assignments = ",".join(f"{column}=?" for column in self.META_COLUMNS)
        conn.execute(
            f"UPDATE hybrid_meta SET {assignments} WHERE singleton=1",
            tuple(meta[column] for column in self.META_COLUMNS),
        )

    def _bucket_pair(
        self, meta: dict[str, Any], key: str, capacity: int
    ) -> tuple[int, int]:
        bucket_size = int(meta["bucket_size"])
        bucket_count = capacity // bucket_size
        mask = bucket_count - 1
        if int(meta["force_same_pair"]):
            first, second = 0, 1
        else:
            first = self._hash(key, self.HASH_KEY_1) & mask
            second = self._hash(key, self.HASH_KEY_2) & mask
        if second == first:
            second = (second + 1) & mask
        return first, second

    def _first_empty_tx(
        self,
        conn: sqlite3.Connection,
        generation: int,
        bucket: int,
        bucket_size: int,
    ) -> tuple[int | None, int]:
        rows = {
            int(row["slot"]): str(row["key"])
            for row in conn.execute(
                "SELECT slot,key FROM primary_slots WHERE generation=? AND bucket=?",
                (generation, bucket),
            )
        }
        inspections = 0
        for slot in range(bucket_size):
            inspections += 1
            if slot not in rows:
                return slot, inspections
        return None, inspections

    def _victim_slot(self, key: str, kick: int, bucket_size: int) -> int:
        return self._hash(f"{key}|{kick}", self.VICTIM_KEY) % bucket_size

    def _stash_empty_tx(
        self,
        conn: sqlite3.Connection,
        generation: int,
        stash_capacity: int,
    ) -> int | None:
        used = {
            int(row["stash_slot"])
            for row in conn.execute(
                "SELECT stash_slot FROM primary_stash WHERE generation=?",
                (generation,),
            )
        }
        for slot in range(stash_capacity):
            if slot not in used:
                return slot
        return None

    def _place_primary_tx(
        self,
        conn: sqlite3.Connection,
        meta: dict[str, Any],
        generation: int,
        capacity: int,
        key: str,
    ) -> dict[str, int | bool]:
        bucket_size = int(meta["bucket_size"])
        max_kicks = int(meta["max_kicks"])
        stash_capacity = int(meta["stash_capacity"])
        first, second = self._bucket_pair(meta, key, capacity)
        inspections = 0
        relocations = 0
        rollback_writes = 0
        sql_writes = 0

        for bucket in (first, second):
            empty, scanned = self._first_empty_tx(
                conn, generation, bucket, bucket_size
            )
            inspections += scanned
            if empty is not None:
                conn.execute(
                    "INSERT INTO primary_slots(generation,bucket,slot,key) VALUES (?,?,?,?)",
                    (generation, bucket, empty, key),
                )
                sql_writes += 1
                return {
                    "success": True,
                    "stashed": False,
                    "work": inspections,
                    "sql_writes": sql_writes,
                }

        current = key
        target = (
            first
            if self._hash(key, self.CHOICE_KEY) & 1 == 0
            else second
        )
        rollback_log: list[tuple[int, int, str]] = []

        for kick in range(max_kicks):
            empty, scanned = self._first_empty_tx(
                conn, generation, target, bucket_size
            )
            inspections += scanned
            if empty is not None:
                conn.execute(
                    "INSERT INTO primary_slots(generation,bucket,slot,key) VALUES (?,?,?,?)",
                    (generation, target, empty, current),
                )
                sql_writes += 1
                return {
                    "success": True,
                    "stashed": False,
                    "work": inspections + relocations,
                    "sql_writes": sql_writes,
                }

            victim_slot = self._victim_slot(current, kick, bucket_size)
            row = conn.execute(
                "SELECT key FROM primary_slots WHERE generation=? AND bucket=? AND slot=?",
                (generation, target, victim_slot),
            ).fetchone()
            if row is None:
                raise AssertionError("full bucket unexpectedly contained an empty slot")
            victim = str(row["key"])
            rollback_log.append((target, victim_slot, victim))
            conn.execute(
                "UPDATE primary_slots SET key=? WHERE generation=? AND bucket=? AND slot=?",
                (current, generation, target, victim_slot),
            )
            sql_writes += 1
            relocations += 1
            current = victim

            victim_first, victim_second = self._bucket_pair(meta, current, capacity)
            if victim_first == target:
                target = victim_second
            elif victim_second == target:
                target = victim_first
            else:
                raise AssertionError("evicted key was not stored in a candidate bucket")

        stash_slot = self._stash_empty_tx(conn, generation, stash_capacity)
        if stash_slot is not None:
            conn.execute(
                "INSERT INTO primary_stash(generation,stash_slot,key) VALUES (?,?,?)",
                (generation, stash_slot, current),
            )
            sql_writes += 1
            return {
                "success": True,
                "stashed": True,
                "work": inspections + relocations + 1,
                "sql_writes": sql_writes,
            }

        for bucket, slot, previous in reversed(rollback_log):
            conn.execute(
                "UPDATE primary_slots SET key=? WHERE generation=? AND bucket=? AND slot=?",
                (previous, generation, bucket, slot),
            )
            sql_writes += 1
            rollback_writes += 1

        return {
            "success": False,
            "stashed": False,
            "work": inspections + relocations + rollback_writes,
            "sql_writes": sql_writes,
        }

    def _lookup_generation_tx(
        self,
        conn: sqlite3.Connection,
        meta: dict[str, Any],
        generation: int,
        capacity: int,
        key: str,
    ) -> tuple[bool, int, int]:
        bucket_size = int(meta["bucket_size"])
        first, second = self._bucket_pair(meta, key, capacity)
        slot_work = 0
        page_probes = 0

        for bucket in (first, second):
            page_probes += 1
            rows = {
                int(row["slot"]): str(row["key"])
                for row in conn.execute(
                    "SELECT slot,key FROM primary_slots WHERE generation=? AND bucket=?",
                    (generation, bucket),
                )
            }
            for slot in range(bucket_size):
                slot_work += 1
                if rows.get(slot) == key:
                    return True, slot_work, page_probes

        stash_rows = [
            str(row["key"])
            for row in conn.execute(
                "SELECT key FROM primary_stash WHERE generation=? ORDER BY stash_slot",
                (generation,),
            )
        ]
        if stash_rows:
            page_probes += 1
        for current in stash_rows:
            slot_work += 1
            if current == key:
                return True, slot_work, page_probes
        return False, slot_work, page_probes

    def _lookup_tx(
        self,
        conn: sqlite3.Connection,
        meta: dict[str, Any],
        key: str,
    ) -> DurableLookupTrace:
        current_found, current_work, current_pages = self._lookup_generation_tx(
            conn,
            meta,
            int(meta["current_generation"]),
            int(meta["current_capacity"]),
            key,
        )
        if current_found:
            return DurableLookupTrace(
                key,
                True,
                "current",
                1,
                current_work,
                current_pages,
                1,
                False,
                0,
                current_pages,
            )

        primary_work = current_work
        primary_pages = current_pages
        generations = 1
        old_generation = meta["old_generation"]
        if old_generation is not None:
            generations = 2
            old_found, old_work, old_pages = self._lookup_generation_tx(
                conn,
                meta,
                int(old_generation),
                int(meta["old_capacity"]),
                key,
            )
            primary_work += old_work
            primary_pages += old_pages
            if old_found:
                return DurableLookupTrace(
                    key,
                    True,
                    "old",
                    generations,
                    primary_work,
                    primary_pages,
                    1,
                    False,
                    0,
                    primary_pages,
                )

        if int(meta["overflow_rows"]) == 0:
            return DurableLookupTrace(
                key,
                False,
                "none",
                generations,
                primary_work,
                primary_pages,
                1,
                False,
                0,
                primary_pages,
            )

        height = btree_stats(conn, "overflow").height
        row = conn.execute("SELECT key FROM overflow WHERE key=?", (key,)).fetchone()
        found = row is not None
        return DurableLookupTrace(
            key,
            found,
            "overflow" if found else "none",
            generations,
            primary_work,
            primary_pages,
            1,
            True,
            height,
            primary_pages + height,
        )

    def lookup(self, key: str) -> DurableLookupTrace:
        with self.connect() as conn:
            meta = self._meta_tx(conn)
            return self._lookup_tx(conn, meta, key)

    def _start_migration(self, meta: dict[str, Any]) -> None:
        if meta["old_generation"] is not None:
            raise RuntimeError("overlapping primary migration is not allowed")
        old_generation = int(meta["current_generation"])
        old_capacity = int(meta["current_capacity"])
        old_rows = int(meta["current_rows"])
        new_generation = int(meta["next_generation"])
        meta["old_generation"] = old_generation
        meta["old_capacity"] = old_capacity
        meta["old_rows"] = old_rows
        meta["current_generation"] = new_generation
        meta["current_capacity"] = old_capacity * 2
        meta["current_rows"] = 0
        meta["migration_cursor"] = 0
        meta["migration_limit"] = old_capacity + int(meta["stash_capacity"])
        meta["next_generation"] = new_generation + 1

    def _source_key_tx(
        self,
        conn: sqlite3.Connection,
        meta: dict[str, Any],
        virtual_position: int,
    ) -> tuple[str | None, tuple[str, int, int]]:
        old_generation = int(meta["old_generation"])
        old_capacity = int(meta["old_capacity"])
        bucket_size = int(meta["bucket_size"])
        if virtual_position < old_capacity:
            bucket = virtual_position // bucket_size
            slot = virtual_position % bucket_size
            row = conn.execute(
                "SELECT key FROM primary_slots WHERE generation=? AND bucket=? AND slot=?",
                (old_generation, bucket, slot),
            ).fetchone()
            return (
                None if row is None else str(row["key"]),
                ("slot", bucket, slot),
            )
        stash_slot = virtual_position - old_capacity
        row = conn.execute(
            "SELECT key FROM primary_stash WHERE generation=? AND stash_slot=?",
            (old_generation, stash_slot),
        ).fetchone()
        return (
            None if row is None else str(row["key"]),
            ("stash", stash_slot, -1),
        )

    def _delete_source_tx(
        self,
        conn: sqlite3.Connection,
        meta: dict[str, Any],
        location: tuple[str, int, int],
    ) -> int:
        old_generation = int(meta["old_generation"])
        kind, first, second = location
        if kind == "slot":
            conn.execute(
                "DELETE FROM primary_slots WHERE generation=? AND bucket=? AND slot=?",
                (old_generation, first, second),
            )
        else:
            conn.execute(
                "DELETE FROM primary_stash WHERE generation=? AND stash_slot=?",
                (old_generation, first),
            )
        return 1

    def _migrate_step_tx(
        self,
        conn: sqlite3.Connection,
        meta: dict[str, Any],
    ) -> dict[str, int | bool]:
        if meta["old_generation"] is None:
            return {
                "scanned": 0,
                "moved": 0,
                "to_overflow": 0,
                "primary_work": 0,
                "primary_sql_writes": 0,
                "overflow_writes": 0,
                "completed": False,
            }

        budget = int(meta["migration_slot_budget"])
        scanned = 0
        moved = 0
        to_overflow = 0
        primary_work = 0
        primary_sql_writes = 0
        overflow_writes = 0
        limit = int(meta["migration_limit"])

        while scanned < budget and int(meta["migration_cursor"]) < limit:
            position = int(meta["migration_cursor"])
            meta["migration_cursor"] = position + 1
            scanned += 1
            key, location = self._source_key_tx(conn, meta, position)
            if key is None:
                continue

            placement = self._place_primary_tx(
                conn,
                meta,
                int(meta["current_generation"]),
                int(meta["current_capacity"]),
                key,
            )
            primary_work += int(placement["work"])
            primary_sql_writes += int(placement["sql_writes"])
            if bool(placement["success"]):
                meta["current_rows"] = int(meta["current_rows"]) + 1
            else:
                conn.execute("INSERT INTO overflow(key) VALUES (?)", (key,))
                meta["overflow_rows"] = int(meta["overflow_rows"]) + 1
                overflow_writes += 1
                to_overflow += 1

            primary_sql_writes += self._delete_source_tx(conn, meta, location)
            meta["old_rows"] = int(meta["old_rows"]) - 1
            moved += 1

        completed = int(meta["migration_cursor"]) >= limit
        if completed:
            if int(meta["old_rows"]) != 0:
                raise AssertionError(
                    f"migration exhausted virtual source without moving all rows: {meta['old_rows']}"
                )
            old_generation = int(meta["old_generation"])
            orphan_slots = conn.execute(
                "SELECT COUNT(*) FROM primary_slots WHERE generation=?",
                (old_generation,),
            ).fetchone()[0]
            orphan_stash = conn.execute(
                "SELECT COUNT(*) FROM primary_stash WHERE generation=?",
                (old_generation,),
            ).fetchone()[0]
            if orphan_slots or orphan_stash:
                raise AssertionError((orphan_slots, orphan_stash))
            meta["old_generation"] = None
            meta["old_capacity"] = None
            meta["old_rows"] = 0
            meta["migration_cursor"] = 0
            meta["migration_limit"] = 0

        return {
            "scanned": scanned,
            "moved": moved,
            "to_overflow": to_overflow,
            "primary_work": primary_work,
            "primary_sql_writes": primary_sql_writes,
            "overflow_writes": overflow_writes,
            "completed": completed,
        }

    def _insert_tx(
        self,
        conn: sqlite3.Connection,
        key: str,
        *,
        stop_after_route: bool = False,
    ) -> DurableInsertTrace:
        meta = self._meta_tx(conn)
        existing = self._lookup_tx(conn, meta, key)
        if existing.found:
            raise ValueError(f"duplicate key: {key}")
        duplicate_check_overflow = bool(existing.overflow_checked)

        migration_started = False
        if meta["old_generation"] is None:
            projected = (int(meta["current_rows"]) + 1) / int(meta["current_capacity"])
            if projected > float(meta["max_load"]):
                self._start_migration(meta)
                migration_started = True

        placement = self._place_primary_tx(
            conn,
            meta,
            int(meta["current_generation"]),
            int(meta["current_capacity"]),
            key,
        )
        new_key_primary_work = int(placement["work"])
        primary_sql_writes = int(placement["sql_writes"])
        overflow_writes = 0
        if bool(placement["success"]):
            path = "primary"
            meta["current_rows"] = int(meta["current_rows"]) + 1
        else:
            path = "overflow"
            conn.execute("INSERT INTO overflow(key) VALUES (?)", (key,))
            meta["overflow_rows"] = int(meta["overflow_rows"]) + 1
            overflow_writes += 1

        meta["live_size"] = int(meta["live_size"]) + 1

        if stop_after_route:
            self._write_meta_tx(conn, meta)
            return DurableInsertTrace(
                key=key,
                path=path,
                migration_started=migration_started,
                migration_completed=False,
                migration_source_slots_scanned=0,
                migration_rows_moved=0,
                migration_rows_to_overflow=0,
                new_key_primary_work=new_key_primary_work,
                migration_primary_work=0,
                primary_mutation_work=new_key_primary_work,
                primary_sql_row_writes=primary_sql_writes,
                overflow_row_writes=overflow_writes,
                metadata_row_writes=1,
                duplicate_check_overflow=duplicate_check_overflow,
                current_generation=int(meta["current_generation"]),
                old_generation=None if meta["old_generation"] is None else int(meta["old_generation"]),
                migration_cursor=int(meta["migration_cursor"]),
                live_size=int(meta["live_size"]),
                overflow_rows=int(meta["overflow_rows"]),
            )

        migration = self._migrate_step_tx(conn, meta)
        primary_sql_writes += int(migration["primary_sql_writes"])
        overflow_writes += int(migration["overflow_writes"])
        self._write_meta_tx(conn, meta)

        return DurableInsertTrace(
            key=key,
            path=path,
            migration_started=migration_started,
            migration_completed=bool(migration["completed"]),
            migration_source_slots_scanned=int(migration["scanned"]),
            migration_rows_moved=int(migration["moved"]),
            migration_rows_to_overflow=int(migration["to_overflow"]),
            new_key_primary_work=new_key_primary_work,
            migration_primary_work=int(migration["primary_work"]),
            primary_mutation_work=(
                new_key_primary_work
                + int(migration["primary_work"])
                + int(migration["scanned"])
            ),
            primary_sql_row_writes=primary_sql_writes,
            overflow_row_writes=overflow_writes,
            metadata_row_writes=1,
            duplicate_check_overflow=duplicate_check_overflow,
            current_generation=int(meta["current_generation"]),
            old_generation=None if meta["old_generation"] is None else int(meta["old_generation"]),
            migration_cursor=int(meta["migration_cursor"]),
            live_size=int(meta["live_size"]),
            overflow_rows=int(meta["overflow_rows"]),
        )

    def insert(self, key: str) -> DurableInsertTrace:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            trace = self._insert_tx(conn, key)
            conn.commit()
            return trace
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def begin_insert_without_commit(
        self, key: str, *, stage: str
    ) -> tuple[sqlite3.Connection, DurableInsertTrace]:
        if stage not in {"route", "final"}:
            raise ValueError(stage)
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            trace = self._insert_tx(conn, key, stop_after_route=(stage == "route"))
            if not conn.in_transaction:
                raise AssertionError("expected live uncommitted transaction")
            return conn, trace
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            conn.close()
            raise

    def transaction_settings(self) -> dict[str, Any]:
        with self.connect() as conn:
            return {
                "journal_mode": str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower(),
                "synchronous": int(conn.execute("PRAGMA synchronous").fetchone()[0]),
                "wal_autocheckpoint": int(conn.execute("PRAGMA wal_autocheckpoint").fetchone()[0]),
            }

    def primary_bucket_lookup_uses_index(self) -> bool:
        with self.connect() as conn:
            rows = conn.execute(
                "EXPLAIN QUERY PLAN SELECT slot,key FROM primary_slots "
                "WHERE generation=? AND bucket=?",
                (0, 0),
            ).fetchall()
            detail = " ".join(str(row["detail"]).lower() for row in rows)
            return "primary key" in detail and "scan" not in detail

    def overflow_lookup_uses_index(self) -> bool:
        with self.connect() as conn:
            rows = conn.execute(
                "EXPLAIN QUERY PLAN SELECT key FROM overflow WHERE key=?",
                ("probe",),
            ).fetchall()
            detail = " ".join(str(row["detail"]).lower() for row in rows)
            return "primary key" in detail and "scan" not in detail

    def meta_snapshot(self) -> dict[str, Any]:
        with self.connect() as conn:
            return self._meta_tx(conn)

    def all_key_locations(self) -> list[tuple[str, str]]:
        with self.connect() as conn:
            rows: list[tuple[str, str]] = []
            rows.extend(
                (str(row["key"]), f"primary:{row['generation']}")
                for row in conn.execute(
                    "SELECT generation,key FROM primary_slots ORDER BY generation,bucket,slot"
                )
            )
            rows.extend(
                (str(row["key"]), f"stash:{row['generation']}")
                for row in conn.execute(
                    "SELECT generation,key FROM primary_stash ORDER BY generation,stash_slot"
                )
            )
            rows.extend(
                (str(row["key"]), "overflow")
                for row in conn.execute("SELECT key FROM overflow ORDER BY key")
            )
            return sorted(rows)

    def audit(self) -> dict[str, Any]:
        with self.connect() as conn:
            meta = self._meta_tx(conn)
            rows = conn.execute(
                """
                SELECT key,COUNT(*) AS copies FROM (
                    SELECT key FROM primary_slots
                    UNION ALL
                    SELECT key FROM primary_stash
                    UNION ALL
                    SELECT key FROM overflow
                ) GROUP BY key ORDER BY key
                """
            ).fetchall()
            duplicate_keys = [str(row["key"]) for row in rows if int(row["copies"]) != 1]
            total_rows = sum(int(row["copies"]) for row in rows)
            primary_slots = int(conn.execute("SELECT COUNT(*) FROM primary_slots").fetchone()[0])
            primary_stash = int(conn.execute("SELECT COUNT(*) FROM primary_stash").fetchone()[0])
            overflow_rows = int(conn.execute("SELECT COUNT(*) FROM overflow").fetchone()[0])
            allowed_generations = {int(meta["current_generation"])}
            if meta["old_generation"] is not None:
                allowed_generations.add(int(meta["old_generation"]))
            actual_generations = {
                int(row[0])
                for row in conn.execute(
                    "SELECT DISTINCT generation FROM primary_slots "
                    "UNION SELECT DISTINCT generation FROM primary_stash"
                )
            }
            counts_match = (
                total_rows == int(meta["live_size"])
                and overflow_rows == int(meta["overflow_rows"])
                and primary_slots + primary_stash == int(meta["current_rows"]) + int(meta["old_rows"])
            )
            generations_valid = actual_generations.issubset(allowed_generations)
            cursor_valid = (
                0 <= int(meta["migration_cursor"]) <= int(meta["migration_limit"])
                if meta["old_generation"] is not None
                else int(meta["migration_cursor"]) == 0 and int(meta["migration_limit"]) == 0
            )
            return {
                "live_size": int(meta["live_size"]),
                "unique_keys": len(rows),
                "total_location_rows": total_rows,
                "duplicate_keys": duplicate_keys,
                "primary_slots": primary_slots,
                "primary_stash": primary_stash,
                "overflow_rows": overflow_rows,
                "counts_match": counts_match,
                "generations_valid": generations_valid,
                "cursor_valid": cursor_valid,
                "valid": not duplicate_keys and counts_match and generations_valid and cursor_valid,
            }

    def logical_snapshot(self) -> dict[str, Any]:
        return {
            "meta": self.meta_snapshot(),
            "locations": self.all_key_locations(),
            "audit": self.audit(),
        }

    def logical_digest(self) -> str:
        payload = json.dumps(self.logical_snapshot(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def recover(self) -> dict[str, Any]:
        # Opening the database causes SQLite WAL recovery before these reads. There is
        # intentionally no application redo journal in this v0.23 candidate because a
        # logical insert + migration step + metadata update is one transaction.
        audit = self.audit()
        if not audit["valid"]:
            raise AssertionError(f"durable hybrid audit failed after restart: {audit}")
        return {
            "logical_work": 0,
            "metadata_repairs": 0,
            "row_repairs": 0,
            "audit_valid": True,
            "logical_digest": self.logical_digest(),
        }

    def overflow_geometry(self) -> dict[str, int]:
        with self.connect() as conn:
            stats = btree_stats(conn, "overflow")
            return stats.to_dict()
