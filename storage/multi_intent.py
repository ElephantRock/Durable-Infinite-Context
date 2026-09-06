from __future__ import annotations

import sqlite3
import time
import uuid
from typing import Any

from simulator.cascade import alias, assertion_id, evidence_id
from state.cascade import context_node
from storage.process_store import PersistentProcessStore
from storage.sqlite_recovery import (
    CANONICAL_APPLIED,
    DELETE_ASSERTION,
    INVALIDATED,
    PREPARED,
    REBUILDING,
    REPAIRED,
    UPSERT_ASSERTION,
    UPSERT_EVIDENCE,
    PersistentRecoveryTrace,
    _json,
    _loads,
    assertion_from_dict,
    assertion_to_dict,
)


QUEUED = "queued"
ACTIVE = "active"
DONE = "done"
CONFLICT = "conflict"


def _read_key(subject_id: str, predicate: str) -> str:
    return f"{subject_id}|{predicate}"


class MultiIntentStore(PersistentProcessStore):
    """v0.10 durable intent queue layered over v0.9 single-flight recovery.

    SQLite remains a single physical writer. Multiple processes may durably admit
    logical intents concurrently; the queue gives those intents an explicit total
    order. A queued intent carries the canonical version it observed at admission.
    Promotion is conditional on that version still matching, so same-record writes
    admitted from the same base do not silently overwrite one another.
    """

    def initialize(self) -> None:
        super().initialize()
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS canonical_versions(
                    write_key TEXT PRIMARY KEY,
                    version INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS intent_queue(
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    intent_id TEXT NOT NULL UNIQUE,
                    operation TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    previous_json TEXT,
                    write_key TEXT NOT NULL,
                    base_version INTEGER NOT NULL,
                    read_keys_json TEXT NOT NULL,
                    writer TEXT,
                    conflict_reason TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_intent_queue_status_seq
                    ON intent_queue(status, seq);
                CREATE INDEX IF NOT EXISTS idx_intent_queue_write_key_seq
                    ON intent_queue(write_key, seq);

                CREATE TRIGGER IF NOT EXISTS trg_multi_intent_finalize
                AFTER DELETE ON maintenance_journal
                BEGIN
                    UPDATE intent_queue
                    SET status='done'
                    WHERE intent_id=OLD.intent_id AND status='active';
                END;
                """
            )

    def prepare(self, *args: Any, **kwargs: Any) -> str:
        """Reject the legacy v0.9 direct journal entry point.

        Optimistic versions are complete only when every v0.10 canonical mutation
        is admitted through intent_queue. Keeping the inherited prepare() callable
        would silently create an unversioned active journal.
        """

        raise RuntimeError("v0.10 mutations must be admitted with enqueue_operation()")

    @staticmethod
    def _queue_write_key(operation: str, payload: dict[str, Any]) -> str:
        if operation == UPSERT_EVIDENCE:
            return f"evidence:{payload['evidence']['id']}"
        if operation == UPSERT_ASSERTION:
            return f"assertion:{payload['assertion']['id']}"
        if operation == DELETE_ASSERTION:
            return f"assertion:{payload['assertion_id']}"
        raise ValueError(operation)

    def _queue_read_keys(
        self,
        conn: sqlite3.Connection,
        operation: str,
        payload: dict[str, Any],
        previous: dict[str, Any] | None,
    ) -> list[str]:
        keys: set[str] = set()
        if operation == UPSERT_EVIDENCE:
            eid = payload["evidence"]["id"]
            rows = conn.execute(
                """SELECT DISTINCT a.subject_id, a.predicate
                   FROM assertion_evidence AS ae
                   JOIN assertions AS a ON a.id=ae.assertion_id
                   WHERE ae.evidence_id=?""",
                (eid,),
            )
            keys.update(_read_key(row["subject_id"], row["predicate"]) for row in rows)
        elif operation == UPSERT_ASSERTION:
            item = assertion_from_dict(payload["assertion"])
            keys.add(_read_key(item.subject_id, item.predicate))
            if previous and previous.get("assertion"):
                old = assertion_from_dict(previous["assertion"])
                keys.add(_read_key(old.subject_id, old.predicate))
        elif operation == DELETE_ASSERTION:
            if previous is None or not previous.get("assertion"):
                raise ValueError("delete intent requires previous assertion")
            old = assertion_from_dict(previous["assertion"])
            keys.add(_read_key(old.subject_id, old.predicate))
        else:
            raise ValueError(operation)
        return sorted(keys)

    @staticmethod
    def _version_tx(conn: sqlite3.Connection, write_key: str) -> int:
        conn.execute(
            "INSERT OR IGNORE INTO canonical_versions(write_key,version) VALUES (?,0)",
            (write_key,),
        )
        row = conn.execute(
            "SELECT version FROM canonical_versions WHERE write_key=?",
            (write_key,),
        ).fetchone()
        if row is None:
            raise AssertionError(write_key)
        return int(row["version"])

    def _enqueue_tx(
        self,
        conn: sqlite3.Connection,
        operation: str,
        payload: dict[str, Any],
        previous: dict[str, Any] | None,
        *,
        writer: str | None,
    ) -> dict[str, Any]:
        write_key = self._queue_write_key(operation, payload)
        read_keys = self._queue_read_keys(conn, operation, payload, previous)
        base_version = self._version_tx(conn, write_key)
        intent_id = f"intent-{uuid.uuid4().hex}"
        cursor = conn.execute(
            """INSERT INTO intent_queue
               (intent_id,operation,status,payload_json,previous_json,write_key,
                base_version,read_keys_json,writer,conflict_reason)
               VALUES (?,?,?,?,?,?,?,?,?,NULL)""",
            (
                intent_id,
                operation,
                QUEUED,
                _json(payload),
                None if previous is None else _json(previous),
                write_key,
                base_version,
                _json(read_keys),
                writer,
            ),
        )
        return {
            "seq": int(cursor.lastrowid),
            "intent_id": intent_id,
            "status": QUEUED,
            "write_key": write_key,
            "base_version": base_version,
            "read_keys": read_keys,
            "writer": writer,
        }

    def enqueue_operation(
        self,
        operation: str,
        index: int,
        *,
        new_value: int = 77,
        writer: str | None = None,
        hold_ms: int = 0,
    ) -> dict[str, Any]:
        """Durably admit one logical mutation without applying canonical state."""

        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            if operation == "replace_evidence_payload":
                eid = evidence_id(index)
                row = conn.execute("SELECT * FROM evidence WHERE id=?", (eid,)).fetchone()
                if row is None:
                    raise KeyError(eid)
                previous = {"evidence": dict(row)}
                replacement = dict(row)
                replacement["payload"] = (
                    f"{alias(index, 'Nova')} finance migration deadline is day 42."
                )
                result = self._enqueue_tx(
                    conn,
                    UPSERT_EVIDENCE,
                    {"evidence": replacement},
                    previous,
                    writer=writer,
                )
            elif operation == "replace_assertion_object":
                aid = assertion_id(index)
                item = self._get_assertion(conn, aid)
                if item is None:
                    raise KeyError(aid)
                replacement = assertion_to_dict(item)
                replacement["object_value"] = new_value
                result = self._enqueue_tx(
                    conn,
                    UPSERT_ASSERTION,
                    {"assertion": replacement},
                    {"assertion": assertion_to_dict(item)},
                    writer=writer,
                )
            elif operation == "delete_assertion":
                aid = assertion_id(index)
                item = self._get_assertion(conn, aid)
                if item is None:
                    raise KeyError(aid)
                result = self._enqueue_tx(
                    conn,
                    DELETE_ASSERTION,
                    {"assertion_id": aid},
                    {"assertion": assertion_to_dict(item)},
                    writer=writer,
                )
            else:
                raise ValueError(f"unsupported operation: {operation}")

            if hold_ms > 0:
                time.sleep(hold_ms / 1000.0)
            conn.commit()
            return result
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def _apply_canonical_tx(
        self,
        conn: sqlite3.Connection,
        intent: sqlite3.Row,
        trace: PersistentRecoveryTrace | None = None,
    ) -> None:
        """Apply canonical state and advance its optimistic version atomically."""

        queued = conn.execute(
            "SELECT write_key,status FROM intent_queue WHERE intent_id=?",
            (intent["intent_id"],),
        ).fetchone()
        if queued is None or queued["status"] != ACTIVE:
            raise RuntimeError(
                "v0.10 canonical apply requires an ACTIVE queue-backed intent"
            )
        super()._apply_canonical_tx(conn, intent, trace)
        self._version_tx(conn, queued["write_key"])
        conn.execute(
            "UPDATE canonical_versions SET version=version+1 WHERE write_key=?",
            (queued["write_key"],),
        )

    def promote_next(self) -> dict[str, Any] | None:
        """Promote the oldest valid queued intent into v0.9's active journal."""

        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            active = conn.execute("SELECT intent_id FROM maintenance_journal LIMIT 1").fetchone()
            if active is not None:
                conn.commit()
                return {"status": "busy", "intent_id": active["intent_id"]}

            row = conn.execute(
                """SELECT * FROM intent_queue INDEXED BY idx_intent_queue_status_seq
                   WHERE status=? ORDER BY seq LIMIT 1""",
                (QUEUED,),
            ).fetchone()
            if row is None:
                conn.commit()
                return None

            current_version = self._version_tx(conn, row["write_key"])
            if current_version != int(row["base_version"]):
                reason = (
                    f"base_version={row['base_version']} current_version={current_version}"
                )
                conn.execute(
                    """UPDATE intent_queue
                       SET status=?, conflict_reason=? WHERE intent_id=?""",
                    (CONFLICT, reason, row["intent_id"]),
                )
                conn.commit()
                return {
                    "seq": int(row["seq"]),
                    "intent_id": row["intent_id"],
                    "status": CONFLICT,
                    "write_key": row["write_key"],
                    "base_version": int(row["base_version"]),
                    "current_version": current_version,
                    "conflict_reason": reason,
                }

            conn.execute(
                """INSERT INTO maintenance_journal
                   (intent_id,operation,phase,payload_json,previous_json,affected_json,partial_node)
                   VALUES (?,?,?,?,?,?,NULL)""",
                (
                    row["intent_id"],
                    row["operation"],
                    PREPARED,
                    row["payload_json"],
                    row["previous_json"],
                    "[]",
                ),
            )
            conn.execute(
                "UPDATE intent_queue SET status=? WHERE intent_id=?",
                (ACTIVE, row["intent_id"]),
            )
            conn.commit()
            return {
                "seq": int(row["seq"]),
                "intent_id": row["intent_id"],
                "status": ACTIVE,
                "write_key": row["write_key"],
                "base_version": int(row["base_version"]),
                "current_version": current_version,
            }
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _accumulate_trace(total: PersistentRecoveryTrace, part: PersistentRecoveryTrace) -> None:
        for field in PersistentRecoveryTrace.__dataclass_fields__:
            setattr(total, field, getattr(total, field) + getattr(part, field))

    def drain_all(self) -> dict[str, Any]:
        """Recover any active intent, then deterministically drain queued intents."""

        trace = PersistentRecoveryTrace()
        promotions = 0
        conflicts = 0
        recovery_rounds = 0

        while True:
            with self.connect() as conn:
                active = conn.execute(
                    "SELECT intent_id FROM maintenance_journal LIMIT 1"
                ).fetchone()
            if active is not None:
                part = self.recover()
                self._accumulate_trace(trace, part)
                recovery_rounds += 1
                continue

            promoted = self.promote_next()
            if promoted is None:
                break
            if promoted["status"] == CONFLICT:
                conflicts += 1
                continue
            if promoted["status"] == ACTIVE:
                promotions += 1
                continue
            if promoted["status"] == "busy":
                continue
            raise AssertionError(promoted)

        return {
            "promotions": promotions,
            "conflicts": conflicts,
            "recovery_rounds": recovery_rounds,
            "trace": trace.to_dict(),
            "queue_counts": self.queue_counts(),
        }

    def read_context(self, subject: str, predicate: str = "deadline") -> str | None:
        """Block only reads whose derived target can be stale in the active phase."""

        with self.connect() as conn:
            intent = conn.execute(
                "SELECT * FROM maintenance_journal ORDER BY intent_id LIMIT 1"
            ).fetchone()
            if intent is not None and intent["phase"] in {
                CANONICAL_APPLIED,
                INVALIDATED,
                REBUILDING,
            }:
                queued = conn.execute(
                    "SELECT read_keys_json FROM intent_queue WHERE intent_id=?",
                    (intent["intent_id"],),
                ).fetchone()
                target = _read_key(subject, predicate)
                if queued is None or target in set(_loads(queued["read_keys_json"], [])):
                    raise RuntimeError(
                        "active maintenance can make this derived read stale"
                    )

            row = conn.execute(
                """SELECT value_json FROM derived_nodes
                   WHERE node_id=? AND status='fresh'""",
                (context_node((subject, predicate, "default")),),
            ).fetchone()
            if row is None:
                return None
            return _loads(row["value_json"], {}).get("text")

    def queue_counts(self) -> dict[str, int]:
        with self.connect() as conn:
            counts = {QUEUED: 0, ACTIVE: 0, DONE: 0, CONFLICT: 0}
            for row in conn.execute(
                "SELECT status,COUNT(*) AS n FROM intent_queue GROUP BY status"
            ):
                counts[row["status"]] = int(row["n"])
            return counts

    def queue_snapshot(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [
                {
                    "seq": int(row["seq"]),
                    "intent_id": row["intent_id"],
                    "operation": row["operation"],
                    "status": row["status"],
                    "write_key": row["write_key"],
                    "base_version": int(row["base_version"]),
                    "read_keys": _loads(row["read_keys_json"], []),
                    "writer": row["writer"],
                    "conflict_reason": row["conflict_reason"],
                    "payload": _loads(row["payload_json"], {}),
                }
                for row in conn.execute("SELECT * FROM intent_queue ORDER BY seq")
            ]

    def canonical_version(self, write_key: str) -> int:
        with self.connect() as conn:
            return self._version_tx(conn, write_key)

    def queue_lookup_uses_index(self) -> bool:
        with self.connect() as conn:
            rows = conn.execute(
                """EXPLAIN QUERY PLAN
                   SELECT seq FROM intent_queue INDEXED BY idx_intent_queue_status_seq
                   WHERE status=? ORDER BY seq LIMIT 1""",
                (QUEUED,),
            ).fetchall()
            detail = " ".join(str(row["detail"]) for row in rows).lower()
            return "idx_intent_queue_status_seq" in detail

    def queue_schema_snapshot(self) -> dict[str, Any]:
        return {
            "counts": self.queue_counts(),
            "rows": self.queue_snapshot(),
            "queue_lookup_uses_index": self.queue_lookup_uses_index(),
        }
