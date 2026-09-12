from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from storage.recyclable_retirement_descriptor_primary import (
    RecyclableRetirementDescriptorPrimaryStore,
)

SEGMENT_BUDGET = 2


def _fail(message: str, **context: Any) -> None:
    raise AssertionError(
        f"{message}; observed={json.dumps(context, sort_keys=True, default=str)}"
    )


def _drain(store: RecyclableRetirementDescriptorPrimaryStore) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    while store.retirement_queue_snapshot()["queue_count"]:
        trace = store.reclaim_step(budget=SEGMENT_BUDGET)
        if trace.reclaimed_segments > SEGMENT_BUDGET:
            _fail("v0.36 control reclaim exceeded segment budget", trace=trace.to_dict())
        rows.append(trace.to_dict())
    return rows


def _real_tail_control(key_count: int, expected_pool: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"dic-v036-tail-control-{key_count}-") as tmp:
        path = Path(tmp) / "primary.pages"
        store = RecyclableRetirementDescriptorPrimaryStore(str(path))
        store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=4096)

        enqueues: list[dict[str, Any]] = []
        for index in range(key_count):
            trace = store.insert(f"k-{index:03d}")
            if trace.retirement_descriptors_enqueued:
                enqueues.append(trace.to_dict())
        before_drain = store.retirement_queue_snapshot()
        if int(before_drain["descriptor_pool_count"]) != expected_pool:
            _fail(
                "v0.36 control descriptor pool depth drifted",
                key_count=key_count,
                expected_pool=expected_pool,
                snapshot=before_drain,
                enqueues=enqueues,
            )

        reclaim_rows = _drain(store)
        drained = store.retirement_queue_snapshot()
        if int(drained["descriptor_free_count"]) != expected_pool:
            _fail(
                "v0.36 control did not drain descriptors to free list",
                key_count=key_count,
                expected_pool=expected_pool,
                snapshot=drained,
            )
        if not drained["free_descriptors"]:
            _fail("v0.36 control has no free-list head", key_count=key_count, snapshot=drained)

        meta = store.meta_snapshot()
        frontier = int(meta["next_physical_page"])
        free_layout: list[dict[str, Any]] = []
        for descriptor in drained["free_descriptors"]:
            page = int(descriptor["descriptor_page"])
            end_page = page + 2
            suffix_pages = frontier - end_page
            free_layout.append(
                {
                    "descriptor_page": page,
                    "descriptor_incarnation": int(descriptor["descriptor_incarnation"]),
                    "descriptor_end_page": end_page,
                    "committed_suffix_pages": suffix_pages,
                    "is_physical_tail": end_page == frontier,
                }
            )

        all_buried = all(int(row["committed_suffix_pages"]) > 0 for row in free_layout)
        if not all_buried:
            _fail(
                "v0.36 found a free descriptor at or beyond the committed physical tail",
                key_count=key_count,
                frontier=frontier,
                free_layout=free_layout,
                snapshot=drained,
            )

        head = free_layout[0]
        head_page = int(head["descriptor_page"])
        head_incarnation = int(head["descriptor_incarnation"])
        descriptor_end = int(head["descriptor_end_page"])
        suffix_pages = int(head["committed_suffix_pages"])
        eligible = bool(head["is_physical_tail"])

        # The candidate under falsification is intentionally strict: with no scan,
        # relocation, segregated arena, or alternate allocator, only a current free
        # head already at the committed file tail could be truncated. The full free
        # chain is traversed only by this diagnostic measurement, not by the candidate.
        if eligible:
            _fail(
                "v0.36 real descriptor unexpectedly landed at physical tail",
                key_count=key_count,
                head=head,
                meta=meta,
                snapshot=drained,
            )

        return {
            "key_count": key_count,
            "expected_descriptor_pool": expected_pool,
            "enqueue_count": len(enqueues),
            "enqueue_traces": enqueues,
            "queue_before_drain": before_drain,
            "reclaim_steps": reclaim_rows,
            "drained_queue": drained,
            "free_head_page": head_page,
            "free_head_incarnation": head_incarnation,
            "free_head_end_page": descriptor_end,
            "committed_physical_frontier_page": frontier,
            "committed_suffix_pages_after_free_head": suffix_pages,
            "free_head_is_physical_tail": eligible,
            "head_only_tail_release_possible": eligible,
            "free_descriptor_layout_diagnostic": free_layout,
            "all_free_descriptors_buried_below_tail": all_buried,
            "minimum_committed_suffix_pages_after_free_descriptor": min(
                int(row["committed_suffix_pages"]) for row in free_layout
            ),
            "diagnostic_free_chain_traversal_is_not_candidate_work": True,
            "descriptor_history_walks_required_by_control": 0,
            "physical_pages_released_by_control": 0,
        }


def run_tail_release_falsification() -> dict[str, Any]:
    single = _real_tail_control(17, 1)
    peak_three = _real_tail_control(65, 3)
    if single["head_only_tail_release_possible"] or peak_three["head_only_tail_release_possible"]:
        raise AssertionError("v0.36 head-only tail-release candidate unexpectedly survived")
    if not single["all_free_descriptors_buried_below_tail"]:
        raise AssertionError("v0.36 single-descriptor layout was not buried")
    if not peak_three["all_free_descriptors_buried_below_tail"]:
        raise AssertionError("v0.36 three-descriptor layout contained an unexpected free tail pair")
    return {
        "single_descriptor_case": single,
        "three_descriptor_peak_case": peak_three,
        "candidate": "release only the current FREE descriptor head when head_page + 2 == committed next_physical_page",
        "candidate_history_walks": 0,
        "candidate_relocations": 0,
        "candidate_physical_pages_released": 0,
        "falsified": True,
        "reason": (
            "real append-local placement leaves committed physical pages above every free retirement-descriptor "
            "pair observed after drain; in particular, the current free-list head is never the file tail in "
            "the one-descriptor or three-descriptor real cases"
        ),
        "next_requirement": (
            "physical descriptor capacity reduction needs a mechanism that changes or escapes the current "
            "interleaved placement, such as descriptor segregation, relocation, or a broader allocator able "
            "to reuse buried descriptor pairs; maintained tail-addressable metadata alone cannot truncate a "
            "pair while committed pages remain above it"
        ),
    }
