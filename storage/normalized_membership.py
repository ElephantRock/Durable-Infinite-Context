from __future__ import annotations

import hashlib
import math
import sqlite3
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from core.models import Assertion
from state.cascade import context_node, evidence_node, profile_node, state_node, support_node
from storage.compositional_profile import CompositionalProfileStore
from storage.multi_intent import _read_key
from storage.sqlite_recovery import (
    CANONICAL_APPLIED,
    DELETE_ASSERTION,
    INVALIDATED,
    REBUILDING,
    UPSERT_ASSERTION,
    UPSERT_EVIDENCE,
    PersistentRecoveryTrace,
    _json,
    _loads,
    assertion_from_dict,
)


@dataclass
class MembershipMutationTrace:
    probes: int = 0
    rows_read: int = 0
    rows_written: int = 0
    bytes_read: int = 0
    bytes_written: int = 0

    @property
    def logical_work(self) -> int:
        return self.probes + self.rows_read + self.rows_written

    def to_dict(self) -> dict[str, int]:
        out = asdict(self)
        out["logical_work"] = self.logical_work
        return out


@dataclass
class PhysicalAssemblyTrace:
    journal_queries: int = 0
    descriptor_queries: int = 0
    membership_probes: int = 0
    membership_rows: int = 0
    facet_queries: int = 0
    journal_bytes: int = 0
    descriptor_bytes: int = 0
    membership_bytes: int = 0
    facet_bytes: int = 0
    vm_steps: int = 0
    elapsed_ns: int = 0
    page_size: int = 0
    row_page_units: int = 0

    @property
    def logical_work(self) -> int:
        return (
            self.journal_queries
            + self.descriptor_queries
            + self.membership_probes
            + self.membership_rows
            + self.facet_queries
        )

    @property
    def payload_bytes(self) -> int:
        return (
            self.journal_bytes
            + self.descriptor_bytes
            + self.membership_bytes
            + self.facet_bytes
        )

    def add_row_bytes(self, value: int) -> None:
        if value <= 0:
            return
        self.row_page_units += max(1, math.ceil(value / max(self.page_size, 1)))

    def to_dict(self) -> dict[str, int]:
        out = asdict(self)
        out["logical_work"] = self.logical_work
        out["payload_bytes"] = self.payload_bytes
        return out


