from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Type

from simulator.cascade import assertion_id, evidence_id, subject_id
from storage.predicate_schema import PredicateSchemaAwareStore
from storage.subject_heads import HeadIndexedPredicateStore
from storage.sqlite_recovery import _json


StoreType = Type[PredicateSchemaAwareStore]


def fanout_predicate(position: int) -> str:
    if position == 0:
        return "deadline"
    return f"facet_{position:03d}"


def _seed_subject_fanout(
    store: PredicateSchemaAwareStore,
    *,
    entity_count: int,
    index: int,
    predicate_count: int,
    history_depth: int,
) -> list[str]:
    if entity_count < 2:
        raise ValueError("entity_count must be >= 2")
    if index < 0 or index >= entity_count:
        raise ValueError("index must refer to a bootstrap subject")
    if predicate_count < 1:
        raise ValueError("predicate_count must be >= 1")
    if history_depth < 1:
        raise ValueError("history_depth must be >= 1")

    store.bootstrap(entity_count)
    subject = subject_id(index)
    predicates = [fanout_predicate(i) for i in range(predicate_count)]

    # Add one current assertion for every extra live predicate through the ordinary
    # durable intent path so the fixture begins with valid v0.13/v0.14 materialization.
    for position, predicate in enumerate(predicates[1:], start=1):
        store.enqueue_predicate_addition(
            index,
            predicate,
            value=100 + position,
            writer="fanout-setup",
        )
        store.drain_all()

    # Add older canonical versions directly. Their recorded_seq is strictly below
    # every current head, so they enlarge H without changing current materialization.
    if history_depth > 1:
        with store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for position, predicate in enumerate(predicates):
                for depth in range(1, history_depth):
                    eid = f"history-evidence-{index:06d}-{position:03d}-{depth:04d}"
                    aid = f"history-assertion-{index:06d}-{position:03d}-{depth:04d}"
                    seq = -(position * 1_000_000 + depth)
                    value = 10 + position - depth
                    payload = (
                        f"History subject {index:06d} {predicate.replace('_', ' ')} "
                        f"was day {value} at version {depth}."
                    )
                    conn.execute(
                        """INSERT INTO evidence
                           (id,payload,source_id,recorded_seq,source_event_time,scope,lifecycle)
                           VALUES (?,?,?,?,?,'default','active')""",
                        (eid, payload, "source-history-v014", seq, value),
                    )
                    conn.execute(
                        """INSERT INTO assertions
                           (id,subject_id,predicate,object_json,recorded_seq,valid_from,valid_to,
                            modality,polarity,extraction_version)
                           VALUES (?,?,?,?,?,?,NULL,'asserted','positive','oracle-v1')""",
                        (aid, subject, predicate, _json(value), seq, value),
                    )
                    conn.execute(
                        "INSERT INTO assertion_evidence(assertion_id,evidence_id) VALUES (?,?)",
                        (aid, eid),
                    )
            conn.commit()

    if not store.materialization_matches_clean_rebuild():
        raise AssertionError("fanout fixture is not equivalent to a clean current-state rebuild")
    if isinstance(store, HeadIndexedPredicateStore) and not store.head_index_matches_canonical():
        raise AssertionError("head index drifted while adding irrelevant historical versions")
    return predicates


def _run_profile_rebuild_case(
    store_cls: StoreType,
    *,
    entity_count: int,
    predicate_count: int,
    history_depth: int,
    index: int | None = None,
) -> dict[str, Any]:
    chosen = entity_count // 2 if index is None else index
    with tempfile.TemporaryDirectory(prefix="dic-v014-fanout-") as tmp:
        store = store_cls(Path(tmp) / "memory.sqlite3")
        predicates = _seed_subject_fanout(
            store,
            entity_count=entity_count,
            index=chosen,
            predicate_count=predicate_count,
            history_depth=history_depth,
        )
        subject = subject_id(chosen)

        store.enqueue_operation("replace_evidence_payload", chosen, writer="fanout-measure")
        promoted = store.promote_next()
        if promoted is None or promoted.get("status") != "active":
            raise AssertionError(f"fanout measurement intent failed promotion: {promoted}")
        trace = store.recover()

        profile = store.profile_snapshot(subject)
        if profile is None:
            raise AssertionError("subject profile disappeared during fanout measurement")

        head_trace = (
            store.subject_head_trace()
            if isinstance(store, HeadIndexedPredicateStore)
            else {"head_refresh_queries": 0, "head_rows_read": 0, "head_rows_written": 0, "logical_work": 0}
        )
        head_ok = (
            store.head_index_matches_canonical()
            if isinstance(store, HeadIndexedPredicateStore)
            else None
        )
        lookup_indexed = (
            store.head_lookup_uses_index(subject)
            if isinstance(store, HeadIndexedPredicateStore)
            else None
        )
        refresh_indexed = (
            store.head_refresh_uses_index(subject)
            if isinstance(store, HeadIndexedPredicateStore)
            else None
        )

        base_work = trace.logical_work
        total_work = base_work + int(head_trace["logical_work"])
        return {
            "store": store_cls.__name__,
            "entity_count": entity_count,
            "subject_index": chosen,
            "predicate_count": predicate_count,
            "history_depth": history_depth,
            "subject_id": subject,
            "profile_predicates": list(profile.get("predicates", [])),
            "profile_predicate_count": len(profile.get("predicates", [])),
            "base_recovery_trace": trace.to_dict(),
            "head_trace": head_trace,
            "base_recovery_work": base_work,
            "total_recovery_work": total_work,
            "full_rebuild_work": store.full_rebuild_work(),
            "materialization_equal": store.materialization_matches_clean_rebuild(),
            "all_derived_fresh": store.all_derived_fresh(),
            "queue_final": store.queue_counts(),
            "head_index_equal": head_ok,
            "head_lookup_uses_index": lookup_indexed,
            "head_refresh_uses_index": refresh_indexed,
            "expected_predicates": predicates,
        }


