from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from typing import Any

from core.models import Assertion
from storage.predicate_schema import PredicateSchemaAwareStore
from storage.sqlite_recovery import (
    DELETE_ASSERTION,
    UPSERT_ASSERTION,
    PersistentRecoveryTrace,
    _loads,
    assertion_from_dict,
)


@dataclass
class SubjectHeadTrace:
    head_refresh_queries: int = 0
    head_rows_read: int = 0
    head_rows_written: int = 0

    @property
    def logical_work(self) -> int:
        return self.head_refresh_queries + self.head_rows_read + self.head_rows_written

    def to_dict(self) -> dict[str, int]:
        out = asdict(self)
        out["logical_work"] = self.logical_work
        return out


class HeadIndexedPredicateStore(PredicateSchemaAwareStore):
    """v0.14 store with an explicit current assertion head per subject/predicate.

    v0.13 correctly makes ``profile:<subject>`` subject-wide, but reconstructing it
    scans every historical assertion owned by the subject before choosing the latest
    row per predicate. That makes current-profile repair O(P*H) for P live predicates
    and H historical versions per predicate.

    v0.14 materializes only the current assertion ID for each (subject,predicate).
    The head table is updated atomically with canonical assertion mutation. Profile
    rebuild therefore reads O(P) heads/current assertions and remains independent of
    irrelevant history depth H. O(P) work is still expected because the profile
    itself contains all P live predicates/evidence payloads.
    """

    def __init__(self, path: str):
        super().__init__(path)
        self._subject_head_trace = SubjectHeadTrace()

    def initialize(self) -> None:
        super().initialize()
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS subject_predicate_heads(
                    subject_id TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    assertion_id TEXT NOT NULL,
                    PRIMARY KEY(subject_id,predicate)
                );
                CREATE INDEX IF NOT EXISTS idx_subject_predicate_heads_subject
                    ON subject_predicate_heads(subject_id,predicate,assertion_id);
                """
            )

    def bootstrap(self, entity_count: int) -> None:
        super().bootstrap(entity_count)
        self.rebuild_head_index()
        self._subject_head_trace = SubjectHeadTrace()

    def rebuild_head_index(self) -> None:
        """Bootstrap/recovery oracle path; excluded from measured local maintenance."""

        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM subject_predicate_heads")
            seen: set[tuple[str, str]] = set()
            for row in conn.execute(
                """SELECT id,subject_id,predicate FROM assertions
                   ORDER BY subject_id,predicate,recorded_seq DESC,id DESC"""
            ):
                key = (str(row["subject_id"]), str(row["predicate"]))
                if key in seen:
                    continue
                seen.add(key)
                conn.execute(
                    """INSERT INTO subject_predicate_heads(subject_id,predicate,assertion_id)
                       VALUES (?,?,?)""",
                    (key[0], key[1], row["id"]),
                )
            conn.commit()

    def _refresh_head_tx(
        self,
        conn: sqlite3.Connection,
        subject: str,
        predicate: str,
    ) -> None:
        self._subject_head_trace.head_refresh_queries += 1
        row = conn.execute(
            """SELECT id FROM assertions INDEXED BY idx_assertions_subject_predicate
               WHERE subject_id=? AND predicate=?
               ORDER BY recorded_seq DESC,id DESC LIMIT 1""",
            (subject, predicate),
        ).fetchone()
        if row is None:
            cursor = conn.execute(
                "DELETE FROM subject_predicate_heads WHERE subject_id=? AND predicate=?",
                (subject, predicate),
            )
            self._subject_head_trace.head_rows_written += max(cursor.rowcount, 0)
            return

        self._subject_head_trace.head_rows_read += 1
        conn.execute(
            """INSERT INTO subject_predicate_heads(subject_id,predicate,assertion_id)
               VALUES (?,?,?)
               ON CONFLICT(subject_id,predicate) DO UPDATE SET assertion_id=excluded.assertion_id""",
            (subject, predicate, row["id"]),
        )
        self._subject_head_trace.head_rows_written += 1

    def _apply_canonical_tx(
        self,
        conn: sqlite3.Connection,
        intent: sqlite3.Row,
        trace: PersistentRecoveryTrace | None = None,
    ) -> None:
        operation = str(intent["operation"])
        payload = _loads(intent["payload_json"], {})
        previous = _loads(intent["previous_json"], {}) if intent["previous_json"] else {}
        keys: set[tuple[str, str]] = set()

        if operation == UPSERT_ASSERTION:
            item = assertion_from_dict(payload["assertion"])
            keys.add((item.subject_id, item.predicate))
            old_data = previous.get("assertion")
            if old_data is not None:
                old = assertion_from_dict(old_data)
                keys.add((old.subject_id, old.predicate))
        elif operation == DELETE_ASSERTION:
            old = assertion_from_dict(previous["assertion"])
            keys.add((old.subject_id, old.predicate))

        super()._apply_canonical_tx(conn, intent, trace)
        for subject, predicate in sorted(keys):
            self._refresh_head_tx(conn, subject, predicate)

    def _latest_subject_assertions_tx(
        self,
        conn: sqlite3.Connection,
        subject: str,
        trace: PersistentRecoveryTrace | None = None,
    ) -> list[Assertion]:
        assertions: list[Assertion] = []
        heads = conn.execute(
            """SELECT predicate,assertion_id
               FROM subject_predicate_heads INDEXED BY idx_subject_predicate_heads_subject
               WHERE subject_id=? ORDER BY predicate""",
            (subject,),
        )
        for head in heads:
            self._subject_head_trace.head_rows_read += 1
            row = conn.execute(
                "SELECT * FROM assertions WHERE id=?",
                (head["assertion_id"],),
            ).fetchone()
            if row is None:
                raise AssertionError(
                    f"head points to missing assertion: {subject}/{head['predicate']}"
                )
            if trace is not None:
                trace.canonical_rows_read += 1
            evidence_ids = [
                r["evidence_id"]
                for r in conn.execute(
                    "SELECT evidence_id FROM assertion_evidence WHERE assertion_id=? ORDER BY evidence_id",
                    (row["id"],),
                )
            ]
            if trace is not None:
                trace.canonical_rows_read += len(evidence_ids)
            assertions.append(self._assertion_row_to_model(row, evidence_ids))
        return assertions

    def recover(self) -> PersistentRecoveryTrace:
        self._subject_head_trace = SubjectHeadTrace()
        return super().recover()

    def subject_head_trace(self) -> dict[str, int]:
        return self._subject_head_trace.to_dict()

    def head_lookup_uses_index(self, subject: str | None = None) -> bool:
        probe = subject or "cascade-subject-0000000"
        with self.connect() as conn:
            rows = conn.execute(
                """EXPLAIN QUERY PLAN
                   SELECT predicate,assertion_id
                   FROM subject_predicate_heads INDEXED BY idx_subject_predicate_heads_subject
                   WHERE subject_id=? ORDER BY predicate""",
                (probe,),
            ).fetchall()
            detail = " ".join(str(row["detail"]) for row in rows).lower()
            return (
                "idx_subject_predicate_heads_subject" in detail
                and "search subject_predicate_heads" in detail
                and "scan subject_predicate_heads" not in detail
            )

    def head_refresh_uses_index(self, subject: str | None = None, predicate: str = "deadline") -> bool:
        probe = subject or "cascade-subject-0000000"
        with self.connect() as conn:
            rows = conn.execute(
                """EXPLAIN QUERY PLAN
                   SELECT id FROM assertions INDEXED BY idx_assertions_subject_predicate
                   WHERE subject_id=? AND predicate=?
                   ORDER BY recorded_seq DESC,id DESC LIMIT 1""",
                (probe, predicate),
            ).fetchall()
            detail = " ".join(str(row["detail"]) for row in rows).lower()
            return (
                "idx_assertions_subject_predicate" in detail
                and "search assertions" in detail
                and "scan assertions" not in detail
            )

    def head_snapshot(self, subject: str) -> dict[str, str]:
        with self.connect() as conn:
            return {
                str(row["predicate"]): str(row["assertion_id"])
                for row in conn.execute(
                    """SELECT predicate,assertion_id FROM subject_predicate_heads
                       WHERE subject_id=? ORDER BY predicate""",
                    (subject,),
                )
            }

    def head_index_matches_canonical(self) -> bool:
        expected: dict[tuple[str, str], str] = {}
        with self.connect() as conn:
            for row in conn.execute(
                """SELECT id,subject_id,predicate FROM assertions
                   ORDER BY subject_id,predicate,recorded_seq DESC,id DESC"""
            ):
                key = (str(row["subject_id"]), str(row["predicate"]))
                expected.setdefault(key, str(row["id"]))
            observed = {
                (str(row["subject_id"]), str(row["predicate"])): str(row["assertion_id"])
                for row in conn.execute(
                    "SELECT subject_id,predicate,assertion_id FROM subject_predicate_heads"
                )
            }
        return observed == expected
