from __future__ import annotations

from typing import Any

from storage.sqlite_recovery import (
    INVALIDATED,
    REBUILDING,
    REPAIRED,
    PersistentRecoveryTrace,
    SQLiteRecoveryStore,
    _json,
    _loads,
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
