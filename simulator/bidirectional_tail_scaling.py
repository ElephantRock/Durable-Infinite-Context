from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from simulator.bidirectional_tail_unlink import _next_key
from storage.bidirectional_retirement_descriptor_primary import (
    BidirectionalRetirementDescriptorPrimaryStore,
)

SETUP_RECLAIM_BUDGET = 1_000_000
TARGET_DESCRIPTOR_COUNTS = (3, 4, 5, 6)


def _insert_until_target(
    store: BidirectionalRetirementDescriptorPrimaryStore,
    *,
    index: int,
    target: int,
    cap: int = 65536,
) -> int:
    # Track queue growth from bounded foreground traces instead of traversing the
    # diagnostic queue snapshot after every insert. Snapshot traversal remains setup
    # validation only and is excluded from the measured shrink work.
    count = int(store.retirement_queue_snapshot()["queue_count"])
    while count < target:
        if index >= cap:
            raise AssertionError(f"v0.39 scaling did not reach queue count {target}")
        trace = store.insert(_next_key(index))
        count += int(trace.retirement_descriptors_enqueued)
        index += 1
    if count != target:
        raise AssertionError("v0.39 scaling overshot requested queue count")
    snapshot = store.retirement_queue_snapshot()
    if int(snapshot["queue_count"]) != target:
        raise AssertionError("v0.39 scaling trace-derived queue count drifted")
    return index


def _reclaim_to_one(store: BidirectionalRetirementDescriptorPrimaryStore) -> None:
    while int(store.retirement_queue_snapshot()["queue_count"]) > 1:
        trace = store.reclaim_step(budget=SETUP_RECLAIM_BUDGET)
        if int(trace.retirement_descriptors_scanned) != 0:
            raise AssertionError("v0.39 scaling setup scanned descriptor history")


def _case(target_count: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"dic-v039-scale-{target_count}-") as tmp:
        path = Path(tmp) / "primary.pages"
        store = BidirectionalRetirementDescriptorPrimaryStore(str(path))
        store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=4096)

        index = _insert_until_target(store, index=0, target=target_count)
        initial = store.retirement_queue_snapshot()
        expected_pages = list(range(0, 2 * target_count, 2))
        observed_pages = [int(row["descriptor_page"]) for row in initial["descriptors"]]
        if observed_pages != expected_pages:
            raise AssertionError(
                f"v0.39 scaling initial descriptor layout drifted: {observed_pages}"
            )

        physical_tail = expected_pages[-1]
        _reclaim_to_one(store)
        one_live = store.retirement_queue_snapshot()
        if int(one_live["head_page"]) != physical_tail:
            raise AssertionError("v0.39 scaling did not leave physical tail live before reuse")
        if int(one_live["descriptor_free_count"]) != target_count - 1:
            raise AssertionError("v0.39 scaling initial free count drifted")

        index = _insert_until_target(store, index=index, target=target_count)
        reused = store.retirement_queue_snapshot()
        expected_reuse = list(reversed(expected_pages))
        observed_reuse = [int(row["descriptor_page"]) for row in reused["descriptors"]]
        if observed_reuse != expected_reuse:
            raise AssertionError(
                f"v0.39 scaling reuse order drifted: {observed_reuse}"
            )

        _reclaim_to_one(store)
        buried = store.retirement_queue_snapshot()
        expected_free = expected_pages[1:]
        observed_free = [int(row["descriptor_page"]) for row in buried["free_descriptors"]]
        if observed_free != expected_free:
            raise AssertionError(
                f"v0.39 scaling buried free chain drifted: {observed_free}"
            )
        if int(buried["head_page"]) != 0:
            raise AssertionError("v0.39 scaling did not retain page 0 as live queue head")
        if int(buried["descriptor_free_count"]) != target_count - 1:
            raise AssertionError("v0.39 scaling buried free count drifted")
        tail_row = buried["free_descriptors"][-1]
        if int(tail_row["descriptor_page"]) != physical_tail:
            raise AssertionError("v0.39 scaling physical tail is not last free node")
        expected_predecessor = physical_tail - 2
        if int(tail_row.get("prev_descriptor_page", -1)) != expected_predecessor:
            raise AssertionError("v0.39 scaling tail predecessor authority drifted")

        trace = store.shrink_retirement_arena_tail_step()
        after = store.retirement_queue_snapshot()
        if not trace.released or trace.tail_was_free_head:
            raise AssertionError("v0.39 scaling did not release buried physical tail")
        if int(trace.predecessor_page or -1) != expected_predecessor:
            raise AssertionError("v0.39 scaling did not use direct tail predecessor")
        if int(trace.retirement_descriptor_preads) != 4:
            raise AssertionError("v0.39 scaling read count depends on free-chain length")
        if int(trace.retirement_descriptor_pwrites) != 1:
            raise AssertionError("v0.39 scaling write count depends on free-chain length")
        if int(trace.retirement_descriptors_scanned) != 0:
            raise AssertionError("v0.39 scaling shrink scanned free-list/history")
        if int(trace.candidate_relocations) != 0:
            raise AssertionError("v0.39 scaling shrink relocated live descriptors")
        if int(after["descriptor_arena_pages"]) != 2 * (target_count - 1):
            raise AssertionError("v0.39 scaling committed frontier did not shrink by one pair")
        if int(after["queue_count"]) != 1:
            raise AssertionError("v0.39 scaling shrink changed live queue depth")

        return {
            "target_descriptor_count": target_count,
            "keys_inserted": index,
            "free_chain_length_before": target_count - 1,
            "physical_tail_page": physical_tail,
            "predecessor_page": expected_predecessor,
            "arena_pages_before": 2 * target_count,
            "arena_pages_after": 2 * (target_count - 1),
            "retirement_descriptor_preads": int(trace.retirement_descriptor_preads),
            "retirement_descriptor_pwrites": int(trace.retirement_descriptor_pwrites),
            "retirement_descriptors_scanned": int(trace.retirement_descriptors_scanned),
            "candidate_relocations": int(trace.candidate_relocations),
        }


def run_bidirectional_tail_scaling_experiment() -> dict[str, Any]:
    rows = [_case(count) for count in TARGET_DESCRIPTOR_COUNTS]
    return {
        "target_descriptor_counts": list(TARGET_DESCRIPTOR_COUNTS),
        "rows": rows,
        "all_constant_shrink_work": all(
            int(row["retirement_descriptor_preads"]) == 4
            and int(row["retirement_descriptor_pwrites"]) == 1
            and int(row["retirement_descriptors_scanned"]) == 0
            and int(row["candidate_relocations"]) == 0
            for row in rows
        ),
    }
