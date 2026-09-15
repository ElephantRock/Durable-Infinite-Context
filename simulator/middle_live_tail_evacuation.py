from __future__ import annotations

import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from simulator.retired_generation_reclamation import _sparse_copy
from storage.fixed_page_primary import PAGE_SIZE
from storage.middle_live_tail_evacuation_retirement_descriptor_primary import (
    MiddleLiveTailEvacuationRetirementDescriptorPrimaryStore,
)
from storage.multi_free_live_tail_evacuation_retirement_descriptor_primary import (
    MultiFreeLiveTailEvacuationRetirementDescriptorPrimaryStore,
)
from storage.retirement_descriptor_pool import RETIREMENT_STATUS_QUEUED

ROOT = Path(__file__).resolve().parent.parent
WORKER = ROOT / "middle_live_tail_evacuation_worker.py"
SEGMENT_BUDGET = 2
EVACUATION_FAILPOINTS = (
    "retirement_middle_tail_destination_staged",
    "retirement_middle_tail_predecessor_staged",
    "retirement_middle_tail_dependencies_synced",
    "committed",
    "retirement_middle_tail_relocation_committed",
    "retirement_arena_truncated",
    "retirement_middle_tail_relocation_synced",
)
EVACUATION_PRECOMMIT = set(EVACUATION_FAILPOINTS[:3])
EVACUATION_POSTCOMMIT_PRETRUNCATE = {
    "committed",
    "retirement_middle_tail_relocation_committed",
}
REUSE_FAILPOINTS = (
    "retirement_descriptor_reused",
    "retirement_tail_linked",
    "retirement_arena_synced",
    "data_synced",
    "committed",
)
REUSE_PRECOMMIT = set(REUSE_FAILPOINTS[:-1])


def _next_key(index: int) -> str:
    return f"k-{index:04d}"


def _insert_until_queue_count(store, *, index: int, target: int, cap: int = 65536) -> int:
    while int(store.retirement_queue_snapshot()["queue_count"]) < target:
        if index >= cap:
            raise AssertionError(f"v0.42 fixture did not reach queue count {target}")
        store.insert(_next_key(index))
        index += 1
    if int(store.retirement_queue_snapshot()["queue_count"]) != target:
        raise AssertionError("v0.42 fixture overshot requested queue count")
    return index


def _reclaim_to_queue_depth(store, *, target_depth: int) -> None:
    while int(store.retirement_queue_snapshot()["queue_count"]) > target_depth:
        trace = store.reclaim_step(budget=SEGMENT_BUDGET)
        if int(trace.retirement_descriptors_scanned) != 0:
            raise AssertionError("v0.42 setup reclaim scanned retirement descriptors")
    if int(store.retirement_queue_snapshot()["queue_count"]) != target_depth:
        raise AssertionError("v0.42 setup reclaim overshot target depth")


def _copy_store(source: Path, destination: Path, store_cls) -> None:
    _sparse_copy(source, destination)
    source_arena = store_cls.arena_path_for(source)
    destination_arena = store_cls.arena_path_for(destination)
    shutil.copyfile(source_arena, destination_arena)


def _prepare_pre_reuse_fixture(path: Path, store_cls) -> dict[str, Any]:
    store = store_cls(str(path))
    store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=4096)
    index = _insert_until_queue_count(store, index=0, target=4)
    initial = store.retirement_queue_snapshot()
    if [int(row["descriptor_page"]) for row in initial["descriptors"]] != [0, 2, 4, 6]:
        raise AssertionError("v0.42 initial four-descriptor layout drifted")

    _reclaim_to_queue_depth(store, target_depth=2)
    reclaimed = store.retirement_queue_snapshot()
    if [int(row["descriptor_page"]) for row in reclaimed["descriptors"]] != [4, 6]:
        raise AssertionError("v0.42 reclaimed live queue is not [4,6]")
    if [int(row["descriptor_page"]) for row in reclaimed["free_descriptors"]] != [2, 0]:
        raise AssertionError("v0.42 reclaimed FREE chain is not [2,0]")
    if int(reclaimed["descriptor_arena_pages"]) != 8:
        raise AssertionError("v0.42 reclaimed arena frontier drifted")

    # Find the exact next insertion that creates a retirement descriptor through
    # current FREE-head reuse without perturbing the pre-trigger oracle.
    while index < 65536:
        key = _next_key(index)
        with tempfile.TemporaryDirectory(prefix="dic-v042-reuse-probe-") as probe_tmp:
            probe_path = Path(probe_tmp) / "probe.pages"
            _copy_store(path, probe_path, store_cls)
            probe = store_cls(str(probe_path))
            trace = probe.insert(key)
            after = probe.retirement_queue_snapshot()
            if int(after["queue_count"]) == 3:
                if int(getattr(trace, "retirement_descriptor_reuses", 0)) != 1:
                    raise AssertionError("v0.42 trigger did not reuse exactly one FREE descriptor")
                return {
                    "next_key_index": index,
                    "trigger_key": key,
                    "pre_queue": store.retirement_queue_snapshot(),
                    "initial_queue": initial,
                    "observed_keys": tuple(_next_key(i) for i in range(index + 1)),
                }
        store.insert(key)
        index += 1
    raise AssertionError("v0.42 did not find a FREE-reuse enqueue trigger")


