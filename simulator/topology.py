from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Type

from simulator.cascade import assertion_id, subject_id
from storage.topology_intent import (
    PromotionRevalidatedTopologyStore,
    TopologyMutationStore,
)


StoreType = Type[TopologyMutationStore]


def _run_topology_case(
    store_cls: StoreType,
    *,
    entity_count: int = 64,
    moved_index: int = 40,
    target_index: int = 5,
) -> dict[str, Any]:
    if entity_count <= max(moved_index, target_index) + 2:
        raise ValueError("entity_count too small for topology case")
    if moved_index <= target_index:
        raise ValueError("moved assertion must have a later recorded_seq than target control")

    with tempfile.TemporaryDirectory(prefix="dic-v011-topology-") as tmp:
        db = Path(tmp) / "memory.sqlite3"
        store = store_cls(db)
        store.bootstrap(entity_count)

        moved_subject = subject_id(moved_index)
        target_subject = subject_id(target_index)
        target_key = f"{target_subject}|deadline"
        old_key = f"{moved_subject}|deadline"

        move = store.enqueue_topology_move(
            moved_index,
            target_index,
            writer="topology-writer",
        )
        evidence = store.enqueue_operation(
            "replace_evidence_payload",
            moved_index,
            writer="evidence-writer",
        )
        queue_at_admission = store.queue_snapshot()
        evidence_at_admission = next(
            row for row in queue_at_admission if row["intent_id"] == evidence["intent_id"]
        )
        admission_read_keys = list(evidence_at_admission["read_keys"])

        first_promotion = store.promote_next()
        if first_promotion is None or first_promotion["intent_id"] != move["intent_id"]:
            raise AssertionError("topology predecessor was not promoted first")
        first_trace = store.recover()

        queue_after_move = store.queue_snapshot()
        if [row["status"] for row in queue_after_move] != ["done", "queued"]:
            raise AssertionError(f"unexpected queue after topology move: {queue_after_move}")
        pre_update_context = store.read_context(target_subject)
        if pre_update_context is None:
            raise AssertionError("target context disappeared after topology move")

        second_promotion = store.promote_next()
        if second_promotion is None or second_promotion["intent_id"] != evidence["intent_id"]:
            raise AssertionError("evidence successor was not promoted second")
        queue_after_promotion = store.queue_snapshot()
        evidence_active = next(
            row for row in queue_after_promotion if row["intent_id"] == evidence["intent_id"]
        )
        promotion_read_keys = list(evidence_active["read_keys"])

        store.apply_canonical_transaction()
        stale_read_blocked = False
        stale_context = None
        try:
            stale_context = store.read_context(target_subject)
        except RuntimeError:
            stale_read_blocked = True

        unrelated_index = target_index + 1
        if unrelated_index == moved_index:
            unrelated_index += 1
        unrelated_context = store.read_context(subject_id(unrelated_index))

        second_trace = store.recover()
        final_context = store.read_context(target_subject)
        old_context = store.read_context(moved_subject)
        final_queue = store.queue_counts()
        clean = store.materialization_matches_clean_rebuild()
        with store.connect() as conn:
            moved_assertion = store._get_assertion(conn, assertion_id(moved_index))

        semantic = (
            moved_assertion is not None
            and moved_assertion.subject_id == target_subject
            and final_context is not None
            and "Nova" in final_context
            and old_context is None
            and final_queue["done"] == 2
            and final_queue["conflict"] == 0
            and clean
        )
        lookup_indexed = (
            store.evidence_read_key_lookup_uses_index(moved_index)
            if isinstance(store, PromotionRevalidatedTopologyStore)
            else None
        )

        return {
            "store": store_cls.__name__,
            "entity_count": entity_count,
            "moved_index": moved_index,
            "target_index": target_index,
            "moved_subject": moved_subject,
            "target_subject": target_subject,
            "old_read_key": old_key,
            "target_read_key": target_key,
            "admission_read_keys": admission_read_keys,
            "promotion_read_keys": promotion_read_keys,
            "read_keys_changed": admission_read_keys != promotion_read_keys,
            "pre_update_context_present": pre_update_context is not None,
            "stale_read_blocked": stale_read_blocked,
            "stale_read_admitted": (not stale_read_blocked and stale_context is not None),
            "stale_value_visible": (
                stale_context is not None
                and "Nova" not in stale_context
                and pre_update_context == stale_context
            ),
            "unrelated_read_admitted": unrelated_context is not None,
            "final_context_has_new_payload": final_context is not None and "Nova" in final_context,
            "old_subject_retired": old_context is None,
            "queue_final": final_queue,
            "semantic_check": semantic,
            "materialization_equal": clean,
            "first_recovery_work": first_trace.logical_work,
            "second_recovery_work": second_trace.logical_work,
            "total_recovery_work": first_trace.logical_work + second_trace.logical_work,
            "full_rebuild_work": store.full_rebuild_work(),
            "revalidation_lookup_uses_index": lookup_indexed,
        }


def run_v010_topology_control(
    entity_count: int = 64,
    moved_index: int = 40,
    target_index: int = 5,
) -> dict[str, Any]:
    """Expose the stale-read leak from admission-time impact metadata."""

    return _run_topology_case(
        TopologyMutationStore,
        entity_count=entity_count,
        moved_index=moved_index,
        target_index=target_index,
    )


def run_v011_topology_revalidation_case(
    entity_count: int = 64,
    moved_index: int = 40,
    target_index: int = 5,
) -> dict[str, Any]:
    """Recompute impact metadata at promotion and verify stale-read protection."""

    return _run_topology_case(
        PromotionRevalidatedTopologyStore,
        entity_count=entity_count,
        moved_index=moved_index,
        target_index=target_index,
    )


def run_topology_locality_case(entity_count: int) -> dict[str, Any]:
    moved_index = max(12, entity_count // 2)
    target_index = max(3, entity_count // 10)
    if moved_index >= entity_count - 2:
        moved_index = entity_count - 3
    if target_index >= moved_index:
        target_index = max(1, moved_index // 2)
    return run_v011_topology_revalidation_case(
        entity_count=entity_count,
        moved_index=moved_index,
        target_index=target_index,
    )
