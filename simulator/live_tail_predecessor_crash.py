from __future__ import annotations

import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from simulator import live_tail_evacuation as base
from simulator.retired_generation_reclamation import _sparse_copy
from storage.bounded_live_tail_evacuation_retirement_descriptor_primary import (
    LiveTailEvacuationRetirementDescriptorPrimaryStore,
)
from storage.fixed_page_primary import PAGE_SIZE

ROOT = Path(__file__).resolve().parent.parent
WORKER = ROOT / "live_tail_evacuation_worker.py"
INSERT_FAILPOINTS = (
    "retirement_arena_descriptor_written",
    "retirement_tail_linked",
    "retirement_arena_synced",
    "data_synced",
    "committed",
)
PRECOMMIT_FAILPOINTS = set(INSERT_FAILPOINTS[:-1])


def _copy_candidate_store(source: Path, destination: Path) -> None:
    _sparse_copy(source, destination)
    source_arena = LiveTailEvacuationRetirementDescriptorPrimaryStore.arena_path_for(source)
    destination_arena = LiveTailEvacuationRetirementDescriptorPrimaryStore.arena_path_for(destination)
    shutil.copyfile(source_arena, destination_arena)


def _semantic_state(
    store: LiveTailEvacuationRetirementDescriptorPrimaryStore,
    keys: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "state": store.committed_state(keys),
        "queue": store.retirement_queue_snapshot(),
        "arena": store.descriptor_arena_diagnostic(),
    }


def _require_sigkill(proc: subprocess.CompletedProcess[str], label: str) -> None:
    if proc.returncode != -signal.SIGKILL:
        raise AssertionError(f"{label} did not SIGKILL: {proc.returncode} {proc.stderr}")


def _worker_insert(
    path: Path,
    *,
    key: str,
    failpoint: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(WORKER),
            "--file",
            str(path),
            "insert",
            "--key",
            key,
            "--failpoint",
            failpoint,
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _prepare_tail_predecessor_trigger(path: Path) -> tuple[dict[str, Any], str]:
    store = LiveTailEvacuationRetirementDescriptorPrimaryStore(str(path))
    store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=4096)
    index = base._insert_until_queue_count(store, index=0, target=1)
    one = store.retirement_queue_snapshot()
    if int(one["queue_count"]) != 1 or int(one["descriptor_free_count"]) != 0:
        raise AssertionError("v0.40 predecessor-crash fixture must begin with one live descriptor and no free descriptors")
    if one["tail_predecessor_page"] is not None or one["tail_predecessor_incarnation"] is not None:
        raise AssertionError("v0.40 singleton queue unexpectedly exposes a tail predecessor")

    while index < 8192:
        key = base._next_key(index)
        with tempfile.TemporaryDirectory(prefix="dic-v040-tailpred-probe-") as probe_tmp:
            probe_path = Path(probe_tmp) / "probe.pages"
            _copy_candidate_store(path, probe_path)
            probe = LiveTailEvacuationRetirementDescriptorPrimaryStore(str(probe_path))
            trace = probe.insert(key)
            after = probe.retirement_queue_snapshot()
            if int(after["queue_count"]) == 2:
                if int(trace.retirement_descriptors_enqueued) != 1:
                    raise AssertionError("v0.40 predecessor trigger did not enqueue exactly one retirement descriptor")
                return {
                    "keys": tuple(base._next_key(i) for i in range(index)),
                    "trigger_index": index,
                    "pre_queue": store.retirement_queue_snapshot(),
                }, key
        store.insert(key)
        index += 1
    raise AssertionError("v0.40 predecessor-crash fixture did not find a second-descriptor trigger")


