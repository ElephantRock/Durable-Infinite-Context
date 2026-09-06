from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Type

from simulator.compositional_profile import (
    _accumulate_trace,
    _enqueue_assertion_delete,
    _enqueue_evidence_update,
    evidence_for_position,
    run_v015_compositional_case,
)
from simulator.subject_fanout import _seed_subject_fanout
from simulator.cascade import subject_id
from state.cascade import profile_node
from storage.compositional_profile import CompositionalProfileStore
from storage.normalized_membership import NormalizedPredicateMembershipStore
from storage.sqlite_recovery import PersistentRecoveryTrace
from storage.subject_heads import HeadIndexedPredicateStore


StoreType = Type[HeadIndexedPredicateStore]


def _profile_storage_bytes(store: HeadIndexedPredicateStore, subject: str) -> int:
    with store.connect() as conn:
        row = conn.execute(
            "SELECT LENGTH(value_json) AS n FROM derived_nodes WHERE node_id=?",
            (profile_node(subject),),
        ).fetchone()
        return 0 if row is None else int(row["n"])


def _drain_measured(store: HeadIndexedPredicateStore) -> dict[str, Any]:
    total = PersistentRecoveryTrace()
    head_work = 0
    membership_work = 0
    membership_rows_written = 0
    membership_bytes_written = 0
    membership_probes = 0
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
        if promoted["status"] in {"active", "busy"}:
            if promoted["status"] == "active":
                promotions += 1
            part = store.recover()
            _accumulate_trace(total, part)
            head_work += int(store.subject_head_trace()["logical_work"])
            if isinstance(store, NormalizedPredicateMembershipStore):
                mtrace = store.membership_mutation_trace()
                membership_work += int(mtrace["logical_work"])
                membership_rows_written += int(mtrace["rows_written"])
                membership_bytes_written += int(mtrace["bytes_written"])
                membership_probes += int(mtrace["probes"])
            rounds += 1
            continue
        raise AssertionError(promoted)
    return {
        "promotions": promotions,
        "conflicts": conflicts,
        "recovery_rounds": rounds,
        "base_trace": total.to_dict(),
        "head_work": head_work,
        "membership_work": membership_work,
        "membership_rows_written": membership_rows_written,
        "membership_bytes_written": membership_bytes_written,
        "membership_probes": membership_probes,
        "total_work": total.logical_work + head_work + membership_work,
        "queue_final": store.queue_counts(),
    }


def run_v016_normalized_case(
    entity_count: int = 128,
    predicate_count: int = 32,
    history_depth: int = 8,
    changed_count: int = 1,
) -> dict[str, Any]:
    if changed_count < 1 or changed_count > predicate_count:
        raise ValueError("changed_count must be between 1 and predicate_count")
    index = entity_count // 2
    with tempfile.TemporaryDirectory(prefix="dic-v016-normalized-") as tmp:
        store = NormalizedPredicateMembershipStore(Path(tmp) / "memory.sqlite3")
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
        full = store.read_composed_profile(subject)
        partial = store.read_composed_profile(subject, changed)
        full_oracle = store.clean_composed_profile(subject)
        partial_oracle = store.clean_composed_profile(subject, changed)

        return {
            "store": type(store).__name__,
            "entity_count": entity_count,
            "subject_index": index,
            "subject_id": subject,
            "predicate_count": predicate_count,
            "history_depth": history_depth,
            "changed_count": changed_count,
            "changed_predicates": changed,
            "recovery": measured,
            "descriptor_storage_bytes": store.descriptor_storage_bytes(subject),
            "membership_count": store.subject_membership_count(subject),
            "membership_storage_bytes": store.subject_membership_bytes(subject),
            "membership_lookup_uses_index": store.membership_lookup_uses_index(subject),
            "membership_enumeration_uses_index": store.membership_enumeration_uses_index(subject),
            "membership_btree_height": store.membership_btree_height(),
            "membership_equal": store.membership_matches_heads(),
            "materialization_equal": store.materialization_matches_clean_rebuild(),
            "all_derived_fresh": store.all_derived_fresh(),
            "head_index_equal": store.head_index_matches_canonical(),
            "full_profile": full["value"],
            "full_assembly_trace": full["trace"],
            "full_assembly_equal": full["value"] == full_oracle,
            "partial_profile": partial["value"],
            "partial_assembly_trace": partial["trace"],
            "partial_assembly_equal": partial["value"] == partial_oracle,
            "full_rebuild_work": store.full_rebuild_work(),
        }