def _advance_to_interior_fixture(path: Path, store_cls) -> dict[str, Any]:
    fixture = _prepare_pre_reuse_fixture(path, store_cls)
    store = store_cls(str(path))
    trace = store.insert(str(fixture["trigger_key"]))
    if int(getattr(trace, "retirement_descriptor_reuses", 0)) != 1:
        raise AssertionError("v0.42 interior fixture did not reuse one descriptor")
    ready = store.retirement_queue_snapshot()
    if [int(row["descriptor_page"]) for row in ready["descriptors"]] != [4, 6, 2]:
        raise AssertionError("v0.42 interior fixture queue is not [4,6,2]")
    if [int(row["descriptor_page"]) for row in ready["free_descriptors"]] != [0]:
        raise AssertionError("v0.42 interior fixture FREE list is not [0]")
    if int(ready["tail_page"]) != 2:
        raise AssertionError("v0.42 interior fixture logical tail drifted")
    fixture["ready_queue"] = ready
    fixture["trigger_trace"] = trace
    return fixture


def _semantic_state(
    store: MiddleLiveTailEvacuationRetirementDescriptorPrimaryStore,
    keys: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "state": store.committed_state(keys),
        "queue": store.retirement_queue_snapshot(),
        "arena": store.descriptor_arena_diagnostic(),
    }


def _require_scan_free(recovery: dict[str, Any]) -> None:
    if int(recovery["logical_work"]) != 0:
        raise AssertionError("v0.42 recovery required logical redo")
    if int(recovery["generation_pages_scanned"]) != 0:
        raise AssertionError("v0.42 recovery scanned generation pages")
    if int(recovery["mapping_nodes_scanned"]) != 0:
        raise AssertionError("v0.42 recovery scanned radix nodes")
    if int(recovery.get("retirement_descriptors_scanned", 0)) != 0:
        raise AssertionError("v0.42 recovery scanned retirement descriptors")


def _recover_twice(
    store: MiddleLiveTailEvacuationRetirementDescriptorPrimaryStore,
) -> tuple[dict[str, Any], dict[str, Any]]:
    one = store.recover()
    two = store.recover()
    _require_scan_free(one)
    _require_scan_free(two)
    if int(two["physical_truncated_bytes"]) != 0:
        raise AssertionError("v0.42 second recovery changed primary physical length")
    if int(two["retirement_descriptor_arena_truncated_bytes"]) != 0:
        raise AssertionError("v0.42 second recovery changed descriptor arena length")
    return one, two


def _require_sigkill(proc: subprocess.CompletedProcess[str], label: str) -> None:
    if proc.returncode != -signal.SIGKILL:
        raise AssertionError(f"{label} did not SIGKILL: {proc.returncode} {proc.stderr}")


