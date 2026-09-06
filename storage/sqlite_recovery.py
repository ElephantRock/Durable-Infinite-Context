from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from core.models import Assertion, EvidenceRecord
from simulator.cascade import alias, assertion_id, evidence_id, subject_id
from state.cascade import context_node, evidence_node, profile_node, state_node, support_node


PREPARED = "prepared"
CANONICAL_APPLIED = "canonical_applied"
INVALIDATED = "invalidated"
REBUILDING = "rebuilding"
REPAIRED = "repaired"

UPSERT_EVIDENCE = "upsert_evidence"
UPSERT_ASSERTION = "upsert_assertion"
DELETE_ASSERTION = "delete_assertion"


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _loads(value: str | None, default: Any = None) -> Any:
    if value is None:
        return default
    return json.loads(value)


def evidence_to_dict(item: EvidenceRecord) -> dict[str, Any]:
    return asdict(item)


def assertion_to_dict(item: Assertion) -> dict[str, Any]:
    out = asdict(item)
    out["evidence_ids"] = list(item.evidence_ids)
    return out


def evidence_from_dict(data: dict[str, Any]) -> EvidenceRecord:
    return EvidenceRecord(**data)


def assertion_from_dict(data: dict[str, Any]) -> Assertion:
    payload = dict(data)
    payload["evidence_ids"] = tuple(payload.get("evidence_ids", ()))
    return Assertion(**payload)


@dataclass
class PersistentRecoveryTrace:
    journal_reads: int = 0
    journal_writes: int = 0
    canonical_mutations: int = 0
    affected_discovered: int = 0
    invalidated_nodes: int = 0
    reinvalidated_nodes: int = 0
    canonical_rows_read: int = 0
    derived_rows_read: int = 0
    derived_rows_written: int = 0
    edge_mutations: int = 0
    retired_nodes: int = 0

    @property
    def logical_work(self) -> int:
        return sum(
            (
                self.journal_reads,
                self.journal_writes,
                self.canonical_mutations,
                self.affected_discovered,
                self.invalidated_nodes,
                self.reinvalidated_nodes,
                self.canonical_rows_read,
                self.derived_rows_read,
                self.derived_rows_written,
                self.edge_mutations,
                self.retired_nodes,
            )
        )

    def to_dict(self) -> dict[str, int]:
        out = asdict(self)
        out["logical_work"] = self.logical_work
        return out


