from __future__ import annotations

import json
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

from simulator.retired_generation_reclamation import _sparse_copy
from storage.queued_generation_reclaiming_primary import (
    QueuedGenerationReclaimingPrimaryStore,
    RETIREMENT_DESCRIPTOR_COPIES,
)

ROOT = Path(__file__).resolve().parent.parent
WORKER = ROOT / "retirement_queue_worker.py"
QUEUE_BACKLOG_CONTROLS = (1, 4, 16, 64, 256)
SEGMENT_BUDGET = 2
EMPTY_ENQUEUE_FAILPOINTS = (
    "retirement_descriptor_written",
    "pages_written",
    "data_synced",
    "committed",
)
NONEMPTY_ENQUEUE_FAILPOINTS = (
    "retirement_descriptor_written",
    "retirement_tail_linked",
    "pages_written",
    "data_synced",
    "committed",
)
PARTIAL_RECLAIM_FAILPOINTS = (
    "mapping_unlinked",
    "free_header_written",
    "retirement_descriptor_updated",
    "dependencies_synced",
    "committed",
)
DEQUEUE_RECLAIM_FAILPOINTS = (
    "mapping_unlinked",
    "free_header_written",
    "retirement_dequeued",
    "dependencies_synced",
    "committed",
)


def run_enqueue_scaling_controls() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for queued in QUEUE_BACKLOG_CONTROLS:
        flat = json.dumps(
            {
                "retired_generations": [
                    {"generation": index, "cursor": 1000 + index, "remaining": index + 1}
                    for index in range(queued)
                ]
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        rows.append(
            {
                "queued_generations": queued,
                "flat_manifest_bytes": len(flat),
                "naive_tail_walk_descriptor_visits": queued,
                "candidate_new_descriptor_pages": RETIREMENT_DESCRIPTOR_COPIES,
                "candidate_new_descriptor_pwrites": 1,
                "candidate_existing_tail_preads": 2,
                "candidate_existing_tail_pwrites": 1,
                "candidate_history_walks": 0,
            }
        )
    if not all(
        rows[index]["flat_manifest_bytes"] < rows[index + 1]["flat_manifest_bytes"]
        for index in range(len(rows) - 1)
    ):
        raise AssertionError("flat retirement manifest control did not grow")
    if not all(
        rows[index]["naive_tail_walk_descriptor_visits"]
        < rows[index + 1]["naive_tail_walk_descriptor_visits"]
        for index in range(len(rows) - 1)
    ):
        raise AssertionError("naive tail-walk control did not grow")
    return {
        "queued_generation_counts": list(QUEUE_BACKLOG_CONTROLS),
        "rows": rows,
        "candidate_enqueue_descriptor_pages": RETIREMENT_DESCRIPTOR_COPIES,
        "candidate_enqueue_max_descriptor_preads": 2,
        "candidate_enqueue_max_descriptor_pwrites": 2,
        "candidate_enqueue_history_walks": 0,
    }


def run_real_queue_cycles() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v034-queue-") as tmp:
        path = Path(tmp) / "primary.pages"
        store = QueuedGenerationReclaimingPrimaryStore(str(path))
        store.initialize(
            initial_capacity=32,
            max_load=0.50,
            migration_slot_budget=512,
        )

        migration_traces: list[dict[str, Any]] = []
        for index in range(65):
            trace = store.insert(f"k-{index:03d}")
            if trace.migration_completed:
                migration_traces.append(trace.to_dict())
        if len(migration_traces) != 3:
            raise AssertionError("expected three completed generations before cleanup")

        queued = store.retirement_queue_snapshot()
        generations = [int(row["generation"]) for row in queued["descriptors"]]
        if generations != [0, 1, 2]:
            raise AssertionError(f"retirement queue order drifted: {generations}")
        for row in migration_traces:
            if int(row["retirement_descriptors_enqueued"]) != 1:
                raise AssertionError("migration completion did not enqueue exactly one descriptor")
            if int(row["retirement_descriptor_pages_appended"]) != 2:
                raise AssertionError("enqueue did not append exactly one dual-copy descriptor")
            if int(row["retirement_descriptor_preads"]) > 2:
                raise AssertionError("enqueue descriptor reads grew beyond fixed tail lookup")
            if int(row["retirement_descriptor_pwrites"]) > 2:
                raise AssertionError("enqueue descriptor writes grew beyond fixed new+tail work")

        for index in range(65):
            if not store.lookup(f"k-{index:03d}").found:
                raise AssertionError("queued retirement hid a live key")

        reclaim_rows: list[dict[str, Any]] = []
        drained_generations: list[int] = []
        while store.retirement_queue_snapshot()["queue_count"]:
            before = store.retirement_queue_snapshot()
            head_generation = int(before["descriptors"][0]["generation"])
            trace = store.reclaim_step(budget=SEGMENT_BUDGET)
            if int(trace.retired_generation) != head_generation:
                raise AssertionError("reclaim did not process FIFO head generation")
            if trace.reclaimed_segments > SEGMENT_BUDGET:
                raise AssertionError("reclaim exceeded explicit segment budget")
            if trace.retirement_descriptor_preads > 2:
                raise AssertionError("reclaim read more than the queue head descriptor")
            if trace.retirement_descriptor_pwrites > 1:
                raise AssertionError("reclaim rewrote more than the queue head descriptor")
            if trace.physical_pages_appended != 0:
                raise AssertionError("queue reclaim appended physical storage")
            if (
                trace.generation_pages_scanned
                or trace.mapping_nodes_scanned
                or trace.retirement_descriptors_scanned
                or trace.logical_redo
            ):
                raise AssertionError("queue reclaim introduced scan/redo work")
            reclaim_rows.append(trace.to_dict())
            drained_generations.append(head_generation)

        final_queue = store.retirement_queue_snapshot()
        if final_queue["queue_count"] != 0 or final_queue["head_page"] is not None:
            raise AssertionError("retirement queue did not drain exactly")
        if sorted(set(drained_generations)) != [0, 1, 2]:
            raise AssertionError("retirement queue skipped or duplicated generation order")
        free_before_reuse = int(final_queue["free_count"])
        if free_before_reuse <= 0:
            raise AssertionError("queue drain produced no reusable extent")

        reuse_traces: list[dict[str, Any]] = []
        for index in range(65, 129):
            trace = store.insert(f"k-{index:03d}")
            if trace.reused_free_extents:
                reuse_traces.append(trace.to_dict())
        if not reuse_traces:
            raise AssertionError("post-drain growth did not reuse a reclaimed extent")
        if sum(int(row["data_page_scrub_pwrites"]) for row in reuse_traces) < 32:
            raise AssertionError("reused extent bypassed the fixed data scrub")

        for index in range(129):
            if not store.lookup(f"k-{index:03d}").found:
                raise AssertionError("post-reuse queue experiment lost a live key")

        return {
            "migration_traces": migration_traces,
            "queue_before_cleanup": queued,
            "reclaim_steps": reclaim_rows,
            "queue_after_cleanup": final_queue,
            "free_before_reuse": free_before_reuse,
            "reuse_traces": reuse_traces,
            "all_129_keys_visible": True,
            "max_enqueue_descriptor_preads": max(
                int(row["retirement_descriptor_preads"]) for row in migration_traces
            ),
            "max_enqueue_descriptor_pwrites": max(
                int(row["retirement_descriptor_pwrites"]) for row in migration_traces
            ),
            "max_enqueue_descriptor_pages": max(
                int(row["retirement_descriptor_pages_appended"]) for row in migration_traces
            ),
            "max_reclaim_segments_per_step": max(
                [int(row["reclaimed_segments"]) for row in reclaim_rows] or [0]
            ),
            "max_reclaim_descriptor_preads": max(
                [int(row["retirement_descriptor_preads"]) for row in reclaim_rows] or [0]
            ),
            "max_reclaim_descriptor_pwrites": max(
                [int(row["retirement_descriptor_pwrites"]) for row in reclaim_rows] or [0]
            ),
            "max_reclaim_pages_appended": max(
                [int(row["physical_pages_appended"]) for row in reclaim_rows] or [0]
            ),
        }


def _worker(
    path: Path,
    command: str,
    *,
    failpoint: str,
    key: str | None = None,
    budget: int | None = None,
) -> subprocess.CompletedProcess[str]:
    argv = [sys.executable, str(WORKER), "--file", str(path), command]
    if key is not None:
        argv.extend(["--key", key])
    if budget is not None:
        argv.extend(["--budget", str(budget)])
    argv.extend(["--failpoint", failpoint])
    return subprocess.run(
        argv,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _semantic_state(
    store: QueuedGenerationReclaimingPrimaryStore,
    keys: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "state": store.committed_state(keys),
        "queue": store.retirement_queue_snapshot(),
    }


def _require_scan_free(recovery: dict[str, Any]) -> None:
    if int(recovery["logical_work"]) != 0:
        raise AssertionError("recovery required logical redo")
    if int(recovery["generation_pages_scanned"]) != 0:
        raise AssertionError("recovery scanned generation pages")
    if int(recovery["mapping_nodes_scanned"]) != 0:
        raise AssertionError("recovery scanned radix nodes")
    if int(recovery.get("retirement_descriptors_scanned", 0)) != 0:
        raise AssertionError("recovery scanned retirement descriptors")


def _run_insert_crash_matrix(
    *,
    fixture_builder: Callable[[Path], None],
    trigger_key: str,
    keys_before: tuple[str, ...],
    failpoints: tuple[str, ...],
    prefix: str,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=prefix) as tmp:
        directory = Path(tmp)
        base_path = directory / "base.pages"
        fixture_builder(base_path)
        base = QueuedGenerationReclaimingPrimaryStore(str(base_path))
        pre = _semantic_state(base, keys_before + (trigger_key,))

        clean_path = directory / "clean.pages"
        _sparse_copy(base_path, clean_path)
        clean = QueuedGenerationReclaimingPrimaryStore(str(clean_path))
        clean_trace = clean.insert(trigger_key)
        post = _semantic_state(clean, keys_before + (trigger_key,))
        if clean_trace.retirement_descriptors_enqueued != 1:
            raise AssertionError("clean enqueue crash control did not enqueue one descriptor")

        rows: list[dict[str, Any]] = []
        for failpoint in failpoints:
            crash_path = directory / f"{failpoint}.pages"
            _sparse_copy(base_path, crash_path)
            proc = _worker(
                crash_path,
                "insert",
                key=trigger_key,
                failpoint=failpoint,
            )
            if proc.returncode != -signal.SIGKILL:
                raise AssertionError(
                    f"enqueue failpoint {failpoint} did not SIGKILL: {proc.returncode} {proc.stderr}"
                )
            crashed = QueuedGenerationReclaimingPrimaryStore(str(crash_path))
            observed = _semantic_state(crashed, keys_before + (trigger_key,))
            expected = post if failpoint == "committed" else pre
            if observed != expected:
                raise AssertionError(f"enqueue crash state drifted at {failpoint}")
            recovery_one = crashed.recover()
            recovery_two = crashed.recover()
            _require_scan_free(recovery_one)
            _require_scan_free(recovery_two)
            if int(recovery_two["physical_truncated_bytes"]) != 0:
                raise AssertionError("second enqueue recovery ceased to be idempotent")
            rows.append(
                {
                    "failpoint": failpoint,
                    "expected_committed": failpoint == "committed",
                    "exact_committed_state_match": True,
                    "recovery_one": recovery_one,
                    "recovery_two": recovery_two,
                }
            )
        return {
            "failpoints": list(failpoints),
            "case_count": len(rows),
            "clean_trace": clean_trace.to_dict(),
            "rows": rows,
            "all_exact_committed_state_match": True,
            "all_recovery_scan_free": True,
            "all_second_recovery_idempotent": True,
        }


def _build_empty_enqueue_fixture(path: Path) -> None:
    store = QueuedGenerationReclaimingPrimaryStore(str(path))
    store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=512)
    for index in range(16):
        store.insert(f"k-{index:03d}")
    if store.retirement_queue_snapshot()["queue_count"] != 0:
        raise AssertionError("empty enqueue fixture already has retirement work")


def _build_nonempty_enqueue_fixture(path: Path) -> None:
    store = QueuedGenerationReclaimingPrimaryStore(str(path))
    store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=512)
    for index in range(32):
        store.insert(f"k-{index:03d}")
    queue = store.retirement_queue_snapshot()
    if queue["queue_count"] != 1:
        raise AssertionError("nonempty enqueue fixture does not have exactly one queued generation")


def run_enqueue_crash_matrices() -> dict[str, Any]:
    empty = _run_insert_crash_matrix(
        fixture_builder=_build_empty_enqueue_fixture,
        trigger_key="k-016",
        keys_before=tuple(f"k-{index:03d}" for index in range(16)),
        failpoints=EMPTY_ENQUEUE_FAILPOINTS,
        prefix="dic-v034-empty-enqueue-crash-",
    )
    nonempty = _run_insert_crash_matrix(
        fixture_builder=_build_nonempty_enqueue_fixture,
        trigger_key="k-032",
        keys_before=tuple(f"k-{index:03d}" for index in range(32)),
        failpoints=NONEMPTY_ENQUEUE_FAILPOINTS,
        prefix="dic-v034-nonempty-enqueue-crash-",
    )
    if int(empty["clean_trace"]["retirement_descriptor_pwrites"]) != 1:
        raise AssertionError("empty queue enqueue write count drifted")
    if int(nonempty["clean_trace"]["retirement_descriptor_pwrites"]) != 2:
        raise AssertionError("nonempty queue enqueue write count drifted")
    if int(nonempty["clean_trace"]["retirement_descriptor_preads"]) != 2:
        raise AssertionError("nonempty queue enqueue tail-read count drifted")
    return {"empty_queue": empty, "nonempty_queue": nonempty}


def _build_partial_reclaim_fixture(path: Path) -> None:
    store = QueuedGenerationReclaimingPrimaryStore(str(path))
    store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=512)
    for index in range(65):
        store.insert(f"k-{index:03d}")
    while True:
        queue = store.retirement_queue_snapshot()
        if queue["queue_count"] <= 0:
            raise AssertionError("could not find a multi-segment queued generation")
        remaining = int(queue["descriptors"][0]["remaining_segments"])
        if remaining > 1:
            return
        store.reclaim_step(budget=remaining)


