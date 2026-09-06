from __future__ import annotations

from typing import Any

from storage.sqlite_recovery import SQLiteRecoveryStore


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
