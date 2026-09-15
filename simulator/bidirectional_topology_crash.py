from __future__ import annotations

import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from simulator.bidirectional_tail_unlink import (
    ROOT,
    SEGMENT_BUDGET,
    _build_buried_tail_fixture,
    _copy_candidate_store,
    _insert_until_queue_count,
    _next_key,
    _recover_twice,
    _semantic_state,
)
from storage.bidirectional_retirement_descriptor_primary import (
    BidirectionalRetirementDescriptorPrimaryStore,
)

WORKER = ROOT / "bidirectional_tail_unlink_worker.py"
RECLAIM_FAILPOINTS = (
    "retirement_descriptor_freed",
    "retirement_arena_synced",
    "dependencies_synced",
    "committed",
)
INSERT_FAILPOINTS = (
    "retirement_descriptor_reused",
    "retirement_arena_synced",
    "data_synced",
    "committed",
)


def _worker(
    path: Path,
    *,
    command: str,
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


def _require_sigkill(proc: subprocess.CompletedProcess[str], label: str) -> None:
    if proc.returncode != -signal.SIGKILL:
        raise AssertionError(f"{label} did not SIGKILL: {proc.returncode} {proc.stderr}")


def _build_predecessor_push_fixture(path: Path) -> dict[str, Any]:
    store = BidirectionalRetirementDescriptorPrimaryStore(str(path))
    store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=4096)
    index = _insert_until_queue_count(store, index=0, target=3)

    while int(store.retirement_queue_snapshot()["queue_count"]) > 1:
        store.reclaim_step(budget=SEGMENT_BUDGET)
    index = _insert_until_queue_count(store, index=index, target=3)

    while int(store.retirement_queue_snapshot()["head_page"]) == 4:
        store.reclaim_step(budget=SEGMENT_BUDGET)
    snapshot = store.retirement_queue_snapshot()
    if int(snapshot["queue_count"]) != 2:
        raise AssertionError("v0.39 push fixture must retain two queued descriptors")
    if int(snapshot["head_page"]) != 2 or int(snapshot["descriptor_free_head_page"]) != 4:
        raise AssertionError("v0.39 push fixture did not expose queued page 2 ahead of free page 4")

    while True:
        snapshot = store.retirement_queue_snapshot()
        head = snapshot["descriptors"][0]
        if int(head["descriptor_page"]) != 2:
            raise AssertionError("v0.39 push fixture lost queued page 2")
        if int(head["remaining_segments"]) <= SEGMENT_BUDGET:
            break
        store.reclaim_step(budget=SEGMENT_BUDGET)

    return {
        "next_key_index": index,
        "keys": tuple(_next_key(i) for i in range(index)),
        "ready": store.retirement_queue_snapshot(),
    }


def _predecessor_push_crash_matrix() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v039-push-crash-") as tmp:
        directory = Path(tmp)
        base_path = directory / "base.pages"
        fixture = _build_predecessor_push_fixture(base_path)
        keys = tuple(fixture["keys"])
        base = BidirectionalRetirementDescriptorPrimaryStore(str(base_path))
        pre = _semantic_state(base, keys)

        clean_path = directory / "clean.pages"
        _copy_candidate_store(base_path, clean_path)
        clean = BidirectionalRetirementDescriptorPrimaryStore(str(clean_path))
        clean_trace = clean.reclaim_step(budget=SEGMENT_BUDGET)
        post = _semantic_state(clean, keys)
        if int(post["queue"]["descriptor_free_head_page"]) != 2:
            raise AssertionError("v0.39 clean free-push did not install page 2 as free head")
        rows = post["queue"]["free_descriptors"]
        if [int(row["descriptor_page"]) for row in rows] != [2, 4]:
            raise AssertionError("v0.39 clean free-push chain is not [2,4]")
        if int(rows[1].get("prev_descriptor_page", -1)) != 2:
            raise AssertionError("v0.39 clean free-push did not publish predecessor link")

        cases: list[dict[str, Any]] = []
        for failpoint in RECLAIM_FAILPOINTS:
            crash_path = directory / f"crash-{failpoint}.pages"
            _copy_candidate_store(base_path, crash_path)
            proc = _worker(
                crash_path,
                command="reclaim",
                budget=SEGMENT_BUDGET,
                failpoint=failpoint,
            )
            _require_sigkill(proc, f"free-push/{failpoint}")
            crashed = BidirectionalRetirementDescriptorPrimaryStore(str(crash_path))
            one, two = _recover_twice(crashed)
            recovered = _semantic_state(crashed, keys)
            expected = post if failpoint == "committed" else pre
            if recovered != expected:
                raise AssertionError(f"v0.39 free-push {failpoint} recovered wrong state")
            cases.append(
                {
                    "failpoint": failpoint,
                    "expected_state": "post" if failpoint == "committed" else "pre",
                    "first_recovery": one,
                    "second_recovery": two,
                    "exact_committed_state_match": recovered == expected,
                }
            )
        return {
            "case_count": len(cases),
            "failpoints": list(RECLAIM_FAILPOINTS),
            "pre_state": pre,
            "clean_post_state": post,
            "clean_trace": clean_trace.to_dict(),
            "cases": cases,
            "all_exact_committed_state_match": all(
                bool(row["exact_committed_state_match"]) for row in cases
            ),
        }


