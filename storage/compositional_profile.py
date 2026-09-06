from __future__ import annotations

import hashlib
import sqlite3
from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from core.models import Assertion
from state.cascade import context_node, evidence_node, profile_node, state_node, support_node
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
from storage.subject_heads import HeadIndexedPredicateStore, SubjectHeadTrace
from storage.topology_intent import PromotionRevalidatedTopologyStore


def predicate_presence_node(subject: str, predicate: str) -> str:
    return f"predicate-presence:{subject}:{predicate}"


@dataclass
class ProfileAssemblyTrace:
    journal_reads: int = 0
    manifest_reads: int = 0
    facet_reads: int = 0

    @property
    def logical_work(self) -> int:
        return self.journal_reads + self.manifest_reads + self.facet_reads

    def to_dict(self) -> dict[str, int]:
        out = asdict(self)
        out["logical_work"] = self.logical_work
        return out


class CompositionalProfileStore(HeadIndexedPredicateStore):
    """v0.15 store that separates subject schema from evidence-bearing facets.

    v0.14 removes historical-depth H from current subject-profile reconstruction, but
    every evidence update still invalidates and rewrites the monolithic subject
    profile, so maintenance remains O(P) in the subject's live predicate count even
    when only K=1 predicate changed.

    v0.15 makes ``profile:<subject>`` a predicate manifest only. Predicate-specific
    support/context nodes already contain the evidence-bearing facets. Evidence and
    object-value changes therefore repair only the affected predicate region, while a
    full logical profile is assembled on demand from the manifest plus P support
    facets. A query requesting K predicates can assemble K facets from one coherent
    SQLite snapshot.
    """

    def bootstrap(self, entity_count: int) -> None:
        super().bootstrap(entity_count)
        self._rewrite_all_profile_manifests()
        self._subject_head_trace = SubjectHeadTrace()

    def _rewrite_all_profile_manifests(self) -> None:
        trace = PersistentRecoveryTrace()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            subjects = [
                str(row["subject_id"])
                for row in conn.execute(
                    "SELECT DISTINCT subject_id FROM subject_predicate_heads ORDER BY subject_id"
                )
            ]
            for subject in subjects:
                materialization = self._subject_profile_materialization_tx(conn, subject, trace)
                if materialization is None:
                    continue
                value, dependencies = materialization
                self._write_derived(
                    conn,
                    profile_node(subject),
                    "profile",
                    subject,
                    None,
                    value,
                    dependencies,
                    trace,
                )
            conn.commit()

    def _subject_profile_materialization_tx(
        self,
        conn: sqlite3.Connection,
        subject: str,
        trace: PersistentRecoveryTrace | None = None,
    ) -> tuple[dict[str, Any], list[str]] | None:
        predicates: list[str] = []
        for row in conn.execute(
            """SELECT predicate FROM subject_predicate_heads
               INDEXED BY idx_subject_predicate_heads_subject
               WHERE subject_id=? ORDER BY predicate""",
            (subject,),
        ):
            self._subject_head_trace.head_rows_read += 1
            predicates.append(str(row["predicate"]))
        if not predicates:
            return None
        value = {"subject_id": subject, "predicates": predicates}
        dependencies = [predicate_presence_node(subject, predicate) for predicate in predicates]
        return value, dependencies

    @staticmethod
    def _predicate_count_tx(conn: sqlite3.Connection, subject: str, predicate: str) -> int:
        return int(
            conn.execute(
                """SELECT COUNT(*) FROM assertions INDEXED BY idx_assertions_subject_predicate
                   WHERE subject_id=? AND predicate=?""",
                (subject, predicate),
            ).fetchone()[0]
        )

    def _manifest_changed_subjects_tx(
        self,
        conn: sqlite3.Connection,
        intent: sqlite3.Row,
    ) -> set[str]:
        operation = str(intent["operation"])
        payload = _loads(intent["payload_json"], {})
        previous = _loads(intent["previous_json"], {}) if intent["previous_json"] else {}

        if operation == UPSERT_EVIDENCE:
            return set()

        if operation == UPSERT_ASSERTION:
            item = assertion_from_dict(payload["assertion"])
            old_data = previous.get("assertion")
            old = assertion_from_dict(old_data) if old_data else None
            if old is not None and old.key == item.key:
                return set()

            changed: set[str] = set()
            if old is not None:
                if self._predicate_count_tx(conn, old.subject_id, old.predicate) == 0:
                    changed.add(old.subject_id)
            # After canonical apply, count==1 means this mutation introduced the
            # predicate key; count>1 means it already existed before the mutation.
            if self._predicate_count_tx(conn, item.subject_id, item.predicate) == 1:
                changed.add(item.subject_id)
            return changed

        if operation == DELETE_ASSERTION:
            old = assertion_from_dict(previous["assertion"])
            if self._predicate_count_tx(conn, old.subject_id, old.predicate) == 0:
                return {old.subject_id}
            return set()

        raise ValueError(operation)

    def _invalidate_tx(
        self,
        conn: sqlite3.Connection,
        intent: sqlite3.Row,
        trace: PersistentRecoveryTrace | None = None,
    ) -> list[str]:
        growth_specs = self._growth_specs_tx(conn, intent, trace)
        existing_region = PromotionRevalidatedTopologyStore._affected_nodes(self, conn, intent)
        changed_subjects = self._manifest_changed_subjects_tx(conn, intent)
        changed_profiles = {profile_node(subject) for subject in changed_subjects}

        # The inherited v0.9/v0.13 region explicitly seeds profile nodes for some
        # assertion/evidence shape changes. Under manifest semantics those are stale
        # only when the predicate-presence set itself changed.
        existing_region = [
            node_id
            for node_id in existing_region
            if not node_id.startswith("profile:") or node_id in changed_profiles
        ]
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

    @staticmethod
    def _phase_can_be_stale(phase: str | None) -> bool:
        return phase in {CANONICAL_APPLIED, INVALIDATED, REBUILDING}

    def read_composed_profile(
        self,
        subject: str,
        requested_predicates: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        """Assemble one coherent logical profile from one WAL snapshot.

        Full-profile reads block if any active maintenance key belongs to the subject,
        because topology changes can alter the manifest itself. Explicit partial
        reads block only when their requested predicate key is protected by the active
        intent, preserving unrelated-facet availability.
        """

        trace = ProfileAssemblyTrace()
        with self.connect() as conn:
            conn.execute("BEGIN")
            intent = conn.execute(
                "SELECT * FROM maintenance_journal ORDER BY intent_id LIMIT 1"
            ).fetchone()
            trace.journal_reads += 1

            manifest_row = conn.execute(
                """SELECT value_json FROM derived_nodes
                   WHERE node_id=? AND status='fresh'""",
                (profile_node(subject),),
            ).fetchone()
            trace.manifest_reads += 1
            if manifest_row is None:
                return {"value": None, "trace": trace.to_dict()}
            manifest = _loads(manifest_row["value_json"], {})
            live = list(manifest.get("predicates", []))

            requested = live if requested_predicates is None else list(requested_predicates)
            missing = sorted(set(requested).difference(live))
            if missing:
                raise KeyError(f"requested predicates are not live for {subject}: {missing}")

            if intent is not None and self._phase_can_be_stale(str(intent["phase"])):
                queued = conn.execute(
                    "SELECT read_keys_json FROM intent_queue WHERE intent_id=?",
                    (intent["intent_id"],),
                ).fetchone()
                trace.journal_reads += 1
                read_keys = set(_loads(queued["read_keys_json"], [])) if queued else set()
                if requested_predicates is None:
                    if queued is None or any(key.startswith(f"{subject}|") for key in read_keys):
                        raise RuntimeError("active maintenance can make full profile stale")
                else:
                    targets = {_read_key(subject, predicate) for predicate in requested}
                    if queued is None or targets.intersection(read_keys):
                        raise RuntimeError("active maintenance can make requested profile facets stale")

            evidence_payloads: dict[str, str] = {}
            for predicate in requested:
                row = conn.execute(
                    """SELECT value_json FROM derived_nodes
                       WHERE node_id=? AND status='fresh'""",
                    (support_node((subject, predicate, "default")),),
                ).fetchone()
                trace.facet_reads += 1
                if row is None:
                    raise RuntimeError(f"missing fresh profile facet: {subject}/{predicate}")
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

    def clean_composed_profile(
        self,
        subject: str,
        requested_predicates: Iterable[str] | None = None,
    ) -> dict[str, Any] | None:
        """Independent canonical oracle for the logical composed profile."""

        latest: dict[str, Assertion] = {}
        with self.connect() as conn:
            for row in conn.execute(
                """SELECT * FROM assertions
                   WHERE subject_id=? ORDER BY predicate,recorded_seq DESC,id DESC""",
                (subject,),
            ):
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
                latest[predicate] = self._assertion_row_to_model(row, evidence_ids)
            if not latest:
                return None
            live = sorted(latest)
            requested = live if requested_predicates is None else list(requested_predicates)
            missing = sorted(set(requested).difference(live))
            if missing:
                raise KeyError(missing)
            payloads: dict[str, str] = {}
            for predicate in requested:
                assertion = latest[predicate]
                for eid, payload in self._payloads(conn, assertion.evidence_ids):
                    payloads[eid] = payload
            return {
                "subject_id": subject,
                "predicates": requested,
                "evidence_payloads": [
                    [eid, payload] for eid, payload in sorted(payloads.items())
                ],
            }

    def clean_rebuild_digest(self) -> str:
        """Independent deterministic oracle for manifest + predicate facets."""

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

                predicates = [assertion.predicate for assertion in assertions]
                pnode = profile_node(sid)
                manifest = {"subject_id": sid, "predicates": predicates}
                node_rows.append(
                    (pnode, "profile", sid, None, "default", "fresh", _json(manifest))
                )
                edge_rows.extend(
                    (predicate_presence_node(sid, predicate), pnode)
                    for predicate in predicates
                )

        h = hashlib.sha256()
        for values in sorted(node_rows, key=lambda item: item[0]):
            self._digest_row(h, "node", values)
        for values in sorted(set(edge_rows)):
            self._digest_row(h, "edge", values)
        return h.hexdigest()