class NormalizedPredicateMembershipStore(CompositionalProfileStore):
    """v0.16 candidate: normalize subject predicate membership into indexed rows.

    v0.15 removes O(P) evidence reconstruction from selective profile reads, but it
    still reads and deserializes one serialized predicate manifest whose size grows
    with P. This store keeps ``profile:<subject>`` as a constant-size descriptor and
    stores live predicate membership as indexed ``(subject,predicate)`` rows.

    Selective reads validate K requested predicates through K indexed membership
    probes, while full reads enumerate P membership rows. Predicate topology changes
    update only affected membership rows. Exact logical profile semantics remain the
    same as v0.15; only the physical representation changes.
    """

    def __init__(self, path: str):
        super().__init__(path)
        self._membership_trace = MembershipMutationTrace()

    def initialize(self) -> None:
        super().initialize()
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS profile_predicate_membership(
                    subject_id TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    PRIMARY KEY(subject_id,predicate)
                );
                CREATE INDEX IF NOT EXISTS idx_profile_membership_subject
                    ON profile_predicate_membership(subject_id,predicate);
                """
            )

    def bootstrap(self, entity_count: int) -> None:
        super().bootstrap(entity_count)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM profile_predicate_membership")
            conn.execute(
                """INSERT INTO profile_predicate_membership(subject_id,predicate)
                   SELECT subject_id,predicate FROM subject_predicate_heads"""
            )
            conn.commit()
        self._membership_trace = MembershipMutationTrace()

    def _subject_profile_materialization_tx(
        self,
        conn: sqlite3.Connection,
        subject: str,
        trace: PersistentRecoveryTrace | None = None,
    ) -> tuple[dict[str, Any], list[str]] | None:
        row = conn.execute(
            """SELECT 1 FROM subject_predicate_heads
               INDEXED BY idx_subject_predicate_heads_subject
               WHERE subject_id=? LIMIT 1""",
            (subject,),
        ).fetchone()
        if row is None:
            return None
        self._subject_head_trace.head_rows_read += 1
        return {"subject_id": subject}, []

    @staticmethod
    def _key_bytes(subject: str, predicate: str) -> int:
        return len(subject.encode("utf-8")) + len(predicate.encode("utf-8"))

    def _sync_membership_key_tx(
        self,
        conn: sqlite3.Connection,
        subject: str,
        predicate: str,
    ) -> None:
        self._membership_trace.probes += 1
        head = conn.execute(
            """SELECT predicate FROM subject_predicate_heads
               WHERE subject_id=? AND predicate=?""",
            (subject, predicate),
        ).fetchone()
        key_bytes = self._key_bytes(subject, predicate)
        if head is not None:
            self._membership_trace.rows_read += 1
            self._membership_trace.bytes_read += key_bytes
            cursor = conn.execute(
                """INSERT OR IGNORE INTO profile_predicate_membership(subject_id,predicate)
                   VALUES (?,?)""",
                (subject, predicate),
            )
            if cursor.rowcount > 0:
                self._membership_trace.rows_written += 1
                self._membership_trace.bytes_written += key_bytes
            return
        cursor = conn.execute(
            """DELETE FROM profile_predicate_membership
               WHERE subject_id=? AND predicate=?""",
            (subject, predicate),
        )
        if cursor.rowcount > 0:
            self._membership_trace.rows_written += 1
            self._membership_trace.bytes_written += key_bytes

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
        elif operation != UPSERT_EVIDENCE:
            raise ValueError(operation)

        super()._apply_canonical_tx(conn, intent, trace)
        for subject, predicate in sorted(keys):
            self._sync_membership_key_tx(conn, subject, predicate)

    def recover(self) -> PersistentRecoveryTrace:
        self._membership_trace = MembershipMutationTrace()
        return super().recover()

    def membership_mutation_trace(self) -> dict[str, int]:
        return self._membership_trace.to_dict()

    def membership_matches_heads(self) -> bool:
        with self.connect() as conn:
            expected = {
                (str(row["subject_id"]), str(row["predicate"]))
                for row in conn.execute(
                    "SELECT subject_id,predicate FROM subject_predicate_heads"
                )
            }
            observed = {
                (str(row["subject_id"]), str(row["predicate"]))
                for row in conn.execute(
                    "SELECT subject_id,predicate FROM profile_predicate_membership"
                )
            }
        return expected == observed

    def membership_lookup_uses_index(self, subject: str, predicate: str = "deadline") -> bool:
        with self.connect() as conn:
            rows = conn.execute(
                """EXPLAIN QUERY PLAN
                   SELECT predicate FROM profile_predicate_membership
                   WHERE subject_id=? AND predicate=?""",
                (subject, predicate),
            ).fetchall()
            detail = " ".join(str(row["detail"]) for row in rows).lower()
            return "search profile_predicate_membership" in detail and "scan profile_predicate_membership" not in detail

    def membership_enumeration_uses_index(self, subject: str) -> bool:
        with self.connect() as conn:
            rows = conn.execute(
                """EXPLAIN QUERY PLAN
                   SELECT predicate FROM profile_predicate_membership
                   INDEXED BY idx_profile_membership_subject
                   WHERE subject_id=? ORDER BY predicate""",
                (subject,),
            ).fetchall()
            detail = " ".join(str(row["detail"]) for row in rows).lower()
            return "idx_profile_membership_subject" in detail and "search profile_predicate_membership" in detail

    def membership_btree_height(self) -> int | None:
        """Return maximum pages on a root-to-leaf path when SQLite dbstat is available."""
        try:
            with self.connect() as conn:
                rows = conn.execute(
                    "SELECT path FROM dbstat WHERE name='idx_profile_membership_subject'"
                ).fetchall()
        except sqlite3.DatabaseError:
            return None
        if not rows:
            return None
        height = 1
        for row in rows:
            path = str(row["path"])
            depth = 1 if path == "/" else max(1, path.count("/"))
            height = max(height, depth)
        return height

    def descriptor_storage_bytes(self, subject: str) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT LENGTH(value_json) AS n FROM derived_nodes WHERE node_id=?",
                (profile_node(subject),),
            ).fetchone()
            return 0 if row is None else int(row["n"])

    def subject_membership_bytes(self, subject: str) -> int:
        with self.connect() as conn:
            row = conn.execute(
                """SELECT COALESCE(SUM(LENGTH(subject_id)+LENGTH(predicate)),0) AS n
                   FROM profile_predicate_membership WHERE subject_id=?""",
                (subject,),
            ).fetchone()
            return int(row["n"])

    def subject_membership_count(self, subject: str) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM profile_predicate_membership WHERE subject_id=?",
                (subject,),
            ).fetchone()
            return int(row["n"])

    def read_composed_profile(
        self,
        subject: str,
        requested_predicates: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        trace = PhysicalAssemblyTrace()
        started = time.perf_counter_ns()
        with self.connect() as conn:
            conn.execute("BEGIN")
            trace.page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])

            def progress() -> int:
                trace.vm_steps += 1
                return 0

            conn.set_progress_handler(progress, 1)
            try:
                trace.journal_queries += 1
                intent = conn.execute(
                    """SELECT intent_id,phase FROM maintenance_journal
                       ORDER BY intent_id LIMIT 1"""
                ).fetchone()
                if intent is not None:
                    n = len(str(intent["intent_id"]).encode("utf-8")) + len(str(intent["phase"]).encode("utf-8"))
                    trace.journal_bytes += n
                    trace.add_row_bytes(n)

                trace.descriptor_queries += 1
                descriptor = conn.execute(
                    """SELECT value_json,LENGTH(value_json) AS n FROM derived_nodes
                       WHERE node_id=? AND status='fresh'""",
                    (profile_node(subject),),
                ).fetchone()
                if descriptor is None:
                    return {"value": None, "trace": trace.to_dict()}
                descriptor_n = int(descriptor["n"])
                trace.descriptor_bytes += descriptor_n
                trace.add_row_bytes(descriptor_n)

                requested = None if requested_predicates is None else list(requested_predicates)
                if intent is not None and str(intent["phase"]) in {CANONICAL_APPLIED, INVALIDATED, REBUILDING}:
                    queued = conn.execute(
                        "SELECT read_keys_json FROM intent_queue WHERE intent_id=?",
                        (intent["intent_id"],),
                    ).fetchone()
                    trace.journal_queries += 1
                    read_keys = set()
                    if queued is not None:
                        raw = str(queued["read_keys_json"])
                        n = len(raw.encode("utf-8"))
                        trace.journal_bytes += n
                        trace.add_row_bytes(n)
                        read_keys = set(_loads(raw, []))
                    if requested is None:
                        if queued is None or any(key.startswith(f"{subject}|") for key in read_keys):
                            raise RuntimeError("active maintenance can make full profile stale")
                    else:
                        targets = {_read_key(subject, predicate) for predicate in requested}
                        if queued is None or targets.intersection(read_keys):
                            raise RuntimeError("active maintenance can make requested profile facets stale")

                if requested is None:
                    membership_rows = conn.execute(
                        """SELECT predicate,LENGTH(predicate) AS n
                           FROM profile_predicate_membership
                           INDEXED BY idx_profile_membership_subject
                           WHERE subject_id=? ORDER BY predicate""",
                        (subject,),
                    ).fetchall()
                    requested = []
                    for row in membership_rows:
                        requested.append(str(row["predicate"]))
                        n = int(row["n"])
                        trace.membership_rows += 1
                        trace.membership_bytes += n
                        trace.add_row_bytes(n)
                else:
                    for predicate in requested:
                        trace.membership_probes += 1
                        row = conn.execute(
                            """SELECT predicate,LENGTH(predicate) AS n
                               FROM profile_predicate_membership
                               WHERE subject_id=? AND predicate=?""",
                            (subject, predicate),
                        ).fetchone()
                        if row is None:
                            raise KeyError(f"requested predicate is not live for {subject}: {predicate}")
                        n = int(row["n"])
                        trace.membership_rows += 1
                        trace.membership_bytes += n
                        trace.add_row_bytes(n)

                evidence_payloads: dict[str, str] = {}
                for predicate in requested:
                    trace.facet_queries += 1
                    row = conn.execute(
                        """SELECT value_json,LENGTH(value_json) AS n FROM derived_nodes
                           WHERE node_id=? AND status='fresh'""",
                        (support_node((subject, predicate, "default")),),
                    ).fetchone()
                    if row is None:
                        raise RuntimeError(f"missing fresh profile facet: {subject}/{predicate}")
                    n = int(row["n"])
                    trace.facet_bytes += n
                    trace.add_row_bytes(n)
                    support = _loads(row["value_json"], {})
                    for eid, payload in support.get("evidence_payloads", []):
                        evidence_payloads[str(eid)] = str(payload)

                value = {
                    "subject_id": subject,
                    "predicates": requested,
                    "evidence_payloads": [
                        [eid, payload] for eid, payload in sorted(evidence_payloads.items())
                    ],
                }
                return {"value": value, "trace": trace.to_dict()}
            finally:
                conn.set_progress_handler(None, 0)
                trace.elapsed_ns = time.perf_counter_ns() - started

    def clean_rebuild_digest(self) -> str:
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
                for assertion in assertions:
                    payloads = self._payloads(conn, assertion.evidence_ids)
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
                node_rows.append(
                    (profile_node(sid), "profile", sid, None, "default", "fresh", _json({"subject_id": sid}))
                )

        h = hashlib.sha256()
        for values in sorted(node_rows, key=lambda item: item[0]):
            self._digest_row(h, "node", values)
        for values in sorted(set(edge_rows)):
            self._digest_row(h, "edge", values)
        return h.hexdigest()
