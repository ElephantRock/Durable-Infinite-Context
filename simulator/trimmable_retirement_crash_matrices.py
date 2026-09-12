from __future__ import annotations

import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from simulator.retired_generation_reclamation import _sparse_copy
from storage.retirement_descriptor_pool import RETIREMENT_STATUS_FREE
from storage.trimmable_retirement_descriptor_primary import (
    TrimmableRetirementDescriptorPrimaryStore,
)

ROOT = Path(__file__).resolve().parent.parent
WORKER = ROOT / "trimmable_retirement_descriptor_worker.py"

TRIM_FAILPOINTS = (
    "descriptor_tail_release_selected",
    "committed",
    "descriptor_tail_truncated",
    "trim_synced",
)
FRESH_AFTER_RELEASE_FAILPOINTS = (
    "retirement_descriptor_written",
    "pages_written",
    "data_synced",
    "committed",
)


def _worker(
    path: Path,
    command: str,
    *,
    failpoint: str,
    key: str | None = None,
) -> subprocess.CompletedProcess[str]:
    argv = [sys.executable, str(WORKER), "--file", str(path), command]
    if key is not None:
        argv.extend(["--key", key])
    argv.extend(["--failpoint", failpoint])
    return subprocess.run(
        argv,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _require_sigkill(proc: subprocess.CompletedProcess[str], label: str) -> None:
    if proc.returncode != -signal.SIGKILL:
        raise AssertionError(f"{label} did not SIGKILL: {proc.returncode} {proc.stderr}")


def _semantic_state(
    store: TrimmableRetirementDescriptorPrimaryStore,
    keys: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "state": store.committed_state(keys),
        "queue": store.retirement_queue_snapshot(),
    }


def _require_scan_free(recovery: dict[str, Any]) -> None:
    if int(recovery["logical_work"]) != 0:
        raise AssertionError("v0.36 recovery required logical redo")
    if int(recovery["generation_pages_scanned"]) != 0:
        raise AssertionError("v0.36 recovery scanned generation pages")
    if int(recovery["mapping_nodes_scanned"]) != 0:
        raise AssertionError("v0.36 recovery scanned radix nodes")
    if int(recovery.get("retirement_descriptors_scanned", 0)) != 0:
        raise AssertionError("v0.36 recovery scanned retirement descriptors")


def _recover_twice(
    store: TrimmableRetirementDescriptorPrimaryStore,
) -> tuple[dict[str, Any], dict[str, Any]]:
    one = store.recover()
    two = store.recover()
    _require_scan_free(one)
    _require_scan_free(two)
    if int(two["physical_truncated_bytes"]) != 0:
        raise AssertionError("v0.36 second recovery is not physically idempotent")
    return one, two


def _drain_all(store: TrimmableRetirementDescriptorPrimaryStore) -> None:
    while store.retirement_queue_snapshot()["queue_count"]:
        store.reclaim_step(budget=2)


def _build_three_free_fixture(path: Path) -> tuple[int, int]:
    store = TrimmableRetirementDescriptorPrimaryStore(str(path))
    store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=4096)
    for index in range(65):
        store.insert(f"k-{index:03d}")
    _drain_all(store)
    snapshot = store.retirement_queue_snapshot()
    if snapshot["descriptor_free_count"] != 3:
        raise AssertionError("v0.36 trim fixture lacks three free descriptors")
    head = snapshot["free_descriptors"][0]
    page = int(head["descriptor_page"])
    incarnation = int(head["descriptor_incarnation"])
    meta = store.meta_snapshot()
    if page + 2 != int(meta["next_physical_page"]):
        raise AssertionError("v0.36 trim fixture free head is not physical tail")
    return page, incarnation


def run_tail_release_crash_matrix() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v036-tail-release-crash-") as tmp:
        directory = Path(tmp)
        base_path = directory / "base.pages"
        released_page, released_incarnation = _build_three_free_fixture(base_path)
        base = TrimmableRetirementDescriptorPrimaryStore(str(base_path))
        keys = tuple(f"k-{index:03d}" for index in range(65))
        pre = _semantic_state(base, keys)
        pre_size = int(base.meta_snapshot()["file_size_bytes"])

        clean_path = directory / "clean.pages"
        _sparse_copy(base_path, clean_path)
        clean = TrimmableRetirementDescriptorPrimaryStore(str(clean_path))
        clean_trace = clean.trim_retirement_descriptor_tail_step()
        post = _semantic_state(clean, keys)
        post_size = int(clean.meta_snapshot()["file_size_bytes"])
        if pre_size - post_size != 2 * 4096:
            raise AssertionError("v0.36 clean tail release did not shrink by one descriptor pair")

        rows: list[dict[str, Any]] = []
        for failpoint in TRIM_FAILPOINTS:
            crash_path = directory / f"{failpoint}.pages"
            _sparse_copy(base_path, crash_path)
            proc = _worker(crash_path, "trim", failpoint=failpoint)
            _require_sigkill(proc, f"v0.36 trim failpoint {failpoint}")
            crashed = TrimmableRetirementDescriptorPrimaryStore(str(crash_path))
            observed = _semantic_state(crashed, keys)
            expected_post = failpoint != "descriptor_tail_release_selected"
            expected = post if expected_post else pre
            if observed != expected:
                raise AssertionError(f"v0.36 trim crash state drifted at {failpoint}")
            recovery_one, recovery_two = _recover_twice(crashed)
            if failpoint == "committed":
                if int(recovery_one["physical_truncated_bytes"]) != 2 * 4096:
                    raise AssertionError("v0.36 committed-before-truncate crash did not derive tail cleanup")
            else:
                if int(recovery_one["physical_truncated_bytes"]) != 0:
                    raise AssertionError("v0.36 unexpected recovery truncation outside committed-before-truncate case")

            released_resolves = True
            try:
                crashed.retirement_descriptor_reference(
                    released_page,
                    released_incarnation,
                    expected_status=RETIREMENT_STATUS_FREE,
                )
            except RuntimeError:
                released_resolves = False
            if expected_post and released_resolves:
                raise AssertionError("v0.36 released identity resolved after committed tail release")
            if not expected_post and not released_resolves:
                raise AssertionError("v0.36 pre-commit crash lost committed free descriptor identity")

            rows.append(
                {
                    "failpoint": failpoint,
                    "expected_committed": expected_post,
                    "exact_committed_state_match": True,
                    "released_identity_resolves": released_resolves,
                    "recovery_one": recovery_one,
                    "recovery_two": recovery_two,
                }
            )
        return {
            "failpoints": list(TRIM_FAILPOINTS),
            "case_count": len(rows),
            "clean_trace": clean_trace.to_dict(),
            "released_descriptor_page": released_page,
            "released_descriptor_incarnation": released_incarnation,
            "rows": rows,
            "all_exact_committed_state_match": True,
            "all_recovery_scan_free": True,
            "all_second_recovery_idempotent": True,
        }


