from __future__ import annotations

import sqlite3
from typing import Any

from simulator.cascade import assertion_id, evidence_id, subject_id
from storage.multi_intent import ACTIVE, CONFLICT, QUEUED, MultiIntentStore
from storage.sqlite_recovery import PREPARED, UPSERT_ASSERTION, _json, _loads, assertion_to_dict


class TopologyMutationStore(MultiIntentStore):
    """Control store that adds topology mutation but keeps v0.10 promotion semantics.

    This class deliberately inherits admission-time read-key capture unchanged. It is
    useful as the falsification control for v0.11: a predecessor can move an
    assertion after a later evidence intent has already captured its affected key.
    """

    def enqueue_topology_move(
        self,
        index: int,
        target_index: int,
        *,
        writer: str | None = None,
    ) -> dict[str, Any]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            aid = assertion_id(index)
            item = self._get_assertion(conn, aid)
            if item is None:
                raise KeyError(aid)
            replacement = assertion_to_dict(item)
            replacement["subject_id"] = subject_id(target_index)
            result = self._enqueue_tx(
                conn,
                UPSERT_ASSERTION,
                {"assertion": replacement},
                {"assertion": assertion_to_dict(item)},
                writer=writer,
            )
            conn.commit()
            return {
                **result,
                "source_subject": item.subject_id,
                "target_subject": subject_id(target_index),
            }
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()


class PromotionRevalidatedTopologyStore(TopologyMutationStore):
    """v0.11 intent store with promotion-time derived-impact revalidation.

    Canonical conflict preconditions remain admission-time durable facts. Derived
    read protection is different: it depends on the dependency topology left by all
    earlier committed intents, so it is recomputed inside the same transaction that
    promotes an intent into the active maintenance journal.
    """

    def _revalidated_read_keys_tx(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> list[str]:
        payload = _loads(row["payload_json"], {})
        previous = _loads(row["previous_json"], None) if row["previous_json"] else None
        return self._queue_read_keys(conn, row["operation"], payload, previous)

    def promote_next(self) -> dict[str, Any] | None:
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

            admission_read_keys = list(_loads(row["read_keys_json"], []))
            read_keys = self._revalidated_read_keys_tx(conn, row)
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
                """UPDATE intent_queue
                   SET status=?, read_keys_json=? WHERE intent_id=?""",
                (ACTIVE, _json(read_keys), row["intent_id"]),
            )
            conn.commit()
            return {
                "seq": int(row["seq"]),
                "intent_id": row["intent_id"],
                "status": ACTIVE,
                "write_key": row["write_key"],
                "base_version": int(row["base_version"]),
                "current_version": current_version,
                "admission_read_keys": admission_read_keys,
                "read_keys": read_keys,
                "read_keys_revalidated": admission_read_keys != read_keys,
            }
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def evidence_read_key_lookup_uses_index(self, index: int) -> bool:
        with self.connect() as conn:
            rows = conn.execute(
                """EXPLAIN QUERY PLAN
                   SELECT DISTINCT a.subject_id, a.predicate
                   FROM assertion_evidence AS ae
                   JOIN assertions AS a ON a.id=ae.assertion_id
                   WHERE ae.evidence_id=?""",
                (evidence_id(index),),
            ).fetchall()
            detail = " ".join(str(row["detail"]) for row in rows).lower()
            return (
                "idx_assertion_evidence_evidence" in detail
                and "search a" in detail
                and "scan ae" not in detail
            )
