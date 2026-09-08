from __future__ import annotations

import os
from typing import Callable

from storage.cross_store_hybrid import CrossStoreHybridStore, CrossStoreRecoveryTrace


class InterruptibleCrossStoreHybridStore(CrossStoreHybridStore):
    """v0.26 recovery candidate with explicit process-crash failpoints.

    The cleanup algorithm matches the v0.25 recovery order, but exposes boundaries so a
    subprocess can be SIGKILLed while cleanup is in flight. This remains a single-writer
    process-crash experiment; it does not establish hardware power-loss durability.
    """

    def recover(
        self,
        failpoint: Callable[[str], None] | None = None,
    ) -> CrossStoreRecoveryTrace:
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
                if failpoint is not None:
                    failpoint("future_delete_uncommitted")
                conn.commit()
                sqlite_commits = 1
                if failpoint is not None:
                    failpoint("future_delete_committed")
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
                if failpoint is not None:
                    failpoint("tail_truncated")
                os.fsync(fd)
                fixed_fsyncs = 1
                if failpoint is not None:
                    failpoint("tail_synced")
            finally:
                os.close(fd)

        self._recovered = True
        if failpoint is not None:
            failpoint("recovery_complete")
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
