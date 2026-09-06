from __future__ import annotations

import sqlite3
from typing import Any

from state.cascade import context_node, profile_node, state_node, support_node
from storage.sqlite_recovery import (
    INVALIDATED,
    UPSERT_ASSERTION,
    PersistentRecoveryTrace,
    _json,
    _loads,
    assertion_from_dict,
)
from storage.topology_intent import PromotionRevalidatedTopologyStore


class GrowthAwareTopologyStore(PromotionRevalidatedTopologyStore):
    """v0.12 store that locally creates missing required derived materializations.

    v0.11 revalidates *which* subject/predicate region an intent can make stale, but
    the inherited affected-region walk only returns nodes that already exist. When
    canonical topology grows into a brand-new subject, there may be no profile,
    state, support, or context rows to invalidate/rebuild.

    v0.12 treats those missing outputs as explicit materialization obligations. The
    obligation set is derived directly from the canonical mutation payload, added to
    the affected region, and instantiated as invalid placeholders before the normal
    topological repair path runs. Work therefore remains proportional to the local
    output obligations rather than total memory cardinality.

    Prototype scope remains the controlled `deadline` materialization path used by
    v0.9-v0.11; arbitrary new-predicate synthesis is a separate falsification target.
    """

    @staticmethod
    def _candidate_growth_specs(
        intent: sqlite3.Row,
    ) -> list[tuple[str, str, str, str | None]]:
        if intent["operation"] != UPSERT_ASSERTION:
            return []
        payload = _loads(intent["payload_json"], {})
        previous = _loads(intent["previous_json"], {}) if intent["previous_json"] else {}
        item = assertion_from_dict(payload["assertion"])
        old_data = previous.get("assertion")
        old = assertion_from_dict(old_data) if old_data else None

        # Ordinary object-only assertion updates do not grow canonical topology and
        # must retain the narrower v0.10/v0.11 invalidation path.
        if old is not None and old.key == item.key:
            return []

        subject = item.subject_id
        predicate = item.predicate
        key = (subject, predicate, "default")
        return [
            (profile_node(subject), "profile", subject, None),
            (state_node(key), "state", subject, predicate),
            (support_node(key), "support", subject, predicate),
            (context_node(key), "context", subject, predicate),
        ]

    def _growth_specs_tx(
        self,
        conn: sqlite3.Connection,
        intent: sqlite3.Row,
    ) -> list[tuple[str, str, str, str | None]]:
        """Return only required outputs that are actually absent at invalidation."""

        missing: list[tuple[str, str, str, str | None]] = []
        for spec in self._candidate_growth_specs(intent):
            node_id = spec[0]
            row = conn.execute(
                "SELECT 1 FROM derived_nodes WHERE node_id=?",
                (node_id,),
            ).fetchone()
            if row is None:
                missing.append(spec)
        return missing

    def _affected_nodes(
        self,
        conn: sqlite3.Connection,
        intent: sqlite3.Row,
    ) -> list[str]:
        existing_region = super()._affected_nodes(conn, intent)
        obligations = [node_id for node_id, _, _, _ in self._growth_specs_tx(conn, intent)]
        return sorted(set(existing_region).union(obligations))

    def _ensure_growth_nodes_tx(
        self,
        conn: sqlite3.Connection,
        specs: list[tuple[str, str, str, str | None]],
        trace: PersistentRecoveryTrace | None,
    ) -> int:
        created = 0
        for node_id, kind, subject, predicate in specs:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO derived_nodes
                   (node_id,kind,subject_id,predicate,scope,status,value_json)
                   VALUES (?,?,?,?,?,'invalid',?)""",
                (
                    node_id,
                    kind,
                    subject,
                    predicate,
                    "default",
                    _json({"materialization_obligation": True}),
                ),
            )
            if cursor.rowcount:
                created += 1
                if trace is not None:
                    trace.derived_rows_written += 1
        return created

    def _invalidate_tx(
        self,
        conn: sqlite3.Connection,
        intent: sqlite3.Row,
        trace: PersistentRecoveryTrace | None = None,
    ) -> list[str]:
        # Capture missing obligations before inserting them; once inserted, a second
        # lookup would correctly report that nothing is missing and lose the set.
        growth_specs = self._growth_specs_tx(conn, intent)
        existing_region = super()._affected_nodes(conn, intent)
        affected = sorted(
            set(existing_region).union(node_id for node_id, _, _, _ in growth_specs)
        )
        self._ensure_growth_nodes_tx(conn, growth_specs, trace)
        conn.executemany(
            "UPDATE derived_nodes SET status='invalid' WHERE node_id=?",
            [(node_id,) for node_id in affected],
        )
        conn.execute(
            """UPDATE maintenance_journal
               SET phase=?, affected_json=?, partial_node=NULL
               WHERE intent_id=?""",
            (INVALIDATED, _json(affected), intent["intent_id"]),
        )
        if trace is not None:
            trace.affected_discovered += len(affected)
            trace.invalidated_nodes += len(affected)
            trace.journal_writes += 1
        return affected

    def subject_derived_count(self, subject: str) -> int:
        with self.connect() as conn:
            return int(
                conn.execute(
                    "SELECT COUNT(*) FROM derived_nodes WHERE subject_id=?",
                    (subject,),
                ).fetchone()[0]
            )

    def required_growth_nodes(self, subject: str, predicate: str = "deadline") -> list[str]:
        key = (subject, predicate, "default")
        return [
            profile_node(subject),
            state_node(key),
            support_node(key),
            context_node(key),
        ]
