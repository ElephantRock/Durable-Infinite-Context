from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Callable

from simulator.recyclable_retirement_crash_common import (
    build_dequeue_fixture,
    build_empty_reuse_fixture,
    build_nonempty_reuse_fixture,
    recover_twice,
    require_sigkill,
    semantic_state,
    worker,
)
from simulator.retired_generation_reclamation import _sparse_copy
from storage.recyclable_retirement_descriptor_primary import (
    RecyclableRetirementDescriptorPrimaryStore,
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
        if clean_trace.retirement_descriptor_reuses != 1:
            raise AssertionError("clean v0.35 enqueue did not reuse a descriptor")
        if clean_trace.retirement_descriptor_pages_appended != 0:
            raise AssertionError("clean recycled enqueue appended descriptor pages")

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


def _run_dequeue_matrix() -> dict[str, Any]:
    keys = tuple(f"k-{index:03d}" for index in range(17))
    with tempfile.TemporaryDirectory(prefix="dic-v035-dequeue-recycle-") as tmp:
        directory = Path(tmp)
        base_path = directory / "base.pages"
        build_dequeue_fixture(base_path)
        base = RecyclableRetirementDescriptorPrimaryStore(str(base_path))
        pre = semantic_state(base, keys)

        clean_path = directory / "clean.pages"
        _sparse_copy(base_path, clean_path)
        clean = RecyclableRetirementDescriptorPrimaryStore(str(clean_path))
        clean_trace = clean.reclaim_step(budget=1)
        post = semantic_state(clean, keys)
        if clean_trace.retirement_descriptor_pages_recycled != 2:
            raise AssertionError("clean dequeue did not recycle one dual-copy descriptor")
        if clean.retirement_queue_snapshot()["descriptor_free_count"] != 1:
            raise AssertionError("clean dequeue did not publish descriptor free head")

        rows: list[dict[str, Any]] = []
        for failpoint in DEQUEUE_RECYCLE_FAILPOINTS:
            crash_path = directory / f"{failpoint}.pages"
            _sparse_copy(base_path, crash_path)
            proc = worker(
                crash_path,
                "reclaim",
                budget=1,
                failpoint=failpoint,
            )
            require_sigkill(proc, f"v0.35 dequeue failpoint {failpoint}")
            crashed = RecyclableRetirementDescriptorPrimaryStore(str(crash_path))
            observed = semantic_state(crashed, keys)
            expected = post if failpoint == "committed" else pre
            if observed != expected:
                raise AssertionError(f"v0.35 dequeue crash state drifted at {failpoint}")
            for key in keys:
                if not crashed.lookup(key).found:
                    raise AssertionError("v0.35 dequeue crash hid a live key")
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
            "failpoints": list(DEQUEUE_RECYCLE_FAILPOINTS),
            "case_count": len(rows),
            "clean_trace": clean_trace.to_dict(),
            "rows": rows,
            "all_exact_committed_state_match": True,
            "all_live_keys_visible": True,
            "all_recovery_scan_free": True,
            "all_second_recovery_idempotent": True,
        }


def run_recycling_crash_matrices() -> dict[str, Any]:
    empty_reuse = _run_insert_matrix(
        fixture_builder=build_empty_reuse_fixture,
        trigger_key="k-128",
        keys_before=tuple(f"k-{index:03d}" for index in range(128)),
        failpoints=REUSE_EMPTY_FAILPOINTS,
        prefix="dic-v035-empty-reuse-crash-",
    )
    nonempty_reuse = _run_insert_matrix(
        fixture_builder=build_nonempty_reuse_fixture,
        trigger_key="k-256",
        keys_before=tuple(f"k-{index:03d}" for index in range(256)),
        failpoints=REUSE_NONEMPTY_FAILPOINTS,
        prefix="dic-v035-nonempty-reuse-crash-",
    )
    dequeue = _run_dequeue_matrix()
    return {
        "empty_queue_reuse": empty_reuse,
        "nonempty_queue_reuse": nonempty_reuse,
        "dequeue_to_free": dequeue,
        "case_count": int(empty_reuse["case_count"])
        + int(nonempty_reuse["case_count"])
        + int(dequeue["case_count"]),
    }
