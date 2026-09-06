from __future__ import annotations

import sqlite3
from typing import Any

from state.cascade import evidence_node, profile_node, state_node
from storage.sqlite_recovery import (
    DELETE_ASSERTION,
    INVALIDATED,
    REBUILDING,
    REPAIRED,
    UPSERT_ASSERTION,
    UPSERT_EVIDENCE,
    PersistentRecoveryTrace,
    SQLiteRecoveryStore,
    _json,
    _loads,
    assertion_from_dict,
)


class PersistentProcessStore(SQLiteRecoveryStore):
    """v0.9 process-crash store with canonical JSON/text representation."""

    @staticmethod
    def _context_value(subject: str, predicate: str, support: dict[str, Any]) -> dict[str, Any]:
        # JSON persistence turns tuples into lists. Normalize back to stable pairs
        # before formatting so bootstrap and post-restart reconstruction are byte
        # identical rather than merely semantically equivalent.
        evidence_payloads = [tuple(pair) for pair in support["evidence_payloads"]]
        assertion_ids = list(support["assertion_ids"])
        operative_values = list(support["operative_values"])
        text = (
            f"ENTITY={subject} PROPERTY={predicate} STATUS={support['status']} "
            f"VALUES={operative_values!r} "
            f"ASSERTIONS={assertion_ids!r} "
            f"EVIDENCE={evidence_payloads!r}"
        )
        return {"text": text}

    def full_rebuild_work(self) -> int:
        """Logical full-reconstruction control, excluded from recovery cost."""

        with self.connect() as conn:
            tables = (
                "evidence",
                "assertions",
                "assertion_evidence",
                "derived_nodes",
                "dependency_edges",
            )
            return sum(
                int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in tables
            )

    def _affected_nodes(
        self,
        conn: sqlite3.Connection,
        intent: sqlite3.Row,
    ) -> list[str]:
        """Discover the affected region with an explicitly indexed recursive walk."""

        operation = intent["operation"]
        payload = _loads(intent["payload_json"], {})
        previous = _loads(intent["previous_json"], {}) if intent["previous_json"] else {}
        seeds: list[str] = []
        canonical_seeds: list[str] = []

        if operation == UPSERT_EVIDENCE:
            canonical_seeds.append(evidence_node(payload["evidence"]["id"]))
        elif operation == UPSERT_ASSERTION:
            item = assertion_from_dict(payload["assertion"])
            old_data = previous.get("assertion")
            old = assertion_from_dict(old_data) if old_data else None
            keys = {item.key}
            if old is not None:
                keys.add(old.key)
            seeds.extend(state_node(key) for key in sorted(keys))
            profile_changed = (
                old is None
                or old.subject_id != item.subject_id
                or old.predicate != item.predicate
                or old.evidence_ids != item.evidence_ids
            )
            if profile_changed:
                subjects = {item.subject_id}
                if old is not None:
                    subjects.add(old.subject_id)
                seeds.extend(profile_node(subject_id) for subject_id in sorted(subjects))
        elif operation == DELETE_ASSERTION:
            old = assertion_from_dict(previous["assertion"])
            seeds.extend([state_node(old.key), profile_node(old.subject_id)])
        else:
            raise ValueError(operation)

        all_seeds = tuple(dict.fromkeys([*seeds, *canonical_seeds]))
        if not all_seeds:
            return []
        placeholders = ",".join("?" for _ in all_seeds)
        derived_placeholders = ",".join("?" for _ in seeds) if seeds else "NULL"
        sql = f"""
            WITH RECURSIVE affected(node_id) AS (
                SELECT node_id FROM derived_nodes
                WHERE node_id IN ({derived_placeholders})
                UNION
                SELECT derived_node
                FROM dependency_edges INDEXED BY idx_dependency_source
                WHERE source_node IN ({placeholders})
                UNION
                SELECT e.derived_node
                FROM dependency_edges AS e INDEXED BY idx_dependency_source
                JOIN affected AS a ON e.source_node=a.node_id
            )
            SELECT DISTINCT node_id FROM affected ORDER BY node_id
        """
        params = [*seeds, *all_seeds] if seeds else [*all_seeds]
        return [row["node_id"] for row in conn.execute(sql, params)]

    def affected_traversal_uses_index(self) -> bool:
        """Verify the actual recursive affected-region plan uses the source index."""

        with self.connect() as conn:
            rows = conn.execute(
                """
                EXPLAIN QUERY PLAN
                WITH RECURSIVE affected(node_id) AS (
                    SELECT node_id FROM derived_nodes WHERE node_id IN (?)
                    UNION
                    SELECT derived_node
                    FROM dependency_edges INDEXED BY idx_dependency_source
                    WHERE source_node IN (?,?)
                    UNION
                    SELECT e.derived_node
                    FROM dependency_edges AS e INDEXED BY idx_dependency_source
                    JOIN affected AS a ON e.source_node=a.node_id
                )
                SELECT DISTINCT node_id FROM affected ORDER BY node_id
                """,
                ("state:probe:deadline:default", "canonical:probe", "state:probe:deadline:default"),
            ).fetchall()
            detail = " ".join(str(row["detail"]) for row in rows).lower()
            return detail.count("idx_dependency_source") >= 2

    def begin_partial_rebuild_without_commit(self):
        """Leave the partial-derived-write transaction open for SIGKILL testing."""

        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            intent = self._intent(conn)
            if intent is None or intent["phase"] != INVALIDATED:
                raise RuntimeError("INVALIDATED intent required")
            affected = _loads(intent["affected_json"], [])
            if not affected:
                raise RuntimeError("no affected node for partial rebuild")
            priorities = {"profile": 0, "state": 0, "support": 1, "context": 2}
            rows = []
            for node_id in affected:
                row = conn.execute(
                    "SELECT node_id,kind FROM derived_nodes WHERE node_id=?",
                    (node_id,),
                ).fetchone()
                if row is not None:
                    rows.append(row)
            rows.sort(key=lambda row: (priorities.get(row["kind"], 99), row["node_id"]))
            if not rows:
                raise RuntimeError("affected nodes disappeared before partial rebuild")
            node_id = rows[0]["node_id"]
            conn.execute(
                "UPDATE derived_nodes SET status='rebuilding', value_json=? WHERE node_id=?",
                (_json({"partial": True}), node_id),
            )
            conn.execute(
                "UPDATE maintenance_journal SET phase=?, partial_node=? WHERE intent_id=?",
                (REBUILDING, node_id, intent["intent_id"]),
            )
            return conn
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            conn.close()
            raise

    def begin_repair_without_commit(self):
        """Leave local repair + REPAIRED phase advance uncommitted at process death."""

        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            intent = self._intent(conn)
            if intent is None or intent["phase"] not in {INVALIDATED, REBUILDING}:
                raise RuntimeError("INVALIDATED or REBUILDING intent required")
            self._repair_tx(conn, intent, PersistentRecoveryTrace())
            return conn
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            conn.close()
            raise

    def begin_finalize_without_commit(self):
        """Delete the repaired intent in an open transaction, then allow SIGKILL."""

        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            intent = self._intent(conn)
            if intent is None or intent["phase"] != REPAIRED:
                raise RuntimeError("REPAIRED intent required")
            conn.execute(
                "DELETE FROM maintenance_journal WHERE intent_id=?",
                (intent["intent_id"],),
            )
            return conn
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            conn.close()
            raise

    def phase_snapshot(self) -> dict[str, Any]:
        """Expose transaction-boundary lifecycle counts for the crash oracle."""

        with self.connect() as conn:
            intent = self._intent(conn)
            return {
                "phase": None if intent is None else intent["phase"],
                "journal_rows": int(
                    conn.execute("SELECT COUNT(*) FROM maintenance_journal").fetchone()[0]
                ),
                "invalid_nodes": int(
                    conn.execute(
                        "SELECT COUNT(*) FROM derived_nodes WHERE status!='fresh'"
                    ).fetchone()[0]
                ),
                "rebuilding_nodes": int(
                    conn.execute(
                        "SELECT COUNT(*) FROM derived_nodes WHERE status='rebuilding'"
                    ).fetchone()[0]
                ),
            }