def _worker_evacuate(path: Path, *, failpoint: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(WORKER),
            "--file",
            str(path),
            "evacuate",
            "--failpoint",
            failpoint,
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


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


def _v041_control_case() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v042-v041-control-") as tmp:
        path = Path(tmp) / "primary.pages"
        fixture = _advance_to_interior_fixture(
            path,
            MultiFreeLiveTailEvacuationRetirementDescriptorPrimaryStore,
        )
        store = MultiFreeLiveTailEvacuationRetirementDescriptorPrimaryStore(str(path))
        before = store.retirement_queue_snapshot()
        arena_before = store.descriptor_arena_diagnostic()
        trace = store.evacuate_live_retirement_arena_tail_step()
        after = store.retirement_queue_snapshot()
        arena_after = store.descriptor_arena_diagnostic()
        if trace.released:
            raise AssertionError("v0.41 control unexpectedly evacuated an interior physical tail")
        if int(trace.retirement_descriptor_pwrites) != 0:
            raise AssertionError("v0.41 control wrote descriptor state")
        if int(trace.retirement_descriptors_scanned) != 0 or int(trace.live_descriptor_relocations) != 0:
            raise AssertionError("v0.41 control performed non-local work")
        if before != after or arena_before != arena_after:
            raise AssertionError("v0.41 control changed committed state")
        return {
            "trigger_key": fixture["trigger_key"],
            "queue_before": before,
            "arena_before": arena_before,
            "trace": trace.to_dict(),
            "queue_after": after,
            "arena_after": arena_after,
            "exact_state_unchanged": True,
        }


def _candidate_case() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v042-release-") as tmp:
        path = Path(tmp) / "primary.pages"
        fixture = _advance_to_interior_fixture(
            path,
            MiddleLiveTailEvacuationRetirementDescriptorPrimaryStore,
        )
        store = MiddleLiveTailEvacuationRetirementDescriptorPrimaryStore(str(path))
        before = store.retirement_queue_snapshot()
        arena_before = store.descriptor_arena_diagnostic()

        if int(before["physical_tail_page"]) != 6:
            raise AssertionError("v0.42 physical-tail authority is not page 6")
        if int(before["physical_tail_predecessor_page"]) != 4:
            raise AssertionError("v0.42 physical-tail predecessor authority is not page 4")
        old_tail_incarnation = int(before["physical_tail_incarnation"])
        old_destination_incarnation = int(before["descriptor_free_head_incarnation"])
        successor_incarnation = int(before["tail_incarnation"])
        old_tail = store.retirement_descriptor_reference(
            6,
            old_tail_incarnation,
            expected_status=RETIREMENT_STATUS_QUEUED,
        )

        trace = store.evacuate_middle_live_retirement_arena_tail_step()
        after = store.retirement_queue_snapshot()
        arena_after = store.descriptor_arena_diagnostic()
        if not trace.released:
            raise AssertionError("v0.42 did not evacuate the interior physical tail")
        if int(trace.retirement_descriptor_preads) != 8 or int(trace.retirement_descriptor_pwrites) != 2:
            raise AssertionError("v0.42 interior relocation work drifted from 8R/2W")
        if int(trace.retirement_descriptors_scanned) != 0 or int(trace.live_descriptor_relocations) != 1:
            raise AssertionError("v0.42 interior relocation performed non-local work")
        if [int(row["descriptor_page"]) for row in after["descriptors"]] != [4, 0, 2]:
            raise AssertionError("v0.42 relocated queue is not [4,0,2]")
        if after["free_descriptors"]:
            raise AssertionError("v0.42 relocation did not consume the sole FREE destination")
        if int(after["descriptor_arena_pages"]) != 6:
            raise AssertionError("v0.42 committed arena frontier did not shrink 8 -> 6")
        if int(arena_before["arena_file_bytes"]) != 8 * PAGE_SIZE:
            raise AssertionError("v0.42 pre-relocation physical arena length drifted")
        if int(arena_after["arena_file_bytes"]) != 6 * PAGE_SIZE:
            raise AssertionError("v0.42 post-relocation physical arena length drifted")
        if int(after["head_page"]) != 4 or int(after["tail_page"]) != 2:
            raise AssertionError("v0.42 queue head/tail identity drifted")
        if int(after["tail_predecessor_page"]) != 0:
            raise AssertionError("v0.42 logical-tail predecessor was not redirected")
        if int(after["physical_tail_page"]) != 4:
            raise AssertionError("v0.42 new physical tail is not predecessor page 4")
        if after["physical_tail_predecessor_page"] is not None:
            raise AssertionError("v0.42 new physical queue-head tail unexpectedly has predecessor")

        stale_tail_rejected = False
        try:
            store.retirement_descriptor_reference(
                6,
                old_tail_incarnation,
                expected_status=RETIREMENT_STATUS_QUEUED,
            )
        except RuntimeError:
            stale_tail_rejected = True
        if not stale_tail_rejected:
            raise AssertionError("v0.42 truncated physical-tail identity remained visible")

        stale_destination_rejected = False
        try:
            store.retirement_descriptor_reference(
                0,
                old_destination_incarnation,
                expected_status=RETIREMENT_STATUS_QUEUED,
            )
        except RuntimeError:
            stale_destination_rejected = True
        if not stale_destination_rejected:
            raise AssertionError("v0.42 old FREE destination identity aliased relocated live state")

        successor = store.retirement_descriptor_reference(
            2,
            successor_incarnation,
            expected_status=RETIREMENT_STATUS_QUEUED,
        )
        relocated = store.retirement_descriptor_reference(
            0,
            int(trace.destination_new_incarnation or 0),
            expected_status=RETIREMENT_STATUS_QUEUED,
        )
        if int(relocated["generation"]) != int(old_tail["generation"]):
            raise AssertionError("v0.42 relocated descriptor generation drifted")
        if int(relocated["cursor_header_page"]) != int(old_tail["cursor_header_page"]):
            raise AssertionError("v0.42 relocated descriptor cursor drifted")
        if int(relocated["remaining_segments"]) != int(old_tail["remaining_segments"]):
            raise AssertionError("v0.42 relocated descriptor remaining count drifted")
        if int(relocated["next_descriptor_page"] or -1) != 2:
            raise AssertionError("v0.42 relocated descriptor lost outbound successor")
        if int(relocated["next_descriptor_incarnation"] or -1) != successor_incarnation:
            raise AssertionError("v0.42 relocated descriptor successor incarnation drifted")

        return {
            "trigger_key": fixture["trigger_key"],
            "queue_before": before,
            "arena_before": arena_before,
            "trace": trace.to_dict(),
            "queue_after": after,
            "arena_after": arena_after,
            "released_arena_pages": 2,
            "released_arena_bytes": 2 * PAGE_SIZE,
            "stale_tail_rejected": stale_tail_rejected,
            "stale_destination_rejected": stale_destination_rejected,
            "successor_identity_preserved": int(successor["incarnation"]) == successor_incarnation,
            "relocated_payload_preserved": True,
        }


def _relocation_crash_matrix() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v042-relocate-crash-") as tmp:
        directory = Path(tmp)
        base_path = directory / "base.pages"
        fixture = _advance_to_interior_fixture(
            base_path,
            MiddleLiveTailEvacuationRetirementDescriptorPrimaryStore,
        )
        keys = tuple(fixture["observed_keys"])
        base = MiddleLiveTailEvacuationRetirementDescriptorPrimaryStore(str(base_path))
        pre = _semantic_state(base, keys)

        clean_path = directory / "clean.pages"
        _copy_store(base_path, clean_path, MiddleLiveTailEvacuationRetirementDescriptorPrimaryStore)
        clean = MiddleLiveTailEvacuationRetirementDescriptorPrimaryStore(str(clean_path))
        clean_trace = clean.evacuate_middle_live_retirement_arena_tail_step()
        if not clean_trace.released:
            raise AssertionError("v0.42 clean crash oracle did not relocate interior tail")
        post = _semantic_state(clean, keys)

        cases: list[dict[str, Any]] = []
        for failpoint in EVACUATION_FAILPOINTS:
            crash_path = directory / f"crash-{failpoint}.pages"
            _copy_store(base_path, crash_path, MiddleLiveTailEvacuationRetirementDescriptorPrimaryStore)
            proc = _worker_evacuate(crash_path, failpoint=failpoint)
            _require_sigkill(proc, f"middle-relocation/{failpoint}")

            crashed = MiddleLiveTailEvacuationRetirementDescriptorPrimaryStore(str(crash_path))
            arena_before_recovery = crashed.descriptor_arena_diagnostic()
            one, two = _recover_twice(crashed)
            recovered = _semantic_state(crashed, keys)
            expected = pre if failpoint in EVACUATION_PRECOMMIT else post
            if recovered != expected:
                raise AssertionError(f"v0.42 relocation {failpoint} recovered wrong committed state")
            expected_truncated = (
                2 * PAGE_SIZE if failpoint in EVACUATION_POSTCOMMIT_PRETRUNCATE else 0
            )
            observed_truncated = int(one["retirement_descriptor_arena_truncated_bytes"])
            if observed_truncated != expected_truncated:
                raise AssertionError(
                    f"v0.42 relocation {failpoint} recovery truncated {observed_truncated}, "
                    f"expected {expected_truncated}"
                )
            cases.append(
                {
                    "failpoint": failpoint,
                    "expected_state": "pre" if failpoint in EVACUATION_PRECOMMIT else "post",
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
            "failpoints": list(EVACUATION_FAILPOINTS),
            "pre_state": pre,
            "clean_post_state": post,
            "clean_trace": clean_trace.to_dict(),
            "cases": cases,
            "all_exact_committed_state_match": all(bool(row["exact_committed_state_match"]) for row in cases),
            "all_recovery_scan_free": all(bool(row["recovery_scan_free"]) for row in cases),
            "all_second_recovery_idempotent": all(bool(row["second_recovery_idempotent"]) for row in cases),
        }


def _authority_publication_crash_matrix() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v042-authority-crash-") as tmp:
        directory = Path(tmp)
        base_path = directory / "base.pages"
        fixture = _prepare_pre_reuse_fixture(
            base_path,
            MiddleLiveTailEvacuationRetirementDescriptorPrimaryStore,
        )
        trigger_key = str(fixture["trigger_key"])
        keys = tuple(fixture["observed_keys"])
        base = MiddleLiveTailEvacuationRetirementDescriptorPrimaryStore(str(base_path))
        pre = _semantic_state(base, keys)

        clean_path = directory / "clean.pages"
        _copy_store(base_path, clean_path, MiddleLiveTailEvacuationRetirementDescriptorPrimaryStore)
        clean = MiddleLiveTailEvacuationRetirementDescriptorPrimaryStore(str(clean_path))
        clean_insert = clean.insert(trigger_key)
        post = _semantic_state(clean, keys)
        post_queue = post["queue"]
        if [int(row["descriptor_page"]) for row in post_queue["descriptors"]] != [4, 6, 2]:
            raise AssertionError("v0.42 clean authority trigger did not create [4,6,2]")
        if int(post_queue["physical_tail_page"]) != 6 or int(post_queue["physical_tail_predecessor_page"]) != 4:
            raise AssertionError("v0.42 clean trigger did not publish interior physical-tail authority")

        clean_trace = {
            "retirement_descriptor_reuses": int(clean_insert.retirement_descriptor_reuses),
            "retirement_descriptor_preads": int(clean_insert.retirement_descriptor_preads),
            "retirement_descriptor_pwrites": int(clean_insert.retirement_descriptor_pwrites),
            "retirement_queue_count": int(clean_insert.retirement_queue_count),
            "retirement_descriptor_free_count": int(clean_insert.retirement_descriptor_free_count),
            "retirement_arena_pages": int(clean_insert.retirement_arena_pages),
        }

        cases: list[dict[str, Any]] = []
        for failpoint in REUSE_FAILPOINTS:
            crash_path = directory / f"crash-{failpoint}.pages"
            _copy_store(base_path, crash_path, MiddleLiveTailEvacuationRetirementDescriptorPrimaryStore)
            proc = _worker_insert(crash_path, key=trigger_key, failpoint=failpoint)
            _require_sigkill(proc, f"physical-tail-authority/{failpoint}")

            crashed = MiddleLiveTailEvacuationRetirementDescriptorPrimaryStore(str(crash_path))
            arena_before_recovery = crashed.descriptor_arena_diagnostic()
            one, two = _recover_twice(crashed)
            recovered = _semantic_state(crashed, keys)
            expected = pre if failpoint in REUSE_PRECOMMIT else post
            if recovered != expected:
                raise AssertionError(f"v0.42 authority {failpoint} recovered wrong committed state")
            if int(one["retirement_descriptor_arena_truncated_bytes"]) != 0:
                raise AssertionError("v0.42 FREE-reuse authority recovery changed arena length")
            cases.append(
                {
                    "failpoint": failpoint,
                    "expected_state": "pre" if failpoint in REUSE_PRECOMMIT else "post",
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
            "failpoints": list(REUSE_FAILPOINTS),
            "trigger_key": trigger_key,
            "trigger_index": int(fixture["next_key_index"]),
            "pre_state": pre,
            "clean_post_state": post,
            "clean_trace": clean_trace,
            "cases": cases,
            "all_exact_committed_state_match": all(bool(row["exact_committed_state_match"]) for row in cases),
            "all_recovery_scan_free": all(bool(row["recovery_scan_free"]) for row in cases),
            "all_second_recovery_idempotent": all(bool(row["second_recovery_idempotent"]) for row in cases),
        }


def run_middle_live_tail_evacuation_experiment() -> dict[str, Any]:
    return {
        "experiment": "v0.42-middle-live-tail-evacuation",
        "survived": True,
        "v041_control": _v041_control_case(),
        "middle_tail_evacuation": _candidate_case(),
        "relocation_crash_matrix": _relocation_crash_matrix(),
        "physical_tail_authority_crashes": _authority_publication_crash_matrix(),
        "claim_boundary": {
            "queue_count": 3,
            "physical_tail_predecessor_is_queue_head": True,
            "physical_tail_successor_is_queue_tail": True,
            "sole_free_destination": True,
            "queue_or_history_walk": False,
        },
    }