def run_v013_history_control(
    entity_count: int = 128,
    predicate_count: int = 8,
    history_depth: int = 8,
) -> dict[str, Any]:
    return _run_profile_rebuild_case(
        PredicateSchemaAwareStore,
        entity_count=entity_count,
        predicate_count=predicate_count,
        history_depth=history_depth,
    )


def run_v014_head_index_case(
    entity_count: int = 128,
    predicate_count: int = 8,
    history_depth: int = 8,
) -> dict[str, Any]:
    return _run_profile_rebuild_case(
        HeadIndexedPredicateStore,
        entity_count=entity_count,
        predicate_count=predicate_count,
        history_depth=history_depth,
    )


def run_head_fallback_case(
    entity_count: int = 64,
    index: int = 30,
    history_depth: int = 4,
) -> dict[str, Any]:
    """Move/delete a current head and require local fallback to historical truth."""

    with tempfile.TemporaryDirectory(prefix="dic-v014-head-fallback-") as tmp:
        store = HeadIndexedPredicateStore(Path(tmp) / "memory.sqlite3")
        _seed_subject_fanout(
            store,
            entity_count=entity_count,
            index=index,
            predicate_count=2,
            history_depth=history_depth,
        )
        subject = subject_id(index)

        with store.connect() as conn:
            fallback = conn.execute(
                """SELECT id FROM assertions INDEXED BY idx_assertions_subject_predicate
                   WHERE subject_id=? AND predicate='deadline' AND id!=?
                   ORDER BY recorded_seq DESC,id DESC LIMIT 1""",
                (subject, assertion_id(index)),
            ).fetchone()
            if fallback is None:
                raise AssertionError("fallback fixture requires historical deadline assertion")
            fallback_id = str(fallback["id"])

        moved = store.enqueue_predicate_change(
            index,
            "renamed_deadline",
            new_value=77,
            writer="head-fallback-move",
        )
        promoted = store.promote_next()
        if promoted is None or promoted.get("intent_id") != moved["intent_id"]:
            raise AssertionError("head fallback move failed promotion")
        move_trace = store.recover()
        after_move = store.head_snapshot(subject)
        move_head_trace = store.subject_head_trace()
        move_equal = store.materialization_matches_clean_rebuild()
        move_head_equal = store.head_index_matches_canonical()

        store.enqueue_operation("delete_assertion", index, writer="head-fallback-delete")
        delete_result = store.drain_all()
        after_delete = store.head_snapshot(subject)

        return {
            "subject_id": subject,
            "fallback_assertion_id": fallback_id,
            "after_move": after_move,
            "after_delete": after_delete,
            "move_base_work": move_trace.logical_work,
            "move_head_trace": move_head_trace,
            "move_total_work": move_trace.logical_work + int(move_head_trace["logical_work"]),
            "move_materialization_equal": move_equal,
            "move_head_index_equal": move_head_equal,
            "delete_queue_final": store.queue_counts(),
            "delete_drain": delete_result,
            "delete_materialization_equal": store.materialization_matches_clean_rebuild(),
            "delete_head_index_equal": store.head_index_matches_canonical(),
            "final_profile_predicates": list((store.profile_snapshot(subject) or {}).get("predicates", [])),
            "deadline_context_present": store.read_context(subject, "deadline") is not None,
            "renamed_context_present": store.read_context(subject, "renamed_deadline") is not None,
            "head_lookup_uses_index": store.head_lookup_uses_index(subject),
            "head_refresh_uses_index": store.head_refresh_uses_index(subject),
        }