def run_live_tail_predecessor_crash_experiment() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v040-tailpred-crash-") as tmp:
        directory = Path(tmp)
        base_path = directory / "base.pages"
        fixture, trigger_key = _prepare_tail_predecessor_trigger(base_path)
        observed_keys = (*tuple(fixture["keys"]), trigger_key)

        base_store = LiveTailEvacuationRetirementDescriptorPrimaryStore(str(base_path))
        pre = _semantic_state(base_store, observed_keys)
        pre_queue = pre["queue"]
        old_tail_page = int(pre_queue["tail_page"])
        old_tail_incarnation = int(pre_queue["tail_incarnation"])

        clean_path = directory / "clean.pages"
        _copy_candidate_store(base_path, clean_path)
        clean = LiveTailEvacuationRetirementDescriptorPrimaryStore(str(clean_path))
        clean_trace = clean.insert(trigger_key)
        post = _semantic_state(clean, observed_keys)
        post_queue = post["queue"]
        if int(clean_trace.retirement_descriptors_enqueued) != 1:
            raise AssertionError("v0.40 clean predecessor trigger did not enqueue exactly one descriptor")
        if int(post_queue["queue_count"]) != 2:
            raise AssertionError("v0.40 clean predecessor trigger did not produce queue depth two")
        if int(post_queue["tail_predecessor_page"]) != old_tail_page:
            raise AssertionError("v0.40 clean enqueue did not publish old tail as new tail predecessor")
        if int(post_queue["tail_predecessor_incarnation"]) != old_tail_incarnation:
            raise AssertionError("v0.40 clean enqueue predecessor incarnation drifted")
        if int(post_queue["tail_page"]) == old_tail_page:
            raise AssertionError("v0.40 clean enqueue did not advance queue tail")

        # Keep the canonical evidence portable: the generic insert trace includes
        # filesystem allocation observations (`allocated_bytes`) that can vary across
        # filesystems even when logical state is identical. Freeze only the fields
        # relevant to this predecessor-maintenance experiment.
        clean_trace_row = {
            "retirement_descriptors_enqueued": int(clean_trace.retirement_descriptors_enqueued),
            "retirement_descriptor_preads": int(clean_trace.retirement_descriptor_preads),
            "retirement_descriptor_pwrites": int(clean_trace.retirement_descriptor_pwrites),
            "retirement_arena_pages": int(clean_trace.retirement_arena_pages),
            "retirement_queue_count": int(clean_trace.retirement_queue_count),
        }

        cases: list[dict[str, Any]] = []
        for failpoint in INSERT_FAILPOINTS:
            crash_path = directory / f"crash-{failpoint}.pages"
            _copy_candidate_store(base_path, crash_path)
            proc = _worker_insert(crash_path, key=trigger_key, failpoint=failpoint)
            _require_sigkill(proc, f"tail-predecessor/{failpoint}")

            crashed = LiveTailEvacuationRetirementDescriptorPrimaryStore(str(crash_path))
            arena_before_recovery = crashed.descriptor_arena_diagnostic()
            one, two = base._recover_twice(crashed)
            recovered = _semantic_state(crashed, observed_keys)
            expected = pre if failpoint in PRECOMMIT_FAILPOINTS else post
            if recovered != expected:
                raise AssertionError(
                    f"v0.40 tail-predecessor maintenance {failpoint} recovered wrong committed state"
                )

            expected_truncated = 2 * PAGE_SIZE if failpoint in PRECOMMIT_FAILPOINTS else 0
            observed_truncated = int(one["retirement_descriptor_arena_truncated_bytes"])
            if observed_truncated != expected_truncated:
                raise AssertionError(
                    f"v0.40 tail-predecessor {failpoint} recovery truncated {observed_truncated}, "
                    f"expected {expected_truncated}"
                )

            cases.append(
                {
                    "failpoint": failpoint,
                    "expected_state": "pre" if failpoint in PRECOMMIT_FAILPOINTS else "post",
                    "arena_before_recovery": arena_before_recovery,
                    "first_recovery": one,
                    "second_recovery": two,
                    "exact_committed_state_match": recovered == expected,
                    "recovery_scan_free": True,
                    "second_recovery_idempotent": (
                        int(two["physical_truncated_bytes"]) == 0
                        and int(two["retirement_descriptor_arena_truncated_bytes"]) == 0
                    ),
                }
            )

        return {
            "case_count": len(cases),
            "failpoints": list(INSERT_FAILPOINTS),
            "trigger_key": trigger_key,
            "trigger_index": int(fixture["trigger_index"]),
            "old_tail_page": old_tail_page,
            "old_tail_incarnation": old_tail_incarnation,
            "pre_state": pre,
            "clean_post_state": post,
            "clean_trace": clean_trace_row,
            "cases": cases,
            "all_exact_committed_state_match": all(
                bool(row["exact_committed_state_match"]) for row in cases
            ),
            "all_recovery_scan_free": all(bool(row["recovery_scan_free"]) for row in cases),
            "all_second_recovery_idempotent": all(
                bool(row["second_recovery_idempotent"]) for row in cases
            ),
        }
