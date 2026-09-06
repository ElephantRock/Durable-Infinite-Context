from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable

from simulator.cascade import subject_id
from storage.multi_intent import MultiIntentStore


class _CursorAfterFetch:
    def __init__(self, cursor: Any, callback: Callable[[], None]) -> None:
        self._cursor = cursor
        self._callback = callback
        self._fired = False

    def fetchone(self):
        row = self._cursor.fetchone()
        if not self._fired:
            self._fired = True
            self._callback()
        return row

    def __getattr__(self, name: str):
        return getattr(self._cursor, name)


class _ConnectionAfterPhaseRead:
    def __init__(self, conn: Any, callback: Callable[[], None]) -> None:
        self._conn = conn
        self._callback = callback
        self._wrapped = False

    def __enter__(self):
        self._conn.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        return self._conn.__exit__(exc_type, exc, tb)

    def execute(self, sql: str, parameters=()):
        cursor = self._conn.execute(sql, parameters)
        if not self._wrapped and "SELECT * FROM maintenance_journal" in sql:
            self._wrapped = True
            return _CursorAfterFetch(cursor, self._callback)
        return cursor

    def __getattr__(self, name: str):
        return getattr(self._conn, name)


class _RaceReader(MultiIntentStore):
    def __init__(self, path: Path, callback: Callable[[], None]) -> None:
        super().__init__(path)
        self._race_callback = callback

    def connect(self):
        return _ConnectionAfterPhaseRead(super().connect(), self._race_callback)


class MultiIntentSnapshotReadTests(unittest.TestCase):
    def test_phase_and_derived_lookup_share_one_wal_snapshot(self):
        """A maintenance commit between the two reads must not expose a transient hole.

        The reader first observes PREPARED. At that exact point another connection
        commits canonical mutation and invalidation. Without an explicit read
        transaction the following derived SELECT sees the invalidated row and
        returns None, even though the context exists both before and after repair.
        With a pinned WAL snapshot it returns the coherent pre-write context.
        """

        with tempfile.TemporaryDirectory(prefix="dic-v010-read-snapshot-") as tmp:
            db = Path(tmp) / "memory.sqlite3"
            store = MultiIntentStore(db)
            store.bootstrap(16)
            index = 5
            subject = subject_id(index)
            before = store.read_context(subject)
            self.assertIsNotNone(before)

            store.enqueue_operation(
                "replace_assertion_object",
                index,
                new_value=88,
                writer="race-writer",
            )
            promoted = store.promote_next()
            self.assertIsNotNone(promoted)
            self.assertEqual(promoted["status"], "active")
            self.assertEqual(store.phase_snapshot()["phase"], "prepared")

            writer = MultiIntentStore(db)
            fired = {"value": False}

            def advance_writer() -> None:
                fired["value"] = True
                writer.apply_canonical_transaction()
                writer.invalidate_transaction()

            reader = _RaceReader(db, advance_writer)
            during = reader.read_context(subject)

            self.assertTrue(fired["value"])
            self.assertEqual(during, before)
            self.assertEqual(writer.phase_snapshot()["phase"], "invalidated")
            self.assertEqual(writer.canonical_value(index), 88)

            writer.recover()
            after = writer.read_context(subject)
            self.assertIsNotNone(after)
            self.assertIn("88", after)
            self.assertTrue(writer.materialization_matches_clean_rebuild())


if __name__ == "__main__":
    unittest.main()