def run_v016_read_protection_case(entity_count: int = 48, index: int = 24) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v016-read-protection-") as tmp:
        store = NormalizedPredicateMembershipStore(Path(tmp) / "memory.sqlite3")
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

        unrelated = store.read_composed_profile(subject, [predicates[1]])
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
        final = store.read_composed_profile(subject)
        return {
            "unrelated_partial_present": unrelated["value"] is not None,
            "affected_partial_blocked": affected_blocked,
            "full_profile_blocked": full_blocked,
            "final_full_equal": final["value"] == store.clean_composed_profile(subject),
            "membership_equal": store.membership_matches_heads(),
            "materialization_equal": store.materialization_matches_clean_rebuild(),
        }


def _run_topology_case(
    store_cls: StoreType,
    *,
    entity_count: int,
    predicate_count: int,
    history_depth: int,
) -> dict[str, Any]:
    index = entity_count // 2
    with tempfile.TemporaryDirectory(prefix="dic-v016-topology-") as tmp:
        store = store_cls(Path(tmp) / "memory.sqlite3")
        _seed_subject_fanout(
            store,
            entity_count=entity_count,
            index=index,
            predicate_count=predicate_count,
            history_depth=history_depth,
        )
        subject = subject_id(index)
        before_bytes = _profile_storage_bytes(store, subject)

        added = store.enqueue_predicate_addition(
            index,
            "facet_added",
            value=777,
            writer="v016-topology-add",
        )
        add = _drain_measured(store)
        after_add_bytes = _profile_storage_bytes(store, subject)
        if isinstance(store, NormalizedPredicateMembershipStore):
            after_add_profile = store.read_composed_profile(subject)["value"]
            membership_after_add = store.subject_membership_count(subject)
        else:
            after_add_profile = store.profile_snapshot(subject)
            membership_after_add = None

        _enqueue_assertion_delete(store, added["assertion_id"])
        delete = _drain_measured(store)
        after_delete_bytes = _profile_storage_bytes(store, subject)
        if isinstance(store, NormalizedPredicateMembershipStore):
            after_delete_profile = store.read_composed_profile(subject)["value"]
            membership_after_delete = store.subject_membership_count(subject)
            membership_equal = store.membership_matches_heads()
        else:
            after_delete_profile = store.profile_snapshot(subject)
            membership_after_delete = None
            membership_equal = None

        return {
            "store": type(store).__name__,
            "predicate_count": predicate_count,
            "before_profile_bytes": before_bytes,
            "after_add_profile_bytes": after_add_bytes,
            "after_delete_profile_bytes": after_delete_bytes,
            "add": add,
            "delete": delete,
            "after_add_predicates": list((after_add_profile or {}).get("predicates", [])),
            "after_delete_predicates": list((after_delete_profile or {}).get("predicates", [])),
            "membership_after_add": membership_after_add,
            "membership_after_delete": membership_after_delete,
            "membership_equal": membership_equal,
            "materialization_equal": store.materialization_matches_clean_rebuild(),
            "head_index_equal": store.head_index_matches_canonical(),
            "all_derived_fresh": store.all_derived_fresh(),
        }


def run_v015_manifest_topology_control(
    entity_count: int = 128,
    predicate_count: int = 32,
    history_depth: int = 8,
) -> dict[str, Any]:
    return _run_topology_case(
        CompositionalProfileStore,
        entity_count=entity_count,
        predicate_count=predicate_count,
        history_depth=history_depth,
    )


def run_v016_normalized_topology_case(
    entity_count: int = 128,
    predicate_count: int = 32,
    history_depth: int = 8,
) -> dict[str, Any]:
    return _run_topology_case(
        NormalizedPredicateMembershipStore,
        entity_count=entity_count,
        predicate_count=predicate_count,
        history_depth=history_depth,
    )


def run_cross_version_case(
    entity_count: int = 128,
    predicate_count: int = 32,
    history_depth: int = 8,
    changed_count: int = 1,
) -> dict[str, Any]:
    v015 = run_v015_compositional_case(
        entity_count=entity_count,
        predicate_count=predicate_count,
        history_depth=history_depth,
        changed_count=changed_count,
    )
    v016 = run_v016_normalized_case(
        entity_count=entity_count,
        predicate_count=predicate_count,
        history_depth=history_depth,
        changed_count=changed_count,
    )
    return {
        "equal": v016["full_profile"] == v015["full_profile"],
        "v015_full": v015["full_profile"],
        "v016_full": v016["full_profile"],
    }
