from __future__ import annotations

import hashlib
import sqlite3
from collections import defaultdict
from typing import Any

from core.models import Assertion, EvidenceRecord
from simulator.cascade import alias, assertion_id, subject_id
from state.cascade import context_node, evidence_node, profile_node, state_node, support_node
from storage.growth_intent import GrowthAwareTopologyStore
from storage.sqlite_recovery import (
    UPSERT_ASSERTION,
    UPSERT_EVIDENCE,
    PersistentRecoveryTrace,
    _json,
    _loads,
    assertion_to_dict,
    evidence_to_dict,
)


class PredicateMutationControlStore(GrowthAwareTopologyStore):
    """v0.12 semantics plus controlled predicate-schema mutation entry points.

    This is deliberately a control. It can introduce a new predicate, but it keeps
    v0.12's inherited profile reconstruction semantics, which interpret the
    subject-only ``profile:<subject>`` node through the hard-coded ``deadline``
    predicate. v0.13 uses that mismatch as the falsification target.
    """

    def enqueue_predicate_change(
        self,
        index: int,
        new_predicate: str,
        *,
        new_value: int | None = None,
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
            replacement["predicate"] = new_predicate
            if new_value is not None:
                replacement["object_value"] = new_value
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
                "subject_id": item.subject_id,
                "old_predicate": item.predicate,
                "new_predicate": new_predicate,
            }
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def enqueue_predicate_addition(
        self,
        index: int,
        new_predicate: str,
        *,
        value: int = 55,
        writer: str | None = None,
    ) -> dict[str, Any]:
        """Admit ordered evidence + assertion intents for a second subject predicate."""

        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            base = self._get_assertion(conn, assertion_id(index))
            if base is None:
                raise KeyError(assertion_id(index))

            suffix = new_predicate.replace(" ", "_")
            eid = f"evidence-extra-{index:06d}-{suffix}"
            aid = f"assertion-extra-{index:06d}-{suffix}"
            seq = 1_000_000 + index
            evidence = EvidenceRecord(
                id=eid,
                payload=f"{alias(index)} {new_predicate.replace('_', ' ')} is day {value}.",
                source_id="source-v013",
                recorded_seq=seq,
                source_event_time=value,
            )
            assertion = Assertion(
                id=aid,
                subject_id=base.subject_id,
                predicate=new_predicate,
                object_value=value,
                recorded_seq=seq,
                valid_from=value,
                evidence_ids=(eid,),
            )

            evidence_intent = self._enqueue_tx(
                conn,
                UPSERT_EVIDENCE,
                {"evidence": evidence_to_dict(evidence)},
                None,
                writer=writer,
            )
            assertion_intent = self._enqueue_tx(
                conn,
                UPSERT_ASSERTION,
                {"assertion": assertion_to_dict(assertion)},
                None,
                writer=writer,
            )
            conn.commit()
            return {
                "subject_id": base.subject_id,
                "new_predicate": new_predicate,
                "evidence_intent": evidence_intent,
                "assertion_intent": assertion_intent,
                "evidence_id": eid,
                "assertion_id": aid,
            }
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def canonical_predicate(self, index: int) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT predicate FROM assertions WHERE id=?",
                (assertion_id(index),),
            ).fetchone()
            return None if row is None else str(row["predicate"])

    def profile_snapshot(self, subject: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """SELECT value_json FROM derived_nodes
                   WHERE node_id=? AND status='fresh'""",
                (profile_node(subject),),
            ).fetchone()
            return None if row is None else _loads(row["value_json"], {})

    def subject_derived_count(self, subject: str) -> int:
        with self.connect() as conn:
            return int(
                conn.execute(
                    "SELECT COUNT(*) FROM derived_nodes WHERE subject_id=?",
                    (subject,),
                ).fetchone()[0]
            )