def _build_dequeue_reclaim_fixture(path: Path) -> None:
    store = QueuedGenerationReclaimingPrimaryStore(str(path))
    store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=512)
    for index in range(17):
        store.insert(f"k-{index:03d}")
    queue = store.retirement_queue_snapshot()
    if queue["queue_count"] != 1:
        raise AssertionError("dequeue fixture lacks one queued generation")
    remaining = int(queue["descriptors"][0]["remaining_segments"])
    if remaining > 1:
        store.reclaim_step(budget=remaining - 1)
    queue = store.retirement_queue_snapshot()
    if int(queue["descriptors"][0]["remaining_segments"]) != 1:
        raise AssertionError("dequeue fixture did not reach one remaining segment")


def _run_reclaim_crash_matrix(
    *,
    fixture_builder: Callable[[Path], None],
    failpoints: tuple[str, ...],
    prefix: str,
) -> dict[str, Any]:
    keys = tuple(f"k-{index:03d}" for index in range(65))
    with tempfile.TemporaryDirectory(prefix=prefix) as tmp:
        directory = Path(tmp)
        base_path = directory / "base.pages"
        fixture_builder(base_path)
        base = QueuedGenerationReclaimingPrimaryStore(str(base_path))
        visible_keys = tuple(key for key in keys if base.lookup(key).found)
        pre = _semantic_state(base, visible_keys)

        clean_path = directory / "clean.pages"
        _sparse_copy(base_path, clean_path)
        clean = QueuedGenerationReclaimingPrimaryStore(str(clean_path))
        clean_trace = clean.reclaim_step(budget=1)
        post = _semantic_state(clean, visible_keys)
        for key in visible_keys:
            if not clean.lookup(key).found:
                raise AssertionError("clean queued reclaim lost a live key")

        rows: list[dict[str, Any]] = []
        for failpoint in failpoints:
            crash_path = directory / f"{failpoint}.pages"
            _sparse_copy(base_path, crash_path)
            proc = _worker(
                crash_path,
                "reclaim",
                budget=1,
                failpoint=failpoint,
            )
            if proc.returncode != -signal.SIGKILL:
                raise AssertionError(
                    f"reclaim failpoint {failpoint} did not SIGKILL: {proc.returncode} {proc.stderr}"
                )
            crashed = QueuedGenerationReclaimingPrimaryStore(str(crash_path))
            observed = _semantic_state(crashed, visible_keys)
            expected = post if failpoint == "committed" else pre
            if observed != expected:
                raise AssertionError(f"queued reclaim crash state drifted at {failpoint}")
            for key in visible_keys:
                if not crashed.lookup(key).found:
                    raise AssertionError("queued reclaim crash hid a live key")
            recovery_one = crashed.recover()
            recovery_two = crashed.recover()
            _require_scan_free(recovery_one)
            _require_scan_free(recovery_two)
            if int(recovery_two["physical_truncated_bytes"]) != 0:
                raise AssertionError("second queued reclaim recovery ceased to be idempotent")
            rows.append(
                {
                    "failpoint": failpoint,
                    "expected_committed": failpoint == "committed",
                    "exact_committed_state_match": True,
                    "all_live_keys_visible": True,
                    "recovery_one": recovery_one,
                    "recovery_two": recovery_two,
                }
            )
        return {
            "failpoints": list(failpoints),
            "case_count": len(rows),
            "clean_trace": clean_trace.to_dict(),
            "rows": rows,
            "all_exact_committed_state_match": True,
            "all_live_keys_visible": True,
            "all_recovery_scan_free": True,
            "all_second_recovery_idempotent": True,
        }


def run_reclaim_crash_matrices() -> dict[str, Any]:
    partial = _run_reclaim_crash_matrix(
        fixture_builder=_build_partial_reclaim_fixture,
        failpoints=PARTIAL_RECLAIM_FAILPOINTS,
        prefix="dic-v034-partial-reclaim-crash-",
    )
    dequeue = _run_reclaim_crash_matrix(
        fixture_builder=_build_dequeue_reclaim_fixture,
        failpoints=DEQUEUE_RECLAIM_FAILPOINTS,
        prefix="dic-v034-dequeue-reclaim-crash-",
    )
    if int(partial["clean_trace"]["retirement_descriptor_pwrites"]) != 1:
        raise AssertionError("partial reclaim did not rewrite exactly one head descriptor")
    if int(dequeue["clean_trace"]["retirement_descriptor_pwrites"]) != 0:
        raise AssertionError("dequeue reclaim unexpectedly rewrote a descriptor")
    return {"partial_head": partial, "dequeue_head": dequeue}
