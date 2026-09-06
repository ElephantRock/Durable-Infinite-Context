from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Type

from simulator.cascade import assertion_id, subject_id
from storage.growth_intent import GrowthAwareTopologyStore
from storage.topology_intent import PromotionRevalidatedTopologyStore


StoreType = Type[PromotionRevalidatedTopologyStore]


def _subject_derived_count(store: PromotionRevalidatedTopologyStore, subject: str) -> int:
    with store.connect() as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM derived_nodes WHERE subject_id=?",
                (subject,),
            ).fetchone()[0]
        )


def _run_growth_case(
    store_cls: StoreType,
    *,
    entity_count: int = 64,
    moved_index: int = 40,
    target_offset: int = 17,
) -> dict[str, Any]:
    if moved_index >= entity_count:
        raise ValueError("moved_index must refer to an existing bootstrap assertion")

    target_index = entity_count + target_offset
    moved_subject = subject_id(moved_index)
    target_subject = subject_id(target_index)

    with tempfile.TemporaryDirectory(prefix="dic-v012-growth-") as tmp:
        db = Path(tmp) / "memory.sqlite3"
        store = store_cls(db)
        store.bootstrap(entity_count)

        if _subject_derived_count(store, target_subject) != 0:
            raise AssertionError("growth control target unexpectedly has derived materialization")
        if store.read_context(target_subject) is not None:
            raise AssertionError("growth control target unexpectedly has context")

        intent = store.enqueue_topology_move(
            moved_index,
            target_index,
            writer="growth-writer",
        )
        promoted = store.promote_next()
        if promoted is None or promoted["intent_id"] != intent["intent_id"]:
            raise AssertionError("growth intent was not promoted")

        trace = store.recover()

        with store.connect() as conn:
            assertion = store._get_assertion(conn, assertion_id(moved_index))
        target_context = store.read_context(target_subject)
        old_context = store.read_context(moved_subject)
        target_derived_count = _subject_derived_count(store, target_subject)
        clean = store.materialization_matches_clean_rebuild()
        queue = store.queue_counts()

        canonical_moved = assertion is not None and assertion.subject_id == target_subject
        semantic = canonical_moved and queue["done"] == 1 and queue["conflict"] == 0

        return {
            "store": store_cls.__name__,
            "entity_count": entity_count,
            "moved_index": moved_index,
            "target_index": target_index,
            "moved_subject": moved_subject,
            "target_subject": target_subject,
            "canonical_moved": canonical_moved,
            "target_context_present": target_context is not None,
            "target_derived_count": target_derived_count,
            "old_subject_retired": old_context is None,
            "queue_final": queue,
            "semantic_check": semantic,
            "materialization_equal": clean,
            "recovery_work": trace.logical_work,
            "full_rebuild_work": store.full_rebuild_work(),
            "all_derived_fresh": store.all_derived_fresh(),
        }


def run_v011_growth_control(
    entity_count: int = 64,
    moved_index: int = 40,
) -> dict[str, Any]:
    """Expose canonical topology growth without local derived-node creation."""

    return _run_growth_case(
        PromotionRevalidatedTopologyStore,
        entity_count=entity_count,
        moved_index=moved_index,
    )


def run_v012_growth_creation_case(
    entity_count: int = 64,
    moved_index: int = 40,
) -> dict[str, Any]:
    """Create missing target materializations locally and require rebuild parity."""

    return _run_growth_case(
        GrowthAwareTopologyStore,
        entity_count=entity_count,
        moved_index=moved_index,
    )


def run_growth_locality_case(entity_count: int) -> dict[str, Any]:
    moved_index = max(5, entity_count // 2)
    if moved_index >= entity_count:
        moved_index = entity_count - 1
    return run_v012_growth_creation_case(entity_count, moved_index)
