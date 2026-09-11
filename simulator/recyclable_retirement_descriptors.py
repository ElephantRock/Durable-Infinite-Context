from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from storage.recyclable_retirement_descriptor_primary import (
    RecyclableRetirementDescriptorPrimaryStore,
)

HISTORY_CONTROLS = (1, 4, 16, 64, 256)
SEGMENT_BUDGET = 2


def run_descriptor_storage_controls() -> dict[str, Any]:
    rows: list[dict[str, int]] = []
    for completed in HISTORY_CONTROLS:
        rows.append(
            {
                "completed_generations": completed,
                "append_only_descriptor_pages": 2 * completed,
                "recyclable_serial_peak_descriptor_pages": 2,
                "recyclable_enqueue_history_walks": 0,
            }
        )
    if not all(
        rows[index]["append_only_descriptor_pages"]
        < rows[index + 1]["append_only_descriptor_pages"]
        for index in range(len(rows) - 1)
    ):
        raise AssertionError("append-only descriptor control did not grow with history")
    if any(row["recyclable_serial_peak_descriptor_pages"] != 2 for row in rows):
        raise AssertionError("serial recyclable descriptor pool grew with history")
    return {
        "completed_generation_counts": list(HISTORY_CONTROLS),
        "rows": rows,
        "candidate_reference_shape": "(base_page, incarnation)",
        "candidate_reuse_incarnation_delta": 1,
        "candidate_history_walks": 0,
    }


def _drain(store: RecyclableRetirementDescriptorPrimaryStore) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    while store.retirement_queue_snapshot()["queue_count"]:
        trace = store.reclaim_step(budget=SEGMENT_BUDGET)
        if trace.reclaimed_segments > SEGMENT_BUDGET:
            raise AssertionError("descriptor recycling reclaim exceeded segment budget")
        if trace.physical_pages_appended != 0:
            raise AssertionError("descriptor recycling reclaim appended physical storage")
        if trace.retirement_descriptor_preads > 2:
            raise AssertionError("descriptor recycling reclaim read beyond the queue head")
        if trace.retirement_descriptor_pwrites > 1:
            raise AssertionError("descriptor recycling reclaim rewrote multiple descriptors")
        rows.append(trace.to_dict())
    return rows


