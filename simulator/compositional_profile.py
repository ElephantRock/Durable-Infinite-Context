from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Type

from simulator.cascade import evidence_id, subject_id
from simulator.subject_fanout import _seed_subject_fanout, fanout_predicate
from storage.compositional_profile import CompositionalProfileStore
from storage.predicate_schema import PredicateMutationControlStore
from storage.sqlite_recovery import (
    DELETE_ASSERTION,
    UPSERT_EVIDENCE,
    PersistentRecoveryTrace,
    _json,
)
from storage.subject_heads import HeadIndexedPredicateStore


StoreType = Type[HeadIndexedPredicateStore]


def _extra_evidence_id(index: int, predicate: str) -> str:
    return f"evidence-extra-{index:06d}-{predicate.replace(' ', '_')}"


def evidence_for_position(index: int, position: int) -> str:
    return evidence_id(index) if position == 0 else _extra_evidence_id(index, fanout_predicate(position))


def _enqueue_evidence_update(store: HeadIndexedPredicateStore, eid: str, tag: str) -> dict[str, Any]:
    conn = store.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM evidence WHERE id=?", (eid,)).fetchone()
        if row is None:
            raise KeyError(eid)
        previous = dict(row)
        replacement = dict(previous)
        replacement["payload"] = f"{previous['payload']} [v0.15 update {tag}]"
        result = store._enqueue_tx(
            conn,
            UPSERT_EVIDENCE,
            {"evidence": replacement},
            {"evidence": previous},
            writer=f"v015-{tag}",
        )
        conn.commit()
        return result
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.close()


def _enqueue_assertion_delete(store: HeadIndexedPredicateStore, aid: str) -> dict[str, Any]:
    conn = store.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        item = store._get_assertion(conn, aid)
        if item is None:
            raise KeyError(aid)
        from storage.sqlite_recovery import assertion_to_dict

        result = store._enqueue_tx(
            conn,
            DELETE_ASSERTION,
            {"assertion_id": aid},
            {"assertion": assertion_to_dict(item)},
            writer="v015-delete-added-predicate",
        )
        conn.commit()
        return result
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.close()


def _accumulate_trace(total: PersistentRecoveryTrace, part: PersistentRecoveryTrace) -> None:
    for field in PersistentRecoveryTrace.__dataclass_fields__:
        setattr(total, field, getattr(total, field) + getattr(part, field))


def _drain_measured(store: HeadIndexedPredicateStore) -> dict[str, Any]:
    total = PersistentRecoveryTrace()
    head_work = 0
    promotions = 0
    conflicts = 0
    rounds = 0
    while True:
        promoted = store.promote_next()
        if promoted is None:
            break
        if promoted["status"] == "conflict":
            conflicts += 1
            continue
        if promoted["status"] == "active":
            promotions += 1
            part = store.recover()
            _accumulate_trace(total, part)
            head_work += int(store.subject_head_trace()["logical_work"])
            rounds += 1
            continue
        if promoted["status"] == "busy":
            part = store.recover()
            _accumulate_trace(total, part)
            head_work += int(store.subject_head_trace()["logical_work"])
            rounds += 1
            continue
        raise AssertionError(promoted)
    return {
        "promotions": promotions,
        "conflicts": conflicts,
        "recovery_rounds": rounds,
        "base_trace": total.to_dict(),
        "head_work": head_work,
        "total_work": total.logical_work + head_work,
        "queue_final": store.queue_counts(),
    }


def _profile_storage_bytes(store: HeadIndexedPredicateStore, subject: str) -> int:
    with store.connect() as conn:
        row = conn.execute(
            "SELECT LENGTH(value_json) AS n FROM derived_nodes WHERE node_id=?",
            (f"profile:{subject}",),
        ).fetchone()
        return 0 if row is None else int(row["n"])