def _build_fresh_after_release_fixture(path: Path) -> tuple[int, int]:
    store = TrimmableRetirementDescriptorPrimaryStore(str(path))
    store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=4096)
    for index in range(17):
        store.insert(f"k-{index:03d}")
    _drain_all(store)
    free = store.retirement_queue_snapshot()
    if free["descriptor_free_count"] != 1:
        raise AssertionError("v0.36 fresh-after-release fixture lacks one free descriptor")
    released = free["free_descriptors"][0]
    released_page = int(released["descriptor_page"])
    released_incarnation = int(released["descriptor_incarnation"])
    trace = store.trim_retirement_descriptor_tail_step()
    if not trace.tail_release_eligible:
        raise AssertionError("v0.36 single descriptor was not tail-releasable")
    for index in range(17, 32):
        store.insert(f"k-{index:03d}")
    snapshot = store.retirement_queue_snapshot()
    if snapshot["descriptor_pool_count"] != 0:
        raise AssertionError("v0.36 descriptor pool regrew before fresh trigger")
    return released_page, released_incarnation


def run_fresh_after_release_crash_matrix() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v036-fresh-after-release-crash-") as tmp:
        directory = Path(tmp)
        base_path = directory / "base.pages"
        released_page, released_incarnation = _build_fresh_after_release_fixture(base_path)
        base = TrimmableRetirementDescriptorPrimaryStore(str(base_path))
        keys = tuple(f"k-{index:03d}" for index in range(33))
        pre = _semantic_state(base, keys)

        clean_path = directory / "clean.pages"
        _sparse_copy(base_path, clean_path)
        clean = TrimmableRetirementDescriptorPrimaryStore(str(clean_path))
        clean_trace = clean.insert("k-032")
        post = _semantic_state(clean, keys)
        queue = clean.retirement_queue_snapshot()
        if clean_trace.retirement_descriptor_pages_appended != 2:
            raise AssertionError("v0.36 fresh post-release enqueue did not allocate one descriptor pair")
        if clean_trace.retirement_descriptor_reuses != 0:
            raise AssertionError("v0.36 fresh post-release enqueue unexpectedly reused a descriptor")
        if [int(row["descriptor_incarnation"]) for row in queue["descriptors"]] != [2]:
            raise AssertionError("v0.36 fresh descriptor incarnation reset after physical release")

        rows: list[dict[str, Any]] = []
        for failpoint in FRESH_AFTER_RELEASE_FAILPOINTS:
            crash_path = directory / f"{failpoint}.pages"
            _sparse_copy(base_path, crash_path)
            proc = _worker(
                crash_path,
                "insert",
                key="k-032",
                failpoint=failpoint,
            )
            _require_sigkill(proc, f"v0.36 fresh-after-release failpoint {failpoint}")
            crashed = TrimmableRetirementDescriptorPrimaryStore(str(crash_path))
            observed = _semantic_state(crashed, keys)
            expected_post = failpoint == "committed"
            expected = post if expected_post else pre
            if observed != expected:
                raise AssertionError(
                    f"v0.36 fresh-after-release crash state drifted at {failpoint}"
                )
            recovery_one, recovery_two = _recover_twice(crashed)
            try:
                crashed.retirement_descriptor_reference(
                    released_page,
                    released_incarnation,
                    expected_status=RETIREMENT_STATUS_FREE,
                )
            except RuntimeError:
                pass
            else:
                raise AssertionError("v0.36 released stale identity resolved after later allocation")
            rows.append(
                {
                    "failpoint": failpoint,
                    "expected_committed": expected_post,
                    "exact_committed_state_match": True,
                    "released_identity_rejected": True,
                    "recovery_one": recovery_one,
                    "recovery_two": recovery_two,
                }
            )
        return {
            "failpoints": list(FRESH_AFTER_RELEASE_FAILPOINTS),
            "case_count": len(rows),
            "clean_trace": clean_trace.to_dict(),
            "clean_queue": queue,
            "released_descriptor_page": released_page,
            "released_descriptor_incarnation": released_incarnation,
            "rows": rows,
            "all_exact_committed_state_match": True,
            "all_released_identity_rejected": True,
            "all_recovery_scan_free": True,
            "all_second_recovery_idempotent": True,
        }


def run_trimmable_retirement_crash_matrices() -> dict[str, Any]:
    trim = run_tail_release_crash_matrix()
    fresh = run_fresh_after_release_crash_matrix()
    return {
        "tail_release": trim,
        "fresh_after_release": fresh,
        "case_count": int(trim["case_count"]) + int(fresh["case_count"]),
    }