class SQLiteRecoveryStore:
    """SQLite/WAL persistence falsification vehicle for v0.9.

    Canonical rows, the maintenance journal, derived materializations, lifecycle
    status, and dependency edges all live in one SQLite database. Recovery never
    discovers the affected region by scanning all derived rows; it follows indexed
    dependency edges or reuses the exact affected IDs already persisted in the
    maintenance intent.
    """

    def __init__(self, path: str | Path):
        self.path = str(path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata(
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS evidence(
                    id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    recorded_seq INTEGER NOT NULL,
                    source_event_time INTEGER,
                    scope TEXT NOT NULL,
                    lifecycle TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS assertions(
                    id TEXT PRIMARY KEY,
                    subject_id TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object_json TEXT NOT NULL,
                    recorded_seq INTEGER NOT NULL,
                    valid_from INTEGER,
                    valid_to INTEGER,
                    modality TEXT NOT NULL,
                    polarity TEXT NOT NULL,
                    extraction_version TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_assertions_subject_predicate
                    ON assertions(subject_id, predicate, recorded_seq, id);

                CREATE TABLE IF NOT EXISTS assertion_evidence(
                    assertion_id TEXT NOT NULL,
                    evidence_id TEXT NOT NULL,
                    PRIMARY KEY(assertion_id, evidence_id)
                );
                CREATE INDEX IF NOT EXISTS idx_assertion_evidence_evidence
                    ON assertion_evidence(evidence_id, assertion_id);

                CREATE TABLE IF NOT EXISTS derived_nodes(
                    node_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    predicate TEXT,
                    scope TEXT NOT NULL,
                    status TEXT NOT NULL,
                    value_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_derived_subject_kind
                    ON derived_nodes(subject_id, kind);

                CREATE TABLE IF NOT EXISTS dependency_edges(
                    source_node TEXT NOT NULL,
                    derived_node TEXT NOT NULL,
                    PRIMARY KEY(source_node, derived_node)
                );
                CREATE INDEX IF NOT EXISTS idx_dependency_source
                    ON dependency_edges(source_node, derived_node);
                CREATE INDEX IF NOT EXISTS idx_dependency_derived
                    ON dependency_edges(derived_node, source_node);

                CREATE TABLE IF NOT EXISTS maintenance_journal(
                    intent_id TEXT PRIMARY KEY,
                    operation TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    previous_json TEXT,
                    affected_json TEXT NOT NULL,
                    partial_node TEXT
                );
                """
            )

    @staticmethod
    def _profile_value(subject: str, predicate: str, payloads: list[tuple[str, str]]) -> dict[str, Any]:
        return {
            "subject_id": subject,
            "predicates": [predicate],
            "evidence_payloads": payloads,
        }

    @staticmethod
    def _state_value(assertion: Assertion) -> dict[str, Any]:
        return {
            "status": "resolved",
            "operative_values": [assertion.object_value],
            "supporting_assertion_ids": [assertion.id],
            "competing_assertion_ids": [],
            "historical_assertion_ids": [],
        }

    @staticmethod
    def _support_value(
        assertion: Assertion,
        payloads: list[tuple[str, str]],
    ) -> dict[str, Any]:
        return {
            "status": "resolved",
            "operative_values": [assertion.object_value],
            "assertion_ids": [assertion.id],
            "evidence_payloads": payloads,
        }

    @staticmethod
    def _context_value(subject: str, predicate: str, support: dict[str, Any]) -> dict[str, Any]:
        text = (
            f"ENTITY={subject} PROPERTY={predicate} STATUS={support['status']} "
            f"VALUES={support['operative_values']!r} "
            f"ASSERTIONS={support['assertion_ids']!r} "
            f"EVIDENCE={support['evidence_payloads']!r}"
        )
        return {"text": text}

    def bootstrap(self, entity_count: int) -> None:
        self.initialize()
        conn = self.connect()
        try:
            existing = conn.execute(
                "SELECT value FROM metadata WHERE key='entity_count'"
            ).fetchone()
            if existing is not None:
                if int(existing["value"]) != entity_count:
                    raise ValueError("existing database cardinality does not match bootstrap request")
                return

            conn.execute("BEGIN IMMEDIATE")
            chunk = 1000
            for start in range(0, entity_count, chunk):
                stop = min(entity_count, start + chunk)
                evidence_rows = []
                assertion_rows = []
                assertion_evidence_rows = []
                node_rows = []
                edge_rows = []

                for i in range(start, stop):
                    sid = subject_id(i)
                    eid = evidence_id(i)
                    aid = assertion_id(i)
                    payload = f"{alias(i)} finance migration deadline is day 42."
                    evidence_rows.append((eid, payload, "source", i + 1, 42, "default", "active"))
                    assertion_rows.append(
                        (aid, sid, "deadline", _json(42), i + 1, 42, None, "asserted", "positive", "oracle-v1")
                    )
                    assertion_evidence_rows.append((aid, eid))

                    assertion = Assertion(
                        id=aid,
                        subject_id=sid,
                        predicate="deadline",
                        object_value=42,
                        recorded_seq=i + 1,
                        valid_from=42,
                        evidence_ids=(eid,),
                    )
                    payloads = [(eid, payload)]
                    profile = self._profile_value(sid, "deadline", payloads)
                    state = self._state_value(assertion)
                    support = self._support_value(assertion, payloads)
                    context = self._context_value(sid, "deadline", support)

                    pnode = profile_node(sid)
                    snode = state_node((sid, "deadline", "default"))
                    unode = support_node((sid, "deadline", "default"))
                    cnode = context_node((sid, "deadline", "default"))
                    node_rows.extend(
                        [
                            (pnode, "profile", sid, None, "default", "fresh", _json(profile)),
                            (snode, "state", sid, "deadline", "default", "fresh", _json(state)),
                            (unode, "support", sid, "deadline", "default", "fresh", _json(support)),
                            (cnode, "context", sid, "deadline", "default", "fresh", _json(context)),
                        ]
                    )
                    edge_rows.extend(
                        [
                            (f"assertion:{aid}", pnode),
                            (evidence_node(eid), pnode),
                            (f"assertion:{aid}", snode),
                            (snode, unode),
                            (f"assertion:{aid}", unode),
                            (evidence_node(eid), unode),
                            (unode, cnode),
                        ]
                    )

                conn.executemany(
                    """INSERT INTO evidence
                       (id,payload,source_id,recorded_seq,source_event_time,scope,lifecycle)
                       VALUES (?,?,?,?,?,?,?)""",
                    evidence_rows,
                )
                conn.executemany(
                    """INSERT INTO assertions
                       (id,subject_id,predicate,object_json,recorded_seq,valid_from,valid_to,
                        modality,polarity,extraction_version)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    assertion_rows,
                )
                conn.executemany(
                    "INSERT INTO assertion_evidence(assertion_id,evidence_id) VALUES (?,?)",
                    assertion_evidence_rows,
                )
                conn.executemany(
                    """INSERT INTO derived_nodes
                       (node_id,kind,subject_id,predicate,scope,status,value_json)
                       VALUES (?,?,?,?,?,?,?)""",
                    node_rows,
                )
                conn.executemany(
                    "INSERT INTO dependency_edges(source_node,derived_node) VALUES (?,?)",
                    edge_rows,
                )

            conn.execute(
                "INSERT INTO metadata(key,value) VALUES ('entity_count',?)",
                (str(entity_count),),
            )
            conn.commit()
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def _assertion_row_to_model(self, row: sqlite3.Row, evidence_ids: Iterable[str]) -> Assertion:
        return Assertion(
            id=row["id"],
            subject_id=row["subject_id"],
            predicate=row["predicate"],
            object_value=_loads(row["object_json"]),
            recorded_seq=row["recorded_seq"],
            valid_from=row["valid_from"],
            valid_to=row["valid_to"],
            modality=row["modality"],
            polarity=row["polarity"],
            evidence_ids=tuple(evidence_ids),
            extraction_version=row["extraction_version"],
        )

    def _get_assertion(self, conn: sqlite3.Connection, aid: str) -> Assertion | None:
        row = conn.execute("SELECT * FROM assertions WHERE id=?", (aid,)).fetchone()
        if row is None:
            return None
        evidence_ids = [
            r["evidence_id"]
            for r in conn.execute(
                "SELECT evidence_id FROM assertion_evidence WHERE assertion_id=? ORDER BY evidence_id",
                (aid,),
            )
        ]
        return self._assertion_row_to_model(row, evidence_ids)

    def _latest_assertion(
        self,
        conn: sqlite3.Connection,
        subject: str,
        predicate: str,
        trace: PersistentRecoveryTrace | None = None,
    ) -> Assertion | None:
        row = conn.execute(
            """SELECT * FROM assertions
               WHERE subject_id=? AND predicate=?
               ORDER BY recorded_seq DESC, id DESC LIMIT 1""",
            (subject, predicate),
        ).fetchone()
        if row is None:
            return None
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
        return self._assertion_row_to_model(row, evidence_ids)

    def _payloads(
        self,
        conn: sqlite3.Connection,
        evidence_ids: Iterable[str],
        trace: PersistentRecoveryTrace | None = None,
    ) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for eid in evidence_ids:
            row = conn.execute("SELECT payload FROM evidence WHERE id=?", (eid,)).fetchone()
            if row is not None:
                out.append((eid, row["payload"]))
                if trace is not None:
                    trace.canonical_rows_read += 1
        return sorted(out)

    def prepare(
        self,
        operation: str,
        payload: dict[str, Any],
        previous: dict[str, Any] | None = None,
        intent_id: str = "maintenance-1",
    ) -> str:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM maintenance_journal LIMIT 1").fetchone() is not None:
                raise RuntimeError("v0.9 prototype permits one in-flight maintenance intent")
            conn.execute(
                """INSERT INTO maintenance_journal
                   (intent_id,operation,phase,payload_json,previous_json,affected_json,partial_node)
                   VALUES (?,?,?,?,?,?,NULL)""",
                (
                    intent_id,
                    operation,
                    PREPARED,
                    _json(payload),
                    None if previous is None else _json(previous),
                    "[]",
                ),
            )
            conn.commit()
            return intent_id
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def prepare_operation(self, operation: str, index: int, new_value: int = 77) -> str:
        conn = self.connect()
        try:
            if operation == "replace_evidence_payload":
                eid = evidence_id(index)
                row = conn.execute("SELECT * FROM evidence WHERE id=?", (eid,)).fetchone()
                if row is None:
                    raise KeyError(eid)
                previous = dict(row)
                payload = dict(previous)
                payload["payload"] = f"{alias(index, 'Nova')} finance migration deadline is day 42."
                return self.prepare(UPSERT_EVIDENCE, {"evidence": payload}, {"evidence": previous})
            if operation == "replace_assertion_object":
                aid = assertion_id(index)
                item = self._get_assertion(conn, aid)
                if item is None:
                    raise KeyError(aid)
                replacement = assertion_to_dict(item)
                replacement["object_value"] = new_value
                return self.prepare(
                    UPSERT_ASSERTION,
                    {"assertion": replacement},
                    {"assertion": assertion_to_dict(item)},
                )
            if operation == "delete_assertion":
                aid = assertion_id(index)
                item = self._get_assertion(conn, aid)
                if item is None:
                    raise KeyError(aid)
                return self.prepare(
                    DELETE_ASSERTION,
                    {"assertion_id": aid},
                    {"assertion": assertion_to_dict(item)},
                )
            raise ValueError(f"unsupported operation: {operation}")
        finally:
            conn.close()

    def _intent(self, conn: sqlite3.Connection) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT * FROM maintenance_journal ORDER BY intent_id LIMIT 1"
        ).fetchone()

    def journal_phase(self) -> str | None:
        with self.connect() as conn:
            row = self._intent(conn)
            return None if row is None else row["phase"]

    def _apply_canonical_tx(
        self,
        conn: sqlite3.Connection,
        intent: sqlite3.Row,
        trace: PersistentRecoveryTrace | None = None,
    ) -> None:
        payload = _loads(intent["payload_json"], {})
        operation = intent["operation"]
        if operation == UPSERT_EVIDENCE:
            item = evidence_from_dict(payload["evidence"])
            conn.execute(
                """INSERT INTO evidence
                   (id,payload,source_id,recorded_seq,source_event_time,scope,lifecycle)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     payload=excluded.payload,
                     source_id=excluded.source_id,
                     recorded_seq=excluded.recorded_seq,
                     source_event_time=excluded.source_event_time,
                     scope=excluded.scope,
                     lifecycle=excluded.lifecycle""",
                (
                    item.id,
                    item.payload,
                    item.source_id,
                    item.recorded_seq,
                    item.source_event_time,
                    item.scope,
                    item.lifecycle,
                ),
            )
        elif operation == UPSERT_ASSERTION:
            item = assertion_from_dict(payload["assertion"])
            conn.execute(
                """INSERT INTO assertions
                   (id,subject_id,predicate,object_json,recorded_seq,valid_from,valid_to,
                    modality,polarity,extraction_version)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     subject_id=excluded.subject_id,
                     predicate=excluded.predicate,
                     object_json=excluded.object_json,
                     recorded_seq=excluded.recorded_seq,
                     valid_from=excluded.valid_from,
                     valid_to=excluded.valid_to,
                     modality=excluded.modality,
                     polarity=excluded.polarity,
                     extraction_version=excluded.extraction_version""",
                (
                    item.id,
                    item.subject_id,
                    item.predicate,
                    _json(item.object_value),
                    item.recorded_seq,
                    item.valid_from,
                    item.valid_to,
                    item.modality,
                    item.polarity,
                    item.extraction_version,
                ),
            )
            conn.execute("DELETE FROM assertion_evidence WHERE assertion_id=?", (item.id,))
            conn.executemany(
                "INSERT INTO assertion_evidence(assertion_id,evidence_id) VALUES (?,?)",
                [(item.id, eid) for eid in item.evidence_ids],
            )
        elif operation == DELETE_ASSERTION:
            aid = payload["assertion_id"]
            conn.execute("DELETE FROM assertion_evidence WHERE assertion_id=?", (aid,))
            conn.execute("DELETE FROM assertions WHERE id=?", (aid,))
        else:
            raise ValueError(f"unsupported journal operation: {operation}")
        if trace is not None:
            trace.canonical_mutations += 1

    def apply_canonical_transaction(self) -> None:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            intent = self._intent(conn)
            if intent is None:
                raise RuntimeError("no maintenance intent")
            if intent["phase"] != PREPARED:
                raise RuntimeError(f"canonical apply requires PREPARED, got {intent['phase']}")
            self._apply_canonical_tx(conn, intent)
            conn.execute(
                "UPDATE maintenance_journal SET phase=? WHERE intent_id=?",
                (CANONICAL_APPLIED, intent["intent_id"]),
            )
            conn.commit()
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def begin_canonical_without_commit(self) -> sqlite3.Connection:
        conn = self.connect()
        conn.execute("BEGIN IMMEDIATE")
        intent = self._intent(conn)
        if intent is None or intent["phase"] != PREPARED:
            conn.rollback()
            conn.close()
            raise RuntimeError("prepared intent required")
        self._apply_canonical_tx(conn, intent)
        conn.execute(
            "UPDATE maintenance_journal SET phase=? WHERE intent_id=?",
            (CANONICAL_APPLIED, intent["intent_id"]),
        )
        return conn

    def _affected_nodes(
        self,
        conn: sqlite3.Connection,
        intent: sqlite3.Row,
    ) -> list[str]:
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
                seeds.extend(profile_node(sid) for sid in sorted(subjects))
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
                SELECT derived_node FROM dependency_edges
                WHERE source_node IN ({placeholders})
                UNION
                SELECT e.derived_node
                FROM dependency_edges e
                JOIN affected a ON e.source_node=a.node_id
            )
            SELECT DISTINCT node_id FROM affected ORDER BY node_id
        """
        params = [*seeds, *all_seeds] if seeds else [*all_seeds]
        return [row["node_id"] for row in conn.execute(sql, params)]

    def _invalidate_tx(
        self,
        conn: sqlite3.Connection,
        intent: sqlite3.Row,
        trace: PersistentRecoveryTrace | None = None,
    ) -> list[str]:
        affected = self._affected_nodes(conn, intent)
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

    def invalidate_transaction(self) -> list[str]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            intent = self._intent(conn)
            if intent is None or intent["phase"] != CANONICAL_APPLIED:
                raise RuntimeError("CANONICAL_APPLIED intent required")
            affected = self._invalidate_tx(conn, intent)
            conn.commit()
            return affected
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def begin_invalidation_without_commit(self) -> sqlite3.Connection:
        conn = self.connect()
        conn.execute("BEGIN IMMEDIATE")
        intent = self._intent(conn)
        if intent is None or intent["phase"] != CANONICAL_APPLIED:
            conn.rollback()
            conn.close()
            raise RuntimeError("CANONICAL_APPLIED intent required")
        self._invalidate_tx(conn, intent)
        return conn

    def partial_rebuild_transaction(self) -> str:
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
            rows.sort(key=lambda r: (priorities.get(r["kind"], 99), r["node_id"]))
            if not rows:
                raise RuntimeError("affected nodes disappeared before partial rebuild")
            node_id = rows[0]["node_id"]
            conn.execute(
                "UPDATE derived_nodes SET status='rebuilding', value_json=? WHERE node_id=?",
                (_json({"partial": True}), node_id),
            )
            conn.execute(
                """UPDATE maintenance_journal
                   SET phase=?, partial_node=? WHERE intent_id=?""",
                (REBUILDING, node_id, intent["intent_id"]),
            )
            conn.commit()
            return node_id
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def _replace_edges(
        self,
        conn: sqlite3.Connection,
        node_id: str,
        dependencies: Iterable[str],
        trace: PersistentRecoveryTrace,
    ) -> None:
        old_count = conn.execute(
            "SELECT COUNT(*) AS n FROM dependency_edges WHERE derived_node=?",
            (node_id,),
        ).fetchone()["n"]
        conn.execute("DELETE FROM dependency_edges WHERE derived_node=?", (node_id,))
        deps = sorted(set(dependencies))
        conn.executemany(
            "INSERT INTO dependency_edges(source_node,derived_node) VALUES (?,?)",
            [(dep, node_id) for dep in deps],
        )
        trace.edge_mutations += old_count + len(deps)

    def _retire_node(
        self,
        conn: sqlite3.Connection,
        node_id: str,
        trace: PersistentRecoveryTrace,
    ) -> None:
        in_count = conn.execute(
            "SELECT COUNT(*) AS n FROM dependency_edges WHERE derived_node=?",
            (node_id,),
        ).fetchone()["n"]
        out_count = conn.execute(
            "SELECT COUNT(*) AS n FROM dependency_edges WHERE source_node=?",
            (node_id,),
        ).fetchone()["n"]
        conn.execute(
            "DELETE FROM dependency_edges WHERE derived_node=? OR source_node=?",
            (node_id, node_id),
        )
        cursor = conn.execute("DELETE FROM derived_nodes WHERE node_id=?", (node_id,))
        if cursor.rowcount:
            trace.retired_nodes += 1
            trace.derived_rows_written += 1
        trace.edge_mutations += in_count + out_count

    def _write_derived(
        self,
        conn: sqlite3.Connection,
        node_id: str,
        kind: str,
        subject: str,
        predicate: str | None,
        value: dict[str, Any],
        dependencies: Iterable[str],
        trace: PersistentRecoveryTrace,
    ) -> None:
        conn.execute(
            """INSERT INTO derived_nodes
               (node_id,kind,subject_id,predicate,scope,status,value_json)
               VALUES (?,?,?,?,?,'fresh',?)
               ON CONFLICT(node_id) DO UPDATE SET
                 kind=excluded.kind,
                 subject_id=excluded.subject_id,
                 predicate=excluded.predicate,
                 scope=excluded.scope,
                 status='fresh',
                 value_json=excluded.value_json""",
            (node_id, kind, subject, predicate, "default", _json(value)),
        )
        trace.derived_rows_written += 1
        self._replace_edges(conn, node_id, dependencies, trace)

    def _rebuild_node_tx(
        self,
        conn: sqlite3.Connection,
        node_id: str,
        trace: PersistentRecoveryTrace,
    ) -> None:
        row = conn.execute(
            "SELECT * FROM derived_nodes WHERE node_id=?",
            (node_id,),
        ).fetchone()
        if row is None:
            return
        trace.derived_rows_read += 1
        subject = row["subject_id"]
        kind = row["kind"]
        predicate = row["predicate"] or "deadline"

        if kind == "profile":
            assertion = self._latest_assertion(conn, subject, "deadline", trace)
            if assertion is None:
                self._retire_node(conn, node_id, trace)
                return
            payloads = self._payloads(conn, assertion.evidence_ids, trace)
            value = self._profile_value(subject, assertion.predicate, payloads)
            deps = [f"assertion:{assertion.id}", *[evidence_node(eid) for eid in assertion.evidence_ids]]
            self._write_derived(conn, node_id, kind, subject, None, value, deps, trace)
            return

        if kind == "state":
            assertion = self._latest_assertion(conn, subject, predicate, trace)
            if assertion is None:
                self._retire_node(conn, node_id, trace)
                return
            value = self._state_value(assertion)
            self._write_derived(
                conn,
                node_id,
                kind,
                subject,
                predicate,
                value,
                [f"assertion:{assertion.id}"],
                trace,
            )
            return

        if kind == "support":
            snode = state_node((subject, predicate, "default"))
            state_row = conn.execute(
                "SELECT value_json FROM derived_nodes WHERE node_id=? AND status='fresh'",
                (snode,),
            ).fetchone()
            trace.derived_rows_read += 1
            if state_row is None:
                self._retire_node(conn, node_id, trace)
                return
            assertion = self._latest_assertion(conn, subject, predicate, trace)
            if assertion is None:
                self._retire_node(conn, node_id, trace)
                return
            payloads = self._payloads(conn, assertion.evidence_ids, trace)
            support = self._support_value(assertion, payloads)
            deps = [
                snode,
                f"assertion:{assertion.id}",
                *[evidence_node(eid) for eid in assertion.evidence_ids],
            ]
            self._write_derived(conn, node_id, kind, subject, predicate, support, deps, trace)
            return

        if kind == "context":
            unode = support_node((subject, predicate, "default"))
            support_row = conn.execute(
                "SELECT value_json FROM derived_nodes WHERE node_id=? AND status='fresh'",
                (unode,),
            ).fetchone()
            trace.derived_rows_read += 1
            if support_row is None:
                self._retire_node(conn, node_id, trace)
                return
            support = _loads(support_row["value_json"], {})
            value = self._context_value(subject, predicate, support)
            self._write_derived(conn, node_id, kind, subject, predicate, value, [unode], trace)
            return

        raise ValueError(f"unknown derived node kind: {kind}")

    def _repair_tx(
        self,
        conn: sqlite3.Connection,
        intent: sqlite3.Row,
        trace: PersistentRecoveryTrace,
    ) -> None:
        affected = list(_loads(intent["affected_json"], []))
        if intent["phase"] == REBUILDING:
            conn.executemany(
                "UPDATE derived_nodes SET status='invalid' WHERE node_id=?",
                [(node_id,) for node_id in affected],
            )
            trace.reinvalidated_nodes += len(affected)

        priorities = {"profile": 0, "state": 0, "support": 1, "context": 2}
        rows: list[tuple[int, str]] = []
        for node_id in affected:
            row = conn.execute(
                "SELECT kind FROM derived_nodes WHERE node_id=?",
                (node_id,),
            ).fetchone()
            if row is not None:
                rows.append((priorities.get(row["kind"], 99), node_id))
        rows.sort()
        for _, node_id in rows:
            self._rebuild_node_tx(conn, node_id, trace)

        conn.execute(
            "UPDATE maintenance_journal SET phase=? WHERE intent_id=?",
            (REPAIRED, intent["intent_id"]),
        )
        trace.journal_writes += 1

    def repair_transaction(self) -> PersistentRecoveryTrace:
        trace = PersistentRecoveryTrace()
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            intent = self._intent(conn)
            if intent is None or intent["phase"] not in {INVALIDATED, REBUILDING}:
                raise RuntimeError("INVALIDATED or REBUILDING intent required")
            self._repair_tx(conn, intent, trace)
            conn.commit()
            return trace
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def finalize_transaction(self) -> None:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            intent = self._intent(conn)
            if intent is None or intent["phase"] != REPAIRED:
                raise RuntimeError("REPAIRED intent required")
            conn.execute("DELETE FROM maintenance_journal WHERE intent_id=?", (intent["intent_id"],))
            conn.commit()
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def recover(self) -> PersistentRecoveryTrace:
        trace = PersistentRecoveryTrace()
        conn = self.connect()
        try:
            intent = self._intent(conn)
            if intent is None:
                return trace
            trace.journal_reads += 1
            phase = intent["phase"]
        finally:
            conn.close()

        if phase == PREPARED:
            conn = self.connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                intent = self._intent(conn)
                if intent is None or intent["phase"] != PREPARED:
                    raise RuntimeError("journal phase changed unexpectedly")
                self._apply_canonical_tx(conn, intent, trace)
                conn.execute(
                    "UPDATE maintenance_journal SET phase=? WHERE intent_id=?",
                    (CANONICAL_APPLIED, intent["intent_id"]),
                )
                trace.journal_writes += 1
                conn.commit()
            except BaseException:
                if conn.in_transaction:
                    conn.rollback()
                raise
            finally:
                conn.close()
            phase = CANONICAL_APPLIED

        # SQLite guarantees that canonical mutation and CANONICAL_APPLIED phase
        # advance commit atomically in the same transaction. Unlike v0.8's weaker
        # durability model, a recovered CANONICAL_APPLIED intent therefore does
        # not need a redundant canonical redo.
        if phase == CANONICAL_APPLIED:
            conn = self.connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                intent = self._intent(conn)
                if intent is None or intent["phase"] != CANONICAL_APPLIED:
                    raise RuntimeError("journal phase changed unexpectedly")
                self._invalidate_tx(conn, intent, trace)
                conn.commit()
            except BaseException:
                if conn.in_transaction:
                    conn.rollback()
                raise
            finally:
                conn.close()
            phase = INVALIDATED

        if phase in {INVALIDATED, REBUILDING}:
            conn = self.connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                intent = self._intent(conn)
                if intent is None or intent["phase"] not in {INVALIDATED, REBUILDING}:
                    raise RuntimeError("journal phase changed unexpectedly")
                self._repair_tx(conn, intent, trace)
                conn.commit()
            except BaseException:
                if conn.in_transaction:
                    conn.rollback()
                raise
            finally:
                conn.close()
            phase = REPAIRED

        if phase == REPAIRED:
            conn = self.connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                intent = self._intent(conn)
                if intent is not None:
                    conn.execute(
                        "DELETE FROM maintenance_journal WHERE intent_id=?",
                        (intent["intent_id"],),
                    )
                    trace.journal_writes += 1
                conn.commit()
            except BaseException:
                if conn.in_transaction:
                    conn.rollback()
                raise
            finally:
                conn.close()

        return trace

    def read_context(self, subject: str, predicate: str = "deadline") -> str | None:
        with self.connect() as conn:
            if self._intent(conn) is not None:
                raise RuntimeError("persistent recovery required before derived reads are admitted")
            row = conn.execute(
                """SELECT value_json FROM derived_nodes
                   WHERE node_id=? AND status='fresh'""",
                (context_node((subject, predicate, "default")),),
            ).fetchone()
            if row is None:
                return None
            return _loads(row["value_json"], {}).get("text")

    def all_derived_fresh(self) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM derived_nodes WHERE status!='fresh'"
            ).fetchone()
            return row["n"] == 0

    def journal_empty(self) -> bool:
        with self.connect() as conn:
            return self._intent(conn) is None

    def transaction_settings(self) -> dict[str, Any]:
        with self.connect() as conn:
            return {
                "journal_mode": conn.execute("PRAGMA journal_mode").fetchone()[0].lower(),
                "synchronous": int(conn.execute("PRAGMA synchronous").fetchone()[0]),
            }

    def dependency_lookup_uses_index(self) -> bool:
        with self.connect() as conn:
            rows = conn.execute(
                "EXPLAIN QUERY PLAN SELECT derived_node FROM dependency_edges WHERE source_node=?",
                ("probe",),
            ).fetchall()
            detail = " ".join(str(row["detail"]) for row in rows).lower()
            return "idx_dependency_source" in detail or "sqlite_autoindex_dependency_edges_1" in detail

    def canonical_value(self, index: int) -> Any | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT object_json FROM assertions WHERE id=?",
                (assertion_id(index),),
            ).fetchone()
            return None if row is None else _loads(row["object_json"])

    def evidence_payload(self, index: int) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload FROM evidence WHERE id=?",
                (evidence_id(index),),
            ).fetchone()
            return None if row is None else row["payload"]

    def phase_snapshot(self) -> dict[str, Any]:
        with self.connect() as conn:
            intent = self._intent(conn)
            return {
                "phase": None if intent is None else intent["phase"],
                "journal_rows": conn.execute(
                    "SELECT COUNT(*) AS n FROM maintenance_journal"
                ).fetchone()["n"],
                "invalid_nodes": conn.execute(
                    "SELECT COUNT(*) AS n FROM derived_nodes WHERE status!='fresh'"
                ).fetchone()["n"],
            }

    @staticmethod
    def _digest_row(h: Any, tag: str, values: Iterable[Any]) -> None:
        h.update(tag.encode())
        h.update(b"\0")
        h.update(_json(list(values)).encode())
        h.update(b"\n")

    def materialization_digest(self) -> str:
        h = hashlib.sha256()
        with self.connect() as conn:
            for row in conn.execute(
                """SELECT node_id,kind,subject_id,predicate,scope,status,value_json
                   FROM derived_nodes ORDER BY node_id"""
            ):
                self._digest_row(
                    h,
                    "node",
                    (
                        row["node_id"],
                        row["kind"],
                        row["subject_id"],
                        row["predicate"],
                        row["scope"],
                        row["status"],
                        row["value_json"],
                    ),
                )
            for row in conn.execute(
                "SELECT source_node,derived_node FROM dependency_edges ORDER BY source_node,derived_node"
            ):
                self._digest_row(h, "edge", (row["source_node"], row["derived_node"]))
        return h.hexdigest()

    def clean_rebuild_digest(self) -> str:
        node_rows: list[tuple[Any, ...]] = []
        edge_rows: list[tuple[str, str]] = []

        with self.connect() as conn:
            cursor = conn.execute(
                """SELECT * FROM assertions
                   ORDER BY subject_id,predicate,recorded_seq DESC,id DESC"""
            )
            seen: set[tuple[str, str]] = set()
            for row in cursor:
                key = (row["subject_id"], row["predicate"])
                if key in seen:
                    continue
                seen.add(key)
                evidence_ids = [
                    r["evidence_id"]
                    for r in conn.execute(
                        "SELECT evidence_id FROM assertion_evidence WHERE assertion_id=? ORDER BY evidence_id",
                        (row["id"],),
                    )
                ]
                assertion = self._assertion_row_to_model(row, evidence_ids)
                payloads = self._payloads(conn, evidence_ids)
                sid = assertion.subject_id
                predicate = assertion.predicate
                pnode = profile_node(sid)
                snode = state_node((sid, predicate, "default"))
                unode = support_node((sid, predicate, "default"))
                cnode = context_node((sid, predicate, "default"))
                profile = self._profile_value(sid, predicate, payloads)
                state = self._state_value(assertion)
                support = self._support_value(assertion, payloads)
                context = self._context_value(sid, predicate, support)
                node_rows.extend(
                    [
                        (pnode, "profile", sid, None, "default", "fresh", _json(profile)),
                        (snode, "state", sid, predicate, "default", "fresh", _json(state)),
                        (unode, "support", sid, predicate, "default", "fresh", _json(support)),
                        (cnode, "context", sid, predicate, "default", "fresh", _json(context)),
                    ]
                )
                edge_rows.extend(
                    [
                        (f"assertion:{assertion.id}", pnode),
                        *[(evidence_node(eid), pnode) for eid in assertion.evidence_ids],
                        (f"assertion:{assertion.id}", snode),
                        (snode, unode),
                        (f"assertion:{assertion.id}", unode),
                        *[(evidence_node(eid), unode) for eid in assertion.evidence_ids],
                        (unode, cnode),
                    ]
                )

        h = hashlib.sha256()
        for values in sorted(node_rows, key=lambda row: row[0]):
            self._digest_row(h, "node", values)
        for values in sorted(set(edge_rows)):
            self._digest_row(h, "edge", values)
        return h.hexdigest()

    def materialization_matches_clean_rebuild(self) -> bool:
        return self.materialization_digest() == self.clean_rebuild_digest()