def _run_case(
    store_cls: StoreType,
    *,
    entity_count: int,
    predicate_count: int,
    history_depth: int,
    changed_count: int,
) -> dict[str, Any]:
    if changed_count < 1 or changed_count > predicate_count:
        raise ValueError("changed_count must be between 1 and predicate_count")
    index = entity_count // 2
    with tempfile.TemporaryDirectory(prefix="dic-v015-composed-") as tmp:
        store = store_cls(Path(tmp) / "memory.sqlite3")
        predicates = _seed_subject_fanout(
            store,
            entity_count=entity_count,
            index=index,
            predicate_count=predicate_count,
            history_depth=history_depth,
        )
        subject = subject_id(index)
        changed = predicates[:changed_count]
        for position, predicate in enumerate(changed):
            _enqueue_evidence_update(
                store,
                evidence_for_position(index, position),
                f"{predicate}-{position}",
            )

        measured = _drain_measured(store)
        profile = store.profile_snapshot(subject)
        if profile is None:
            raise AssertionError("profile disappeared during v0.15 measurement")

        out: dict[str, Any] = {
            "store": store_cls.__name__,
            "entity_count": entity_count,
            "subject_index": index,
            "subject_id": subject,
            "predicate_count": predicate_count,
            "history_depth": history_depth,
            "changed_count": changed_count,
            "changed_predicates": changed,
            "manifest_predicates": list(profile.get("predicates", [])),
            "profile_has_embedded_evidence": bool(profile.get("evidence_payloads", [])),
            "profile_storage_bytes": _profile_storage_bytes(store, subject),
            "recovery": measured,
            "materialization_equal": store.materialization_matches_clean_rebuild(),
            "all_derived_fresh": store.all_derived_fresh(),
            "full_rebuild_work": store.full_rebuild_work(),
            "head_index_equal": store.head_index_matches_canonical(),
        }

        if isinstance(store, CompositionalProfileStore):
            full = store.read_composed_profile(subject)
            partial = store.read_composed_profile(subject, changed)
            full_oracle = store.clean_composed_profile(subject)
            partial_oracle = store.clean_composed_profile(subject, changed)
            out.update(
                {
                    "full_profile": full["value"],
                    "full_assembly_trace": full["trace"],
                    "full_assembly_equal": full["value"] == full_oracle,
                    "full_profile_bytes": len(_json(full["value"])),
                    "partial_profile": partial["value"],
                    "partial_assembly_trace": partial["trace"],
                    "partial_assembly_equal": partial["value"] == partial_oracle,
                    "partial_profile_bytes": len(_json(partial["value"])),
                }
            )
        return out


def run_v014_monolithic_control(
    entity_count: int = 128,
    predicate_count: int = 32,
    history_depth: int = 8,
    changed_count: int = 1,
) -> dict[str, Any]:
    return _run_case(
        HeadIndexedPredicateStore,
        entity_count=entity_count,
        predicate_count=predicate_count,
        history_depth=history_depth,
        changed_count=changed_count,
    )


def run_v015_compositional_case(
    entity_count: int = 128,
    predicate_count: int = 32,
    history_depth: int = 8,
    changed_count: int = 1,
) -> dict[str, Any]:
    return _run_case(
        CompositionalProfileStore,
        entity_count=entity_count,
        predicate_count=predicate_count,
        history_depth=history_depth,
        changed_count=changed_count,
    )


def run_manifest_topology_case(entity_count: int = 64, index: int = 30) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v015-manifest-topology-") as tmp:
        store = CompositionalProfileStore(Path(tmp) / "memory.sqlite3")
        _seed_subject_fanout(
            store,
            entity_count=entity_count,
            index=index,
            predicate_count=2,
            history_depth=4,
        )
        subject = subject_id(index)
        before = store.read_composed_profile(subject)["value"]

        added = store.enqueue_predicate_addition(
            index,
            "facet_added",
            value=88,
            writer="v015-manifest-add",
        )
        add_drain = store.drain_all()
        after_add = store.read_composed_profile(subject)["value"]
        add_equal = store.materialization_matches_clean_rebuild()

        _enqueue_assertion_delete(store, added["assertion_id"])
        delete_drain = store.drain_all()
        after_delete = store.read_composed_profile(subject)["value"]

        return {
            "subject_id": subject,
            "before_predicates": before["predicates"],
            "after_add_predicates": after_add["predicates"],
            "after_delete_predicates": after_delete["predicates"],
            "add_drain": add_drain,
            "delete_drain": delete_drain,
            "add_materialization_equal": add_equal,
            "delete_materialization_equal": store.materialization_matches_clean_rebuild(),
            "head_index_equal": store.head_index_matches_canonical(),
            "all_derived_fresh": store.all_derived_fresh(),
        }


def run_composed_read_protection_case(entity_count: int = 48, index: int = 24) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v015-read-protection-") as tmp:
        store = CompositionalProfileStore(Path(tmp) / "memory.sqlite3")
        predicates = _seed_subject_fanout(
            store,
            entity_count=entity_count,
            index=index,
            predicate_count=4,
            history_depth=4,
        )
        subject = subject_id(index)
        _enqueue_evidence_update(store, evidence_for_position(index, 0), "read-protection")
        promoted = store.promote_next()
        if promoted is None or promoted.get("status") != "active":
            raise AssertionError(promoted)
        store.apply_canonical_transaction()

        unrelated_partial = store.read_composed_profile(subject, [predicates[1]])
        affected_blocked = False
        full_blocked = False
        try:
            store.read_composed_profile(subject, [predicates[0]])
        except RuntimeError:
            affected_blocked = True
        try:
            store.read_composed_profile(subject)
        except RuntimeError:
            full_blocked = True

        store.recover()
        final_full = store.read_composed_profile(subject)
        return {
            "subject_id": subject,
            "unrelated_partial_present": unrelated_partial["value"] is not None,
            "affected_partial_blocked": affected_blocked,
            "full_profile_blocked": full_blocked,
            "final_full_equal": final_full["value"] == store.clean_composed_profile(subject),
            "materialization_equal": store.materialization_matches_clean_rebuild(),
        }
