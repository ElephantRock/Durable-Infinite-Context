from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Callable

from simulator.recyclable_retirement_crash_common import (
    build_dequeue_fixture,
    build_empty_reuse_fixture,
    build_fresh_empty_fixture,
    build_fresh_nonempty_fixture,
    build_nonempty_reuse_fixture,
    build_partial_reclaim_fixture,
    recover_twice,
    require_sigkill,
    semantic_state,
    worker,
)
from simulator.retired_generation_reclamation import _sparse_copy
from storage.recyclable_retirement_descriptor_primary import (
    RecyclableRetirementDescriptorPrimaryStore,
)

FRESH_EMPTY_FAILPOINTS = (
    "retirement_descriptor_written",
    "pages_written",
    "data_synced",
    "committed",
)
FRESH_NONEMPTY_FAILPOINTS = (
    "retirement_descriptor_written",
    "retirement_tail_linked",
    "pages_written",
    "data_synced",
    "committed",
)
REUSE_EMPTY_FAILPOINTS = (
    "retirement_descriptor_reused",
    "pages_written",
    "data_synced",
    "committed",
)
REUSE_NONEMPTY_FAILPOINTS = (
    "retirement_descriptor_reused",
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
DEQUEUE_RECYCLE_FAILPOINTS = (
    "mapping_unlinked",
    "free_header_written",
    "retirement_descriptor_freed",
    "retirement_dequeued",
    "dependencies_synced",
    "committed",
)


def _run_insert_matrix(
    *,
    fixture_builder: Callable[[Path], None],
    trigger_key: str,
    keys_before: tuple[str, ...],
    failpoints: tuple[str, ...],
    prefix: str,
    expected_reuses: int,
    expected_descriptor_pages_appended: int,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=prefix) as tmp:
        directory = Path(tmp)
        base_path = directory / "base.pages"
        fixture_builder(base_path)
        base = RecyclableRetirementDescriptorPrimaryStore(str(base_path))
        keys = keys_before + (trigger_key,)
        pre = semantic_state(base, keys)

        clean_path = directory / "clean.pages"
        _sparse_copy(base_path, clean_path)
        clean = RecyclableRetirementDescriptorPrimaryStore(str(clean_path))
        clean_trace = clean.insert(trigger_key)
        post = semantic_state(clean, keys)
        if clean_trace.retirement_descriptors_enqueued != 1:
            raise AssertionError("clean v0.35 enqueue did not retire one generation")
        if clean_trace.retirement_descriptor_reuses != expected_reuses:
            raise AssertionError("clean v0.35 enqueue descriptor reuse count drifted")
        if clean_trace.retirement_descriptor_pages_appended != expected_descriptor_pages_appended:
            raise AssertionError("clean v0.35 enqueue descriptor append count drifted")

        rows: list[dict[str, Any]] = []
        for failpoint in failpoints:
            crash_path = directory / f"{failpoint}.pages"
            _sparse_copy(base_path, crash_path)
            proc = worker(
                crash_path,
                "insert",
                key=trigger_key,
                failpoint=failpoint,
            )
            require_sigkill(proc, f"v0.35 enqueue failpoint {failpoint}")
            crashed = RecyclableRetirementDescriptorPrimaryStore(str(crash_path))
            observed = semantic_state(crashed, keys)
            expected = post if failpoint == "committed" else pre
            if observed != expected:
                raise AssertionError(f"v0.35 enqueue crash state drifted at {failpoint}")
            recovery_one, recovery_two = recover_twice(crashed)
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


def _run_reclaim_matrix(
    *,
    fixture_builder: Callable[[Path], None],
    keys: tuple[str, ...],
    failpoints: tuple[str, ...],
    prefix: str,
    expect_recycled_descriptor: bool,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=prefix) as tmp:
        directory = Path(tmp)
        base_path = directory / "base.pages"
        fixture_builder(base_path)
        base = RecyclableRetirementDescriptorPrimaryStore(str(base_path))
        visible_keys = tuple(key for key in keys if base.lookup(key).found)
        pre = semantic_state(base, visible_keys)

        clean_path = directory / "clean.pages"
        _sparse_copy(base_path, clean_path)
        clean = RecyclableRetirementDescriptorPrimaryStore(str(clean_path))
        clean_trace = clean.reclaim_step(budget=1)
        post = semantic_state(clean, visible_keys)
        expected_recycled_pages = 2 if expect_recycled_descriptor else 0
        if clean_trace.retirement_descriptor_pages_recycled != expected_recycled_pages:
            raise AssertionError("v0.35 clean reclaim descriptor recycle count drifted")
        if expect_recycled_descriptor:
            if clean.retirement_queue_snapshot()["descriptor_free_count"] <= 0:
                raise AssertionError("clean dequeue did not publish descriptor free head")
        elif clean_trace.retirement_descriptor_pwrites != 1:
            raise AssertionError("clean partial reclaim did not rewrite tagged head descriptor")

        rows: list[dict[str, Any]] = []
        for failpoint in failpoints:
            crash_path = directory / f"{failpoint}.pages"
            _sparse_copy(base_path, crash_path)
            proc = worker(
                crash_path,
                "reclaim",
                budget=1,
                failpoint=failpoint,
            )
            require_sigkill(proc, f"v0.35 reclaim failpoint {failpoint}")
            crashed = RecyclableRetirementDescriptorPrimaryStore(str(crash_path))
            observed = semantic_state(crashed, visible_keys)
            expected = post if failpoint == "committed" else pre
            if observed != expected:
                raise AssertionError(f"v0.35 reclaim crash state drifted at {failpoint}")
            for key in visible_keys:
                if not crashed.lookup(key).found:
                    raise AssertionError("v0.35 reclaim crash hid a live key")
            recovery_one, recovery_two = recover_twice(crashed)
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


def run_recycling_crash_matrices() -> dict[str, Any]:
    fresh_empty = _run_insert_matrix(
        fixture_builder=build_fresh_empty_fixture,
        trigger_key="k-016",
        keys_before=tuple(f"k-{index:03d}" for index in range(16)),
        failpoints=FRESH_EMPTY_FAILPOINTS,
        prefix="dic-v035-fresh-empty-crash-",
        expected_reuses=0,
        expected_descriptor_pages_appended=2,
    )
    fresh_nonempty = _run_insert_matrix(
        fixture_builder=build_fresh_nonempty_fixture,
        trigger_key="k-032",
        keys_before=tuple(f"k-{index:03d}" for index in range(32)),
        failpoints=FRESH_NONEMPTY_FAILPOINTS,
        prefix="dic-v035-fresh-nonempty-crash-",
        expected_reuses=0,
        expected_descriptor_pages_appended=2,
    )
    empty_reuse = _run_insert_matrix(
        fixture_builder=build_empty_reuse_fixture,
        trigger_key="k-128",
        keys_before=tuple(f"k-{index:03d}" for index in range(128)),
        failpoints=REUSE_EMPTY_FAILPOINTS,
        prefix="dic-v035-empty-reuse-crash-",
        expected_reuses=1,
        expected_descriptor_pages_appended=0,
    )
    nonempty_reuse = _run_insert_matrix(
        fixture_builder=build_nonempty_reuse_fixture,
        trigger_key="k-257",
        keys_before=tuple(f"k-{index:03d}" for index in range(257)),
        failpoints=REUSE_NONEMPTY_FAILPOINTS,
        prefix="dic-v035-nonempty-reuse-crash-",
        expected_reuses=1,
        expected_descriptor_pages_appended=0,
    )
    partial = _run_reclaim_matrix(
        fixture_builder=build_partial_reclaim_fixture,
        keys=tuple(f"k-{index:03d}" for index in range(65)),
        failpoints=PARTIAL_RECLAIM_FAILPOINTS,
        prefix="dic-v035-partial-reclaim-crash-",
        expect_recycled_descriptor=False,
    )
    dequeue = _run_reclaim_matrix(
        fixture_builder=build_dequeue_fixture,
        keys=tuple(f"k-{index:03d}" for index in range(17)),
        failpoints=DEQUEUE_RECYCLE_FAILPOINTS,
        prefix="dic-v035-dequeue-recycle-crash-",
        expect_recycled_descriptor=True,
    )
    return {
        "fresh_empty_queue": fresh_empty,
        "fresh_nonempty_queue": fresh_nonempty,
        "empty_queue_reuse": empty_reuse,
        "nonempty_queue_reuse": nonempty_reuse,
        "partial_head": partial,
        "dequeue_to_free": dequeue,
        "case_count": int(fresh_empty["case_count"])
        + int(fresh_nonempty["case_count"])
        + int(empty_reuse["case_count"])
        + int(nonempty_reuse["case_count"])
        + int(partial["case_count"])
        + int(dequeue["case_count"]),
    }