class PredicateSchemaAwareStore(PredicateMutationControlStore):
    """v0.13 store whose subject-only profile has subject-wide semantics.

    ``profile_node(subject)`` omits predicate from node identity. Therefore its
    stable meaning must not depend on any one predicate. This implementation
    reconstructs the profile from the latest assertion for every predicate owned by
    the subject, while state/support/context remain predicate-specific.
    """

    def _latest_subject_assertions_tx(
        self,
        conn: sqlite3.Connection,
        subject: str,
        trace: PersistentRecoveryTrace | None = None,
    ) -> list[Assertion]:
        latest: dict[str, Assertion] = {}
        rows = conn.execute(
            """SELECT * FROM assertions INDEXED BY idx_assertions_subject_predicate
               WHERE subject_id=?
               ORDER BY predicate,recorded_seq DESC,id DESC""",
            (subject,),
        )
        for row in rows:
            if trace is not None:
                trace.canonical_rows_read += 1
            predicate = str(row["predicate"])
            if predicate in latest:
                continue
            evidence_ids = [
                r["evidence_id"]
                for r in conn.execute(
                    "SELECT evidence_id FROM assertion_evidence WHERE assertion_id=? ORDER BY evidence_id",
                    (row["id"],),
                )
            ]
            if trace is not None:
                trace.canonical_rows_read += len(evidence_ids)
            latest[predicate] = self._assertion_row_to_model(row, evidence_ids)
        return [latest[predicate] for predicate in sorted(latest)]

    def _subject_profile_materialization_tx(
        self,
        conn: sqlite3.Connection,
        subject: str,
        trace: PersistentRecoveryTrace | None = None,
    ) -> tuple[dict[str, Any], list[str]] | None:
        assertions = self._latest_subject_assertions_tx(conn, subject, trace)
        if not assertions:
            return None

        evidence_payloads: dict[str, str] = {}
        dependencies: list[str] = []
        for assertion in assertions:
            dependencies.append(f"assertion:{assertion.id}")
            for eid, payload in self._payloads(conn, assertion.evidence_ids, trace):
                evidence_payloads[eid] = payload
                dependencies.append(evidence_node(eid))

        value = {
            "subject_id": subject,
            "predicates": [assertion.predicate for assertion in assertions],
            "evidence_payloads": sorted(evidence_payloads.items()),
        }
        return value, sorted(set(dependencies))

    def _rebuild_node_tx(
        self,
        conn: sqlite3.Connection,
        node_id: str,
        trace: PersistentRecoveryTrace,
    ) -> None:
        if not node_id.startswith("profile:"):
            super()._rebuild_node_tx(conn, node_id, trace)
            return

        row = conn.execute(
            "SELECT * FROM derived_nodes WHERE node_id=?",
            (node_id,),
        ).fetchone()
        if row is None:
            return
        trace.derived_rows_read += 1
        subject = str(row["subject_id"])
        materialization = self._subject_profile_materialization_tx(conn, subject, trace)
        if materialization is None:
            self._retire_node(conn, node_id, trace)
            return
        value, dependencies = materialization
        self._write_derived(
            conn,
            node_id,
            "profile",
            subject,
            None,
            value,
            dependencies,
            trace,
        )

    def subject_profile_lookup_uses_index(self, subject: str | None = None) -> bool:
        probe = subject or subject_id(0)
        with self.connect() as conn:
            rows = conn.execute(
                """EXPLAIN QUERY PLAN
                   SELECT * FROM assertions INDEXED BY idx_assertions_subject_predicate
                   WHERE subject_id=?
                   ORDER BY predicate,recorded_seq DESC,id DESC""",
                (probe,),
            ).fetchall()
            detail = " ".join(str(row["detail"]) for row in rows).lower()
            return (
                "idx_assertions_subject_predicate" in detail
                and "search assertions" in detail
                and "scan assertions" not in detail
            )

    def clean_rebuild_digest(self) -> str:
        """Independent deterministic oracle with one profile per subject."""

        node_rows: list[tuple[Any, ...]] = []
        edge_rows: list[tuple[str, str]] = []

        with self.connect() as conn:
            latest_by_key: dict[tuple[str, str], Assertion] = {}
            rows = conn.execute(
                """SELECT * FROM assertions
                   ORDER BY subject_id,predicate,recorded_seq DESC,id DESC"""
            )
            for row in rows:
                key = (str(row["subject_id"]), str(row["predicate"]))
                if key in latest_by_key:
                    continue
                evidence_ids = [
                    r["evidence_id"]
                    for r in conn.execute(
                        "SELECT evidence_id FROM assertion_evidence WHERE assertion_id=? ORDER BY evidence_id",
                        (row["id"],),
                    )
                ]
                latest_by_key[key] = self._assertion_row_to_model(row, evidence_ids)

            by_subject: dict[str, list[Assertion]] = defaultdict(list)
            for assertion in latest_by_key.values():
                by_subject[assertion.subject_id].append(assertion)

            for sid in sorted(by_subject):
                assertions = sorted(by_subject[sid], key=lambda item: item.predicate)
                profile_payloads: dict[str, str] = {}
                profile_dependencies: list[str] = []

                for assertion in assertions:
                    payloads = self._payloads(conn, assertion.evidence_ids)
                    for eid, payload in payloads:
                        profile_payloads[eid] = payload
                    profile_dependencies.extend(
                        [
                            f"assertion:{assertion.id}",
                            *[evidence_node(eid) for eid in assertion.evidence_ids],
                        ]
                    )

                    predicate = assertion.predicate
                    snode = state_node((sid, predicate, "default"))
                    unode = support_node((sid, predicate, "default"))
                    cnode = context_node((sid, predicate, "default"))
                    state = self._state_value(assertion)
                    support = self._support_value(assertion, payloads)
                    context = self._context_value(sid, predicate, support)
                    node_rows.extend(
                        [
                            (snode, "state", sid, predicate, "default", "fresh", _json(state)),
                            (unode, "support", sid, predicate, "default", "fresh", _json(support)),
                            (cnode, "context", sid, predicate, "default", "fresh", _json(context)),
                        ]
                    )
                    edge_rows.extend(
                        [
                            (f"assertion:{assertion.id}", snode),
                            (snode, unode),
                            (f"assertion:{assertion.id}", unode),
                            *[(evidence_node(eid), unode) for eid in assertion.evidence_ids],
                            (unode, cnode),
                        ]
                    )

                pnode = profile_node(sid)
                profile = {
                    "subject_id": sid,
                    "predicates": [assertion.predicate for assertion in assertions],
                    "evidence_payloads": sorted(profile_payloads.items()),
                }
                node_rows.append(
                    (pnode, "profile", sid, None, "default", "fresh", _json(profile))
                )
                edge_rows.extend(
                    (dependency, pnode) for dependency in sorted(set(profile_dependencies))
                )

        h = hashlib.sha256()
        for values in sorted(node_rows, key=lambda item: item[0]):
            self._digest_row(h, "node", values)
        for values in sorted(set(edge_rows)):
            self._digest_row(h, "edge", values)
        return h.hexdigest()
