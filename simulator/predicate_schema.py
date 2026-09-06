from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Type

from simulator.cascade import subject_id
from storage.predicate_schema import (
    PredicateMutationControlStore,
    PredicateSchemaAwareStore,
)


StoreType = Type[PredicateMutationControlStore]


def _run_predicate_replacement_case(
    store_cls: StoreType,
    *,
    entity_count: int = 64,
    index: int = 40,
    new_predicate: str = "launch_date",
    new_value: int = 55,
) -> dict[str, Any]:
    if index >= entity_count:
        raise ValueError("index must refer to an existing bootstrap assertion")

    subject = subject_id(index)
    with tempfile.TemporaryDirectory(prefix="dic-v013-predicate-replace-") as tmp:
        store = store_cls(Path(tmp) / "memory.sqlite3")
        store.bootstrap(entity_count)

        if store.read_context(subject, "deadline") is None:
            raise AssertionError("predicate control requires bootstrap deadline context")
        if store.read_context(subject, new_predicate) is not None:
            raise AssertionError("new predicate unexpectedly materialized before mutation")

        intent = store.enqueue_predicate_change(
            index,
            new_predicate,
            new_value=new_value,
            writer="predicate-replacement",
        )
        promoted = store.promote_next()
        if promoted is None or promoted["intent_id"] != intent["intent_id"]:
            raise AssertionError("predicate-change intent was not promoted")

        trace = store.recover()
        profile = store.profile_snapshot(subject)
        queue = store.queue_counts()
        canonical_changed = store.canonical_predicate(index) == new_predicate
        new_context = store.read_context(subject, new_predicate)
        old_context = store.read_context(subject, "deadline")

        return {
            "store": store_cls.__name__,
            "entity_count": entity_count,
            "index": index,
            "subject_id": subject,
            "old_predicate": "deadline",
            "new_predicate": new_predicate,
            "canonical_changed": canonical_changed,
            "new_context_present": new_context is not None,
            "old_context_retired": old_context is None,
            "profile_present": profile is not None,
            "profile_predicates": [] if profile is None else list(profile.get("predicates", [])),
            "subject_derived_count": store.subject_derived_count(subject),
            "queue_final": queue,
            "semantic_check": canonical_changed and queue["done"] == 1 and queue["conflict"] == 0,
            "materialization_equal": store.materialization_matches_clean_rebuild(),
            "recovery_work": trace.logical_work,
            "full_rebuild_work": store.full_rebuild_work(),
            "all_derived_fresh": store.all_derived_fresh(),
            "profile_lookup_uses_index": (
                store.subject_profile_lookup_uses_index(subject)
                if isinstance(store, PredicateSchemaAwareStore)
                else None
            ),
        }


def run_v012_predicate_control(
    entity_count: int = 64,
    index: int = 40,
) -> dict[str, Any]:
    """Expose v0.12's hard-coded deadline interpretation of a subject-only profile."""

    return _run_predicate_replacement_case(
        PredicateMutationControlStore,
        entity_count=entity_count,
        index=index,
    )


def run_v013_predicate_replacement(
    entity_count: int = 64,
    index: int = 40,
) -> dict[str, Any]:
    """Rebuild the subject-only profile from the subject's actual predicate set."""

    return _run_predicate_replacement_case(
        PredicateSchemaAwareStore,
        entity_count=entity_count,
        index=index,
    )


def run_v013_predicate_addition(
    entity_count: int = 64,
    index: int = 40,
    new_predicate: str = "launch_date",
    value: int = 55,
) -> dict[str, Any]:
    """Require one subject profile to aggregate two simultaneously live predicates."""

    if index >= entity_count:
        raise ValueError("index must refer to an existing bootstrap assertion")

    subject = subject_id(index)
    with tempfile.TemporaryDirectory(prefix="dic-v013-predicate-add-") as tmp:
        store = PredicateSchemaAwareStore(Path(tmp) / "memory.sqlite3")
        store.bootstrap(entity_count)
        admitted = store.enqueue_predicate_addition(
            index,
            new_predicate,
            value=value,
            writer="predicate-addition",
        )
        drained = store.drain_all()
        profile = store.profile_snapshot(subject)

        with store.connect() as conn:
            added_assertion_present = (
                conn.execute(
                    "SELECT 1 FROM assertions WHERE id=?",
                    (admitted["assertion_id"],),
                ).fetchone()
                is not None
            )

        return {
            "store": type(store).__name__,
            "entity_count": entity_count,
            "index": index,
            "subject_id": subject,
            "new_predicate": new_predicate,
            "added_assertion_present": added_assertion_present,
            "deadline_context_present": store.read_context(subject, "deadline") is not None,
            "new_context_present": store.read_context(subject, new_predicate) is not None,
            "profile_present": profile is not None,
            "profile_predicates": [] if profile is None else list(profile.get("predicates", [])),
            "subject_derived_count": store.subject_derived_count(subject),
            "queue_final": store.queue_counts(),
            "drain": drained,
            "materialization_equal": store.materialization_matches_clean_rebuild(),
            "full_rebuild_work": store.full_rebuild_work(),
            "all_derived_fresh": store.all_derived_fresh(),
            "profile_lookup_uses_index": store.subject_profile_lookup_uses_index(subject),
        }


def run_predicate_locality_case(entity_count: int) -> dict[str, Any]:
    index = max(5, entity_count // 2)
    if index >= entity_count:
        index = entity_count - 1
    return run_v013_predicate_replacement(entity_count, index)
