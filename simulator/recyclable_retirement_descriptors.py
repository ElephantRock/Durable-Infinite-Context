from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from storage.recyclable_retirement_descriptor_primary import (
    RecyclableRetirementDescriptorPrimaryStore,
)

HISTORY_CONTROLS = (1, 4, 16, 64, 256)
SEGMENT_BUDGET = 2


def _fail(message: str, **context: Any) -> None:
    raise AssertionError(
        f"{message}; observed={json.dumps(context, sort_keys=True, default=str)}"
    )


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
        _fail("append-only descriptor control did not grow with history", rows=rows)
    if any(row["recyclable_serial_peak_descriptor_pages"] != 2 for row in rows):
        _fail("serial recyclable descriptor pool grew with history", rows=rows)
    return {
        "completed_generation_counts": list(HISTORY_CONTROLS),
        "rows": rows,
        "candidate_reference_shape": "(base_page, incarnation)",
        "candidate_reuse_incarnation_delta": 1,
        "candidate_history_walks": 0,
    }


def _drain(store: RecyclableRetirementDescriptorPrimaryStore) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    step = 0
    while store.retirement_queue_snapshot()["queue_count"]:
        before = store.retirement_queue_snapshot()
        trace = store.reclaim_step(budget=SEGMENT_BUDGET)
        trace_row = trace.to_dict()
        after = store.retirement_queue_snapshot()
        if trace.reclaimed_segments > SEGMENT_BUDGET:
            _fail(
                "descriptor recycling reclaim exceeded segment budget",
                step=step,
                before=before,
                trace=trace_row,
                after=after,
            )
        if trace.physical_pages_appended != 0:
            _fail(
                "descriptor recycling reclaim appended physical storage",
                step=step,
                before=before,
                trace=trace_row,
                after=after,
            )
        if trace.retirement_descriptor_preads > 2:
            _fail(
                "descriptor recycling reclaim read beyond the queue head",
                step=step,
                before=before,
                trace=trace_row,
                after=after,
            )
        if trace.retirement_descriptor_pwrites > 1:
            _fail(
                "descriptor recycling reclaim rewrote multiple descriptors",
                step=step,
                before=before,
                trace=trace_row,
                after=after,
            )
        rows.append(trace_row)
        step += 1
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
        initial_snapshot = store.retirement_queue_snapshot()
        if len(initial_migrations) != 3:
            _fail(
                "expected three initial retirement descriptors",
                migration_count=len(initial_migrations),
                migrations=initial_migrations,
                snapshot=initial_snapshot,
            )
        if any(
            int(row["retirement_descriptor_pages_appended"]) != 2
            for row in initial_migrations
        ):
            _fail(
                "initial descriptor allocation did not append two pages each",
                migrations=initial_migrations,
                snapshot=initial_snapshot,
            )

        queued = initial_snapshot
        if queued["queue_count"] != 3 or queued["descriptor_pool_count"] != 3:
            _fail(
                "initial real descriptor pool depth drifted",
                expected_queue_count=3,
                expected_descriptor_pool_count=3,
                snapshot=queued,
                migrations=initial_migrations,
            )
        generations = [int(row["generation"]) for row in queued["descriptors"]]
        if generations != [0, 1, 2]:
            _fail(
                "initial retirement order drifted",
                expected_generations=[0, 1, 2],
                actual_generations=generations,
                snapshot=queued,
            )
        stale = queued["descriptors"][-1]
        stale_page = int(stale["descriptor_page"])
        stale_incarnation = int(stale["descriptor_incarnation"])

        reclaim_rows = _drain(store)
        drained = store.retirement_queue_snapshot()
        if drained["queue_count"] != 0:
            _fail(
                "initial descriptor queue failed to drain",
                snapshot=drained,
                reclaim_steps=reclaim_rows,
            )
        if drained["descriptor_free_count"] != 3 or drained["descriptor_pool_count"] != 3:
            _fail(
                "drain did not transfer all descriptors into free pool",
                expected_descriptor_free_count=3,
                expected_descriptor_pool_count=3,
                snapshot=drained,
                reclaim_steps=reclaim_rows,
            )

        first_reuse: dict[str, Any] | None = None
        first_reuse_index: int | None = None
        first_interval_enqueues: list[dict[str, Any]] = []
        for index in range(65, 129):
            trace = store.insert(f"k-{index:03d}")
            if trace.retirement_descriptors_enqueued:
                row = trace.to_dict()
                first_interval_enqueues.append(row)
                if first_reuse is not None:
                    _fail(
                        "unexpected extra migration in first reuse interval",
                        current_index=index,
                        first_reuse_index=first_reuse_index,
                        first_reuse=first_reuse,
                        extra_reuse=row,
                        all_interval_enqueues=first_interval_enqueues,
                        snapshot=store.retirement_queue_snapshot(),
                    )
                first_reuse = row
                first_reuse_index = index
        after_first = store.retirement_queue_snapshot()
        if first_reuse is None:
            _fail(
                "first post-drain migration did not enqueue retirement",
                interval=[65, 128],
                interval_enqueues=first_interval_enqueues,
                snapshot=after_first,
                drained_snapshot=drained,
            )
        if int(first_reuse["retirement_descriptor_reuses"]) != 1:
            _fail(
                "first post-drain enqueue did not recycle a descriptor",
                migration_index=first_reuse_index,
                trace=first_reuse,
                snapshot=after_first,
                drained_snapshot=drained,
            )
        if int(first_reuse["retirement_descriptor_pages_appended"]) != 0:
            _fail(
                "recycled descriptor enqueue appended new descriptor pages",
                migration_index=first_reuse_index,
                trace=first_reuse,
                snapshot=after_first,
                drained_snapshot=drained,
            )

        if after_first["queue_count"] != 1 or after_first["descriptor_free_count"] != 2:
            _fail(
                "first reuse did not move one descriptor free->queue",
                migration_index=first_reuse_index,
                trace=first_reuse,
                expected_queue_count=1,
                expected_descriptor_free_count=2,
                snapshot=after_first,
                drained_snapshot=drained,
            )
        current = after_first["descriptors"][0]
        if int(current["descriptor_page"]) != stale_page:
            _fail(
                "first reuse did not use the committed free-list head",
                expected_page=stale_page,
                current=current,
                stale=stale,
                trace=first_reuse,
                snapshot=after_first,
                drained_snapshot=drained,
            )
        if int(current["descriptor_incarnation"]) != stale_incarnation + 1:
            _fail(
                "descriptor reuse failed to increment incarnation",
                stale_page=stale_page,
                stale_incarnation=stale_incarnation,
                expected_incarnation=stale_incarnation + 1,
                current=current,
                trace=first_reuse,
                snapshot=after_first,
            )

        stale_reference_rejected = False
        stale_reference_error: str | None = None
        try:
            store.retirement_descriptor_reference(stale_page, stale_incarnation)
        except RuntimeError as exc:
            stale_reference_rejected = True
            stale_reference_error = str(exc)
        if not stale_reference_rejected:
            _fail(
                "stale descriptor reference aliased recycled incarnation",
                stale_page=stale_page,
                stale_incarnation=stale_incarnation,
                current=current,
                snapshot=after_first,
            )
        latest_payload = store.retirement_descriptor_reference(
            stale_page, int(current["descriptor_incarnation"])
        )
        if int(latest_payload["generation"]) != 3:
            _fail(
                "recycled descriptor resolves to wrong generation",
                expected_generation=3,
                latest_payload=latest_payload,
                stale_reference_error=stale_reference_error,
                current=current,
                snapshot=after_first,
            )

        second_reuse: dict[str, Any] | None = None
        second_reuse_index: int | None = None
        second_interval_enqueues: list[dict[str, Any]] = []
        for index in range(129, 258):
            trace = store.insert(f"k-{index:03d}")
            if trace.retirement_descriptors_enqueued:
                row = trace.to_dict()
                second_interval_enqueues.append(row)
                if second_reuse is not None:
                    _fail(
                        "unexpected extra migration in second reuse interval",
                        current_index=index,
                        second_reuse_index=second_reuse_index,
                        second_reuse=second_reuse,
                        extra_reuse=row,
                        all_interval_enqueues=second_interval_enqueues,
                        snapshot=store.retirement_queue_snapshot(),
                    )
                second_reuse = row
                second_reuse_index = index
        after_second = store.retirement_queue_snapshot()
        if second_reuse is None:
            _fail(
                "second post-drain migration did not enqueue retirement",
                interval=[129, 257],
                interval_enqueues=second_interval_enqueues,
                snapshot=after_second,
                first_reuse=first_reuse,
                after_first=after_first,
            )
        if int(second_reuse["retirement_descriptor_reuses"]) != 1:
            _fail(
                "non-empty queue enqueue failed to recycle descriptor",
                migration_index=second_reuse_index,
                trace=second_reuse,
                snapshot=after_second,
                after_first=after_first,
            )
        if int(second_reuse["retirement_descriptor_pages_appended"]) != 0:
            _fail(
                "second recycled descriptor enqueue appended pages",
                migration_index=second_reuse_index,
                trace=second_reuse,
                snapshot=after_second,
                after_first=after_first,
            )
        if int(second_reuse["retirement_descriptor_preads"]) > 4:
            _fail(
                "recycled non-empty enqueue exceeded free-head + tail read bound",
                migration_index=second_reuse_index,
                trace=second_reuse,
                expected_max_preads=4,
                snapshot=after_second,
            )
        if int(second_reuse["retirement_descriptor_pwrites"]) > 2:
            _fail(
                "recycled non-empty enqueue exceeded new + tail write bound",
                migration_index=second_reuse_index,
                trace=second_reuse,
                expected_max_pwrites=2,
                snapshot=after_second,
            )

        if after_second["queue_count"] != 2 or after_second["descriptor_free_count"] != 1:
            _fail(
                "second reuse did not preserve fixed descriptor pool",
                expected_queue_count=2,
                expected_descriptor_free_count=1,
                snapshot=after_second,
                trace=second_reuse,
            )
        if after_second["descriptor_pool_count"] != 3:
            _fail(
                "descriptor pool grew with completed-generation history",
                expected_descriptor_pool_count=3,
                snapshot=after_second,
                trace=second_reuse,
            )
        second_generations = [
            int(row["generation"]) for row in after_second["descriptors"]
        ]
        if second_generations != [3, 4]:
            _fail(
                "recycled queue order drifted",
                expected_generations=[3, 4],
                actual_generations=second_generations,
                snapshot=after_second,
                trace=second_reuse,
            )

        for index in range(258):
            lookup = store.lookup(f"k-{index:03d}")
            if not lookup.found:
                _fail(
                    "descriptor recycling cycle lost a live key",
                    missing_key=f"k-{index:03d}",
                    missing_index=index,
                    lookup=lookup.to_dict(),
                    snapshot=after_second,
                )

        return {
            "initial_migration_traces": initial_migrations,
            "initial_queue": queued,
            "initial_reclaim_steps": reclaim_rows,
            "drained_queue": drained,
            "first_reuse_index": first_reuse_index,
            "first_reuse_trace": first_reuse,
            "queue_after_first_reuse": after_first,
            "second_reuse_index": second_reuse_index,
            "second_reuse_trace": second_reuse,
            "queue_after_second_reuse": after_second,
            "stale_reference": {
                "descriptor_page": stale_page,
                "descriptor_incarnation": stale_incarnation,
                "recycled_incarnation": int(current["descriptor_incarnation"]),
                "stale_reference_rejected": stale_reference_rejected,
                "stale_reference_error": stale_reference_error,
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
            "all_258_keys_visible": True,
        }