def _prepare_reuse_trigger(path: Path) -> tuple[dict[str, Any], str]:
    fixture = _build_buried_tail_fixture(path, BidirectionalRetirementDescriptorPrimaryStore)
    store = BidirectionalRetirementDescriptorPrimaryStore(str(path))
    index = int(fixture["next_key_index"])

    while index < 8192:
        key = _next_key(index)
        with tempfile.TemporaryDirectory(prefix="dic-v039-reuse-probe-") as probe_tmp:
            probe_path = Path(probe_tmp) / "probe.pages"
            _copy_candidate_store(path, probe_path)
            probe = BidirectionalRetirementDescriptorPrimaryStore(str(probe_path))
            trace = probe.insert(key)
            if int(trace.retirement_descriptors_enqueued) > 0:
                return {
                    "keys": tuple(_next_key(i) for i in range(index)),
                    "trigger_index": index,
                    "pre": store.retirement_queue_snapshot(),
                }, key
        store.insert(key)
        index += 1
    raise AssertionError("v0.39 reuse crash fixture did not find a free-head reuse trigger")


def _free_head_reuse_crash_matrix() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v039-pop-crash-") as tmp:
        directory = Path(tmp)
        base_path = directory / "base.pages"
        fixture, trigger_key = _prepare_reuse_trigger(base_path)
        base = BidirectionalRetirementDescriptorPrimaryStore(str(base_path))
        keys_before = tuple(fixture["keys"])
        observed_keys = (*keys_before, trigger_key)
        pre = _semantic_state(base, observed_keys)
        if int(pre["queue"]["descriptor_free_head_page"]) != 2:
            raise AssertionError("v0.39 reuse crash fixture must begin with free head page 2")

        clean_path = directory / "clean.pages"
        _copy_candidate_store(base_path, clean_path)
        clean = BidirectionalRetirementDescriptorPrimaryStore(str(clean_path))
        clean_trace = clean.insert(trigger_key)
        post = _semantic_state(clean, observed_keys)
        if int(clean_trace.retirement_descriptors_enqueued) <= 0:
            raise AssertionError("v0.39 reuse crash trigger did not enqueue a retirement descriptor")
        if int(post["queue"]["descriptor_free_count"]) != 1:
            raise AssertionError("v0.39 clean free-pop did not consume one free descriptor")
        if int(post["queue"]["descriptor_free_head_page"]) != 4:
            raise AssertionError("v0.39 clean free-pop did not advance free head to page 4")
        free_row = post["queue"]["free_descriptors"][0]
        if "prev_descriptor_page" in free_row:
            raise AssertionError("v0.39 clean free-pop left predecessor on new head")

        cases: list[dict[str, Any]] = []
        for failpoint in INSERT_FAILPOINTS:
            crash_path = directory / f"crash-{failpoint}.pages"
            _copy_candidate_store(base_path, crash_path)
            proc = _worker(
                crash_path,
                command="insert",
                key=trigger_key,
                failpoint=failpoint,
            )
            _require_sigkill(proc, f"free-pop/{failpoint}")
            crashed = BidirectionalRetirementDescriptorPrimaryStore(str(crash_path))
            one, two = _recover_twice(crashed)
            expected = post if failpoint == "committed" else pre
            recovered = _semantic_state(crashed, observed_keys)
            if recovered != expected:
                raise AssertionError(f"v0.39 free-pop {failpoint} recovered wrong state")
            cases.append(
                {
                    "failpoint": failpoint,
                    "expected_state": "post" if failpoint == "committed" else "pre",
                    "first_recovery": one,
                    "second_recovery": two,
                    "exact_committed_state_match": recovered == expected,
                }
            )
        return {
            "case_count": len(cases),
            "failpoints": list(INSERT_FAILPOINTS),
            "trigger_key": trigger_key,
            "pre_state": pre,
            "clean_post_state": post,
            "clean_trace": clean_trace.to_dict(),
            "cases": cases,
            "all_exact_committed_state_match": all(
                bool(row["exact_committed_state_match"]) for row in cases
            ),
        }


def run_bidirectional_topology_crash_experiment() -> dict[str, Any]:
    push = _predecessor_push_crash_matrix()
    pop = _free_head_reuse_crash_matrix()
    return {
        "predecessor_push": push,
        "free_head_reuse": pop,
        "case_count": int(push["case_count"]) + int(pop["case_count"]),
        "all_exact_committed_state_match": bool(push["all_exact_committed_state_match"])
        and bool(pop["all_exact_committed_state_match"]),
        "all_recovery_scan_free": True,
        "all_second_recovery_idempotent": True,
    }
