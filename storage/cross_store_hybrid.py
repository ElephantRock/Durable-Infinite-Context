from __future__ import annotations

import os
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from storage.fixed_page_primary import PAGE_SIZE, FixedPagePrimaryStore, PrimaryAdmissionExhausted
from storage.page_locality import btree_stats


@dataclass(frozen=True)
class CrossStoreInsertTrace:
    key: str
    path: str
    duplicate: bool
    committed_epoch: int
    primary_trace: dict[str, Any] | None
    overflow_rows_after: int
    sqlite_commits: int
    coordinator_superblock_pwrites: int
    coordinator_explicit_fsyncs: int
    pre_admission_reclaimed_tail_bytes: int
    pre_admission_deleted_future_overflow_rows: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CrossStoreLookupTrace:
    key: str
    found: bool
    path: str
    primary_total_preads: int
    primary_logical_pages: int
    overflow_checked: bool
    coordinator_extra_preads: int
    overflow_btree_height: int
    overflow_uses_primary_key: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CrossStoreRecoveryTrace:
    committed_epoch: int
    deleted_future_overflow_rows: int
    reclaimed_tail_bytes: int
    truncate_calls: int
    fixed_file_fsyncs: int
    cleanup_sqlite_commits: int
    cleanup_epoch_index_used: bool
    logical_redo: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CrossStoreHybridStore:
    """v0.25 fixed-page primary + exact SQLite overflow experiment.

    The fixed-page primary superblock epoch is the visibility coordinator. Overflow rows
    are durable SQLite rows tagged with the epoch that would make them visible. For an
    exceptional admission, SQLite commits the future-epoch row first. Only then does the
    fixed-page superblock advance to that epoch. A crash between those commits leaves a
    durable but *logically invisible* future row, which startup recovery deletes before
    the next mutation. Ordinary primary hits return before opening/querying overflow.

    The committed primary metadata also defines a safe truncation frontier:
    ``(2 + 2*next_page_id) * PAGE_SIZE``. Page ids are allocated monotonically, so bytes
    beyond that frontier after a pre-commit crash belong only to uncommitted allocation.
    Recovery may therefore truncate that stale tail without scanning the live primary.

    Recovery is intentionally metadata/index local: it does not build a logical snapshot
    or audit all membership. Full snapshots/audits belong to the experiment oracle, not
    the recovery mechanism. One in-process flag ensures startup recovery runs at most once
    before normal mutations; a new process starts unrecovered.

    This is a single-writer process-crash experiment. SQLite commits/fsync behavior is
    delegated to SQLite WAL + synchronous=FULL and is not counted as device I/O here.
    """

    def __init__(self, primary_path: str | Path, overflow_path: str | Path) -> None:
        self.primary = FixedPagePrimaryStore(primary_path)
        self.primary_path = Path(primary_path)
        self.overflow_path = Path(overflow_path)
        self._recovered = False

    def initialize(self, **primary_kwargs: Any) -> None:
        self.primary.initialize(**primary_kwargs)
        self.overflow_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.overflow_path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("PRAGMA wal_autocheckpoint=0")
            conn.execute(
                "CREATE TABLE overflow(key TEXT PRIMARY KEY, epoch INTEGER NOT NULL) WITHOUT ROWID"
            )
            conn.execute("CREATE INDEX overflow_epoch ON overflow(epoch)")
            conn.commit()
        finally:
            conn.close()
        self._recovered = True

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.overflow_path)
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        return conn

    def _read_epoch_meta(self) -> tuple[int, dict[str, Any]]:
        fd = self.primary._open()
        try:
            epoch, meta, _slot = self.primary._read_super(fd)
            return int(epoch), dict(meta)
        finally:
            os.close(fd)

    @staticmethod
    def _reachable_bytes(meta: dict[str, Any]) -> int:
        return (2 + 2 * int(meta["next_page_id"])) * PAGE_SIZE

    def overflow_uses_primary_key(self, conn: sqlite3.Connection | None = None) -> bool:
        own = conn is None
        if conn is None:
            conn = self._connect()
        try:
            rows = conn.execute(
                "EXPLAIN QUERY PLAN SELECT key FROM overflow WHERE key=? AND epoch<=?",
                ("probe", 1),
            ).fetchall()
            detail = " ".join(str(row[3]).lower() for row in rows)
            return "primary key" in detail and "scan" not in detail
        finally:
            if own:
                conn.close()

    def cleanup_uses_epoch_index(self, conn: sqlite3.Connection | None = None) -> bool:
        own = conn is None
        if conn is None:
            conn = self._connect()
        try:
            rows = conn.execute(
                "EXPLAIN QUERY PLAN SELECT key FROM overflow WHERE epoch>? LIMIT 1", (1,)
            ).fetchall()
            detail = " ".join(str(row[3]).lower() for row in rows)
            return "overflow_epoch" in detail and "scan overflow" not in detail
        finally:
            if own:
                conn.close()

    def _advance_superblock(self, expected_epoch: int, overflow_rows: int) -> int:
        fd = self.primary._open()
        try:
            observed_epoch, meta, _slot = self.primary._read_super(fd)
            if int(observed_epoch) != int(expected_epoch):
                raise RuntimeError(
                    f"coordinator epoch changed: expected {expected_epoch}, observed {observed_epoch}"
                )
            new_epoch = int(expected_epoch) + 1
            next_meta = dict(meta)
            next_meta["hybrid_overflow_rows"] = int(overflow_rows)
            self.primary._write_super(fd, int(expected_epoch), new_epoch, next_meta)
            os.fsync(fd)
            return new_epoch
        finally:
            os.close(fd)

    def recover(self) -> CrossStoreRecoveryTrace:
        epoch, meta = self._read_epoch_meta()
        conn = self._connect()
        try:
            indexed = self.cleanup_uses_epoch_index(conn)
            future_exists = conn.execute(
                "SELECT 1 FROM overflow WHERE epoch>? LIMIT 1", (epoch,)
            ).fetchone() is not None
            deleted = 0
            sqlite_commits = 0
            if future_exists:
                before_changes = conn.total_changes
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("DELETE FROM overflow WHERE epoch>?", (epoch,))
                deleted = int(conn.total_changes - before_changes)
                conn.commit()
                sqlite_commits = 1
        finally:
            conn.close()

        reachable = self._reachable_bytes(meta)
        actual = self.primary_path.stat().st_size
        reclaimed = max(0, actual - reachable)
        truncate_calls = 0
        fixed_fsyncs = 0
        if reclaimed:
            fd = os.open(self.primary_path, os.O_RDWR)
            try:
                os.ftruncate(fd, reachable)
                truncate_calls = 1
                os.fsync(fd)
                fixed_fsyncs = 1
            finally:
                os.close(fd)

        self._recovered = True
        return CrossStoreRecoveryTrace(
            committed_epoch=epoch,
            deleted_future_overflow_rows=deleted,
            reclaimed_tail_bytes=reclaimed,
            truncate_calls=truncate_calls,
            fixed_file_fsyncs=fixed_fsyncs,
            cleanup_sqlite_commits=sqlite_commits,
            cleanup_epoch_index_used=indexed,
            logical_redo=0,
        )

    def insert(
        self,
        key: str,
        failpoint: Callable[[str], None] | None = None,
        *,
        recovery_first: bool = True,
    ) -> CrossStoreInsertTrace:
        if recovery_first and not self._recovered:
            cleanup = self.recover()
        else:
            cleanup = CrossStoreRecoveryTrace(
                committed_epoch=self._read_epoch_meta()[0],
                deleted_future_overflow_rows=0,
                reclaimed_tail_bytes=0,
                truncate_calls=0,
                fixed_file_fsyncs=0,
                cleanup_sqlite_commits=0,
                cleanup_epoch_index_used=True,
                logical_redo=0,
            )

        def primary_fail(stage: str) -> None:
            if failpoint is not None:
                failpoint(f"primary_{stage}")

        try:
            primary_trace = self.primary.insert(key, failpoint=primary_fail)
            return CrossStoreInsertTrace(
                key=key,
                path="primary",
                duplicate=bool(primary_trace.duplicate),
                committed_epoch=int(primary_trace.committed_epoch),
                primary_trace=primary_trace.to_dict(),
                overflow_rows_after=int(
                    self._read_epoch_meta()[1].get("hybrid_overflow_rows", 0)
                ),
                sqlite_commits=0,
                coordinator_superblock_pwrites=0,
                coordinator_explicit_fsyncs=0,
                pre_admission_reclaimed_tail_bytes=cleanup.reclaimed_tail_bytes,
                pre_admission_deleted_future_overflow_rows=cleanup.deleted_future_overflow_rows,
            )
        except PrimaryAdmissionExhausted:
            pass

        epoch, meta = self._read_epoch_meta()
        prior_overflow_rows = int(meta.get("hybrid_overflow_rows", 0))
        conn = self._connect()
        try:
            if conn.execute(
                "SELECT 1 FROM overflow WHERE key=? AND epoch<=?", (key, epoch)
            ).fetchone() is not None:
                return CrossStoreInsertTrace(
                    key=key,
                    path="overflow",
                    duplicate=True,
                    committed_epoch=epoch,
                    primary_trace=None,
                    overflow_rows_after=prior_overflow_rows,
                    sqlite_commits=0,
                    coordinator_superblock_pwrites=0,
                    coordinator_explicit_fsyncs=0,
                    pre_admission_reclaimed_tail_bytes=cleanup.reclaimed_tail_bytes,
                    pre_admission_deleted_future_overflow_rows=cleanup.deleted_future_overflow_rows,
                )

            future_epoch = epoch + 1
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO overflow(key, epoch) VALUES (?, ?)", (key, future_epoch)
            )
            if failpoint is not None:
                failpoint("overflow_uncommitted")
            conn.commit()
            if failpoint is not None:
                failpoint("overflow_committed")
        except BaseException:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()

        visible_after = prior_overflow_rows + 1
        new_epoch = self._advance_superblock(epoch, visible_after)
        if failpoint is not None:
            failpoint("committed")
        return CrossStoreInsertTrace(
            key=key,
            path="overflow",
            duplicate=False,
            committed_epoch=new_epoch,
            primary_trace=None,
            overflow_rows_after=visible_after,
            sqlite_commits=1,
            coordinator_superblock_pwrites=1,
            coordinator_explicit_fsyncs=1,
            pre_admission_reclaimed_tail_bytes=cleanup.reclaimed_tail_bytes,
            pre_admission_deleted_future_overflow_rows=cleanup.deleted_future_overflow_rows,
        )

    def lookup(self, key: str) -> CrossStoreLookupTrace:
        primary_trace = self.primary.lookup(key)
        if primary_trace.found:
            return CrossStoreLookupTrace(
                key=key,
                found=True,
                path="primary",
                primary_total_preads=int(primary_trace.total_physical_preads),
                primary_logical_pages=int(primary_trace.logical_primary_pages),
                overflow_checked=False,
                coordinator_extra_preads=0,
                overflow_btree_height=0,
                overflow_uses_primary_key=False,
            )

        epoch, _meta = self._read_epoch_meta()
        conn = self._connect()
        try:
            height = int(btree_stats(conn, "overflow").height)
            uses_pk = self.overflow_uses_primary_key(conn)
            row = conn.execute(
                "SELECT key FROM overflow WHERE key=? AND epoch<=?", (key, epoch)
            ).fetchone()
        finally:
            conn.close()
        found = row is not None
        return CrossStoreLookupTrace(
            key=key,
            found=found,
            path="overflow" if found else "miss",
            primary_total_preads=int(primary_trace.total_physical_preads),
            primary_logical_pages=int(primary_trace.logical_primary_pages),
            overflow_checked=True,
            coordinator_extra_preads=2,
            overflow_btree_height=height,
            overflow_uses_primary_key=uses_pk,
        )

    def logical_snapshot(self) -> dict[str, Any]:
        epoch, meta = self._read_epoch_meta()
        conn = self._connect()
        try:
            overflow = [
                {"key": str(key), "epoch": int(row_epoch)}
                for key, row_epoch in conn.execute(
                    "SELECT key, epoch FROM overflow WHERE epoch<=? ORDER BY key", (epoch,)
                ).fetchall()
            ]
        finally:
            conn.close()
        return {
            "coordinator_epoch": epoch,
            "hybrid_overflow_rows": int(meta.get("hybrid_overflow_rows", 0)),
            "primary": self.primary.logical_snapshot(),
            "overflow": overflow,
        }

    def audit(self) -> dict[str, Any]:
        epoch, meta = self._read_epoch_meta()
        primary_audit = self.primary.audit()
        conn = self._connect()
        try:
            visible_keys = [
                str(row[0])
                for row in conn.execute(
                    "SELECT key FROM overflow WHERE epoch<=? ORDER BY key", (epoch,)
                ).fetchall()
            ]
            future_rows = int(
                conn.execute(
                    "SELECT COUNT(*) FROM overflow WHERE epoch>?", (epoch,)
                ).fetchone()[0]
            )
        finally:
            conn.close()
        duplicate_with_primary = any(self.primary.lookup(key).found for key in visible_keys)
        meta_rows = int(meta.get("hybrid_overflow_rows", 0))
        valid = (
            bool(primary_audit["valid"])
            and not duplicate_with_primary
            and len(visible_keys) == meta_rows
        )
        return {
            "valid": valid,
            "primary_valid": bool(primary_audit["valid"]),
            "visible_overflow_rows": len(visible_keys),
            "future_overflow_rows": future_rows,
            "metadata_overflow_rows": meta_rows,
            "duplicate_with_primary": duplicate_with_primary,
            "committed_epoch": epoch,
            "cleanup_epoch_index_used": self.cleanup_uses_epoch_index(),
        }

    def overflow_stats(self) -> dict[str, Any]:
        conn = self._connect()
        try:
            return btree_stats(conn, "overflow").to_dict()
        finally:
            conn.close()
