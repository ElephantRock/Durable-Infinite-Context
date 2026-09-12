from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from storage.retirement_descriptor_pool import RETIREMENT_STATUS_FREE
from storage.trimmable_retirement_descriptor_primary import (
    TrimmableRetirementDescriptorPrimaryStore,
)

SEGMENT_BUDGET = 2


def _fail(message: str, **context: Any) -> None:
    raise AssertionError(
        f"{message}; observed={json.dumps(context, sort_keys=True, default=str)}"
    )


def _drain(store: TrimmableRetirementDescriptorPrimaryStore) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    while store.retirement_queue_snapshot()["queue_count"]:
        trace = store.reclaim_step(budget=SEGMENT_BUDGET)
        if trace.reclaimed_segments > SEGMENT_BUDGET:
            _fail("v0.36 reclaim exceeded segment budget", trace=trace.to_dict())
        rows.append(trace.to_dict())
    return rows


def run_tail_release_cycle() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v036-tail-release-") as tmp:
        path = Path(tmp) / "primary.pages"
        store = TrimmableRetirementDescriptorPrimaryStore(str(path))
        store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=4096)

        initial_enqueues: list[dict[str, Any]] = []
        for index in range(65):
            trace = store.insert(f"k-{index:03d}")
            if trace.retirement_descriptors_enqueued:
                initial_enqueues.append(trace.to_dict())
        queued = store.retirement_queue_snapshot()
        if queued["queue_count"] != 3 or queued["descriptor_pool_count"] != 3:
            _fail("v0.36 initial descriptor pool drifted", snapshot=queued)
        incarnations = [int(row["descriptor_incarnation"]) for row in queued["descriptors"]]
        if incarnations != [1, 2, 3]:
            _fail(
                "global descriptor incarnation allocation drifted",
                expected=[1, 2, 3],
                observed=incarnations,
                snapshot=queued,
            )

        reclaim_rows = _drain(store)
        drained = store.retirement_queue_snapshot()
        if drained["descriptor_free_count"] != 3 or drained["descriptor_pool_count"] != 3:
            _fail("v0.36 drain did not produce three free descriptors", snapshot=drained)
        released = drained["free_descriptors"][0]
        released_page = int(released["descriptor_page"])
        released_incarnation = int(released["descriptor_incarnation"])
        if released_incarnation != 3:
            _fail("physical-tail descriptor was not latest initial identity", released=released)

        before_meta = store.meta_snapshot()
        before_size = int(before_meta["file_size_bytes"])
        before_frontier = int(before_meta["next_physical_page"])
        if released_page + 2 != before_frontier:
            _fail(
                "free-list head is not the committed physical descriptor tail",
                released=released,
                before_meta=before_meta,
                drained=drained,
            )

        release = store.trim_retirement_descriptor_tail_step()
        released_snapshot = store.retirement_queue_snapshot()
        released_meta = store.meta_snapshot()
        if not release.tail_release_eligible or release.physical_pages_released != 2:
            _fail("eligible descriptor tail was not released", trace=release.to_dict())
        if release.retirement_descriptor_preads != 2:
            _fail("tail release read beyond one descriptor pair", trace=release.to_dict())
        if released_snapshot["descriptor_free_count"] != 2:
            _fail("tail release did not reduce free descriptor count", snapshot=released_snapshot)
        if released_snapshot["descriptor_pool_count"] != 2:
            _fail("tail release did not reduce retained descriptor pool", snapshot=released_snapshot)
        if int(released_meta["next_physical_page"]) != before_frontier - 2:
            _fail("committed physical frontier did not move back by one descriptor pair", meta=released_meta)
        if int(released_meta["file_size_bytes"]) != before_size - 2 * 4096:
            _fail("physical file did not shrink by one descriptor pair", meta=released_meta)

        second_release = store.trim_retirement_descriptor_tail_step()
        after_second_release = store.retirement_queue_snapshot()
        if second_release.tail_release_eligible:
            _fail(
                "second release unexpectedly found a contiguous descriptor tail",
                trace=second_release.to_dict(),
                snapshot=after_second_release,
            )
        if second_release.retirement_descriptor_preads != 2:
            _fail("blocked release did not remain head-local", trace=second_release.to_dict())
        if after_second_release["descriptor_pool_count"] != 2:
            _fail("blocked release changed retained pool", snapshot=after_second_release)

        stale_after_release_rejected = False
        stale_after_release_error: str | None = None
        try:
            store.retirement_descriptor_reference(
                released_page,
                released_incarnation,
                expected_status=RETIREMENT_STATUS_FREE,
            )
        except RuntimeError as exc:
            stale_after_release_rejected = True
            stale_after_release_error = str(exc)
        if not stale_after_release_rejected:
            _fail(
                "released descriptor identity remained resolvable",
                page=released_page,
                incarnation=released_incarnation,
            )

        post_release_enqueues: list[dict[str, Any]] = []
        enqueue_indices: list[int] = []
        for index in range(65, 513):
            trace = store.insert(f"k-{index:03d}")
            if trace.retirement_descriptors_enqueued:
                post_release_enqueues.append(trace.to_dict())
                enqueue_indices.append(index)
        after_regrowth = store.retirement_queue_snapshot()
        if enqueue_indices != [128, 256, 512]:
            _fail(
                "v0.36 post-release migration schedule drifted",
                expected=[128, 256, 512],
                observed=enqueue_indices,
                enqueues=post_release_enqueues,
            )
        if [int(row["retirement_descriptor_reuses"]) for row in post_release_enqueues] != [1, 1, 0]:
            _fail("v0.36 expected two reuses then one fresh descriptor", enqueues=post_release_enqueues)
        if [int(row["retirement_descriptor_pages_appended"]) for row in post_release_enqueues] != [0, 0, 2]:
            _fail("v0.36 descriptor append sequence drifted", enqueues=post_release_enqueues)
        generations = [int(row["generation"]) for row in after_regrowth["descriptors"]]
        regrown_incarnations = [
            int(row["descriptor_incarnation"]) for row in after_regrowth["descriptors"]
        ]
        if generations != [3, 4, 5]:
            _fail("v0.36 queue generation order drifted", snapshot=after_regrowth)
        if regrown_incarnations != [4, 5, 6]:
            _fail(
                "global incarnation counter reset after physical release",
                expected=[4, 5, 6],
                observed=regrown_incarnations,
                snapshot=after_regrowth,
            )
        if after_regrowth["descriptor_pool_count"] != 3:
            _fail("descriptor pool did not regrow when live demand returned to three", snapshot=after_regrowth)

        stale_after_regrowth_rejected = False
        stale_after_regrowth_error: str | None = None
        try:
            store.retirement_descriptor_reference(
                released_page,
                released_incarnation,
                expected_status=RETIREMENT_STATUS_FREE,
            )
        except RuntimeError as exc:
            stale_after_regrowth_rejected = True
            stale_after_regrowth_error = str(exc)
        if not stale_after_regrowth_rejected:
            _fail(
                "released stale identity resolved after physical frontier regrowth",
                page=released_page,
                incarnation=released_incarnation,
                snapshot=after_regrowth,
            )

        for index in range(513):
            lookup = store.lookup(f"k-{index:03d}")
            if not lookup.found:
                _fail("v0.36 lost a live key", missing=index, lookup=lookup.to_dict())

        return {
            "initial_enqueues": initial_enqueues,
            "initial_queue": queued,
            "initial_reclaim_steps": reclaim_rows,
            "drained_queue": drained,
            "before_release_meta": before_meta,
            "tail_release_trace": release.to_dict(),
            "after_release_queue": released_snapshot,
            "after_release_meta": released_meta,
            "second_release_trace": second_release.to_dict(),
            "after_second_release_queue": after_second_release,
            "released_identity": {
                "descriptor_page": released_page,
                "descriptor_incarnation": released_incarnation,
                "rejected_immediately_after_release": stale_after_release_rejected,
                "immediate_error": stale_after_release_error,
                "rejected_after_frontier_regrowth": stale_after_regrowth_rejected,
                "regrowth_error": stale_after_regrowth_error,
            },
            "post_release_enqueue_indices": enqueue_indices,
            "post_release_enqueues": post_release_enqueues,
            "after_regrowth_queue": after_regrowth,
            "descriptor_pool_after_peak_release": int(released_snapshot["descriptor_pool_count"]),
            "descriptor_pool_after_live_demand_returns": int(after_regrowth["descriptor_pool_count"]),
            "released_physical_pages": int(release.physical_pages_released),
            "blocked_non_tail_release_is_head_local": True,
            "all_513_keys_visible": True,
        }