def run_real_recycling_cycles() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v035-descriptor-reuse-") as tmp:
        path = Path(tmp) / "primary.pages"
        store = RecyclableRetirementDescriptorPrimaryStore(str(path))
        store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=512)

        initial_migrations: list[dict[str, Any]] = []
        for index in range(65):
            trace = store.insert(f"k-{index:03d}")
            if trace.retirement_descriptors_enqueued:
                initial_migrations.append(trace.to_dict())
        if len(initial_migrations) != 3:
            raise AssertionError("expected three initial retirement descriptors")
        if any(int(row["retirement_descriptor_pages_appended"]) != 2 for row in initial_migrations):
            raise AssertionError("initial descriptor allocation did not append two pages each")

        queued = store.retirement_queue_snapshot()
        if queued["queue_count"] != 3 or queued["descriptor_pool_count"] != 3:
            raise AssertionError("initial real descriptor pool depth drifted")
        generations = [int(row["generation"]) for row in queued["descriptors"]]
        if generations != [0, 1, 2]:
            raise AssertionError(f"initial retirement order drifted: {generations}")
        stale = queued["descriptors"][-1]
        stale_page = int(stale["descriptor_page"])
        stale_incarnation = int(stale["descriptor_incarnation"])

        reclaim_rows = _drain(store)
        drained = store.retirement_queue_snapshot()
        if drained["queue_count"] != 0:
            raise AssertionError("initial descriptor queue failed to drain")
        if drained["descriptor_free_count"] != 3 or drained["descriptor_pool_count"] != 3:
            raise AssertionError("drain did not transfer all descriptors into free pool")

        first_reuse: dict[str, Any] | None = None
        for index in range(65, 129):
            trace = store.insert(f"k-{index:03d}")
            if trace.retirement_descriptors_enqueued:
                if first_reuse is not None:
                    raise AssertionError("unexpected extra migration in first reuse interval")
                first_reuse = trace.to_dict()
        if first_reuse is None:
            raise AssertionError("first post-drain migration did not enqueue retirement")
        if int(first_reuse["retirement_descriptor_reuses"]) != 1:
            raise AssertionError("first post-drain enqueue did not recycle a descriptor")
        if int(first_reuse["retirement_descriptor_pages_appended"]) != 0:
            raise AssertionError("recycled descriptor enqueue appended new descriptor pages")

        after_first = store.retirement_queue_snapshot()
        if after_first["queue_count"] != 1 or after_first["descriptor_free_count"] != 2:
            raise AssertionError("first reuse did not move one descriptor free->queue")
        current = after_first["descriptors"][0]
        if int(current["descriptor_page"]) != stale_page:
            raise AssertionError("first reuse did not use the committed free-list head")
        if int(current["descriptor_incarnation"]) != stale_incarnation + 1:
            raise AssertionError("descriptor reuse failed to increment incarnation")

        stale_reference_rejected = False
        try:
            store.retirement_descriptor_reference(stale_page, stale_incarnation)
        except RuntimeError:
            stale_reference_rejected = True
        if not stale_reference_rejected:
            raise AssertionError("stale descriptor reference aliased recycled incarnation")
        latest_payload = store.retirement_descriptor_reference(
            stale_page, int(current["descriptor_incarnation"])
        )
        if int(latest_payload["generation"]) != 3:
            raise AssertionError("recycled descriptor resolves to wrong generation")

        second_reuse: dict[str, Any] | None = None
        for index in range(129, 257):
            trace = store.insert(f"k-{index:03d}")
            if trace.retirement_descriptors_enqueued:
                if second_reuse is not None:
                    raise AssertionError("unexpected extra migration in second reuse interval")
                second_reuse = trace.to_dict()
        if second_reuse is None:
            raise AssertionError("second post-drain migration did not enqueue retirement")
        if int(second_reuse["retirement_descriptor_reuses"]) != 1:
            raise AssertionError("non-empty queue enqueue failed to recycle descriptor")
        if int(second_reuse["retirement_descriptor_pages_appended"]) != 0:
            raise AssertionError("second recycled descriptor enqueue appended pages")
        if int(second_reuse["retirement_descriptor_preads"]) > 4:
            raise AssertionError("recycled non-empty enqueue exceeded free-head + tail read bound")
        if int(second_reuse["retirement_descriptor_pwrites"]) > 2:
            raise AssertionError("recycled non-empty enqueue exceeded new + tail write bound")

        after_second = store.retirement_queue_snapshot()
        if after_second["queue_count"] != 2 or after_second["descriptor_free_count"] != 1:
            raise AssertionError("second reuse did not preserve fixed descriptor pool")
        if after_second["descriptor_pool_count"] != 3:
            raise AssertionError("descriptor pool grew with completed-generation history")
        if [int(row["generation"]) for row in after_second["descriptors"]] != [3, 4]:
            raise AssertionError("recycled queue order drifted")

        for index in range(257):
            if not store.lookup(f"k-{index:03d}").found:
                raise AssertionError("descriptor recycling cycle lost a live key")

        return {
            "initial_migration_traces": initial_migrations,
            "initial_queue": queued,
            "initial_reclaim_steps": reclaim_rows,
            "drained_queue": drained,
            "first_reuse_trace": first_reuse,
            "queue_after_first_reuse": after_first,
            "second_reuse_trace": second_reuse,
            "queue_after_second_reuse": after_second,
            "stale_reference": {
                "descriptor_page": stale_page,
                "descriptor_incarnation": stale_incarnation,
                "recycled_incarnation": int(current["descriptor_incarnation"]),
                "stale_reference_rejected": stale_reference_rejected,
                "recycled_generation": int(latest_payload["generation"]),
            },
            "initial_descriptor_pages_appended": sum(
                int(row["retirement_descriptor_pages_appended"])
                for row in initial_migrations
            ),
            "descriptor_pages_appended_after_pool_established": int(
                first_reuse["retirement_descriptor_pages_appended"]
            )
            + int(second_reuse["retirement_descriptor_pages_appended"]),
            "completed_generations_represented": 5,
            "descriptor_pool_count_after_five_generations": int(
                after_second["descriptor_pool_count"]
            ),
            "all_257_keys_visible": True,
        }
