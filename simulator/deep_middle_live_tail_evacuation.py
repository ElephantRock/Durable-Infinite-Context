from __future__ import annotations

import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from simulator.retired_generation_reclamation import _sparse_copy
from storage.deep_middle_live_tail_evacuation_retirement_descriptor_primary import (
    DeepMiddleLiveTailEvacuationRetirementDescriptorPrimaryStore,
)
from storage.fixed_page_primary import PAGE_SIZE
from storage.middle_live_tail_evacuation_retirement_descriptor_primary import (
    MiddleLiveTailEvacuationRetirementDescriptorPrimaryStore,
)
from storage.retirement_descriptor_pool import RETIREMENT_STATUS_QUEUED

ROOT = Path(__file__).resolve().parent.parent
WORKER = ROOT / "deep_middle_live_tail_evacuation_worker.py"
SEGMENT_BUDGET = 2
EVACUATION_FAILPOINTS = (
    "retirement_deep_tail_destination_staged",
    "retirement_deep_tail_predecessor_staged",
    "retirement_deep_tail_dependencies_synced",
    "committed",
    "retirement_deep_tail_relocation_committed",
    "retirement_arena_truncated",
    "retirement_deep_tail_relocation_synced",
)
EVACUATION_PRECOMMIT = set(EVACUATION_FAILPOINTS[:3])
EVACUATION_POSTCOMMIT_PRETRUNCATE = {
    "committed",
    "retirement_deep_tail_relocation_committed",
}
APPEND_FAILPOINTS = (
    "retirement_arena_descriptor_written",
    "retirement_tail_linked",
    "retirement_arena_synced",
    "data_synced",
    "committed",
)
REUSE_FAILPOINTS = (
    "retirement_descriptor_reused",
    "retirement_tail_linked",
    "retirement_arena_synced",
    "data_synced",
    "committed",
)


def _next_key(index: int) -> str:
    return f"k-{index:04d}"


def _insert_until_queue_count(store, *, index: int, target: int, cap: int = 65536) -> int:
    while int(store.retirement_queue_snapshot()["queue_count"]) < target:
        if index >= cap:
            raise AssertionError(f"v0.43 fixture did not reach queue count {target}")
        store.insert(_next_key(index))
        index += 1
    if int(store.retirement_queue_snapshot()["queue_count"]) != target:
        raise AssertionError("v0.43 fixture overshot requested queue count")
    return index


def _reclaim_to_queue_depth(store, *, target_depth: int) -> None:
    while int(store.retirement_queue_snapshot()["queue_count"]) > target_depth:
        trace = store.reclaim_step(budget=SEGMENT_BUDGET)
        if int(trace.retirement_descriptors_scanned) != 0:
            raise AssertionError("v0.43 setup reclaim scanned retirement descriptors")
    if int(store.retirement_queue_snapshot()["queue_count"]) != target_depth:
        raise AssertionError("v0.43 setup reclaim overshot target depth")


def _copy_store(source: Path, destination: Path, store_cls) -> None:
    _sparse_copy(source, destination)
    shutil.copyfile(store_cls.arena_path_for(source), store_cls.arena_path_for(destination))


def _find_next_enqueue_trigger(path: Path, store_cls, *, index: int, target: int) -> tuple[int, str]:
    while index < 65536:
        key = _next_key(index)
        with tempfile.TemporaryDirectory(prefix="dic-v043-probe-") as probe_tmp:
            probe_path = Path(probe_tmp) / "probe.pages"
            _copy_store(path, probe_path, store_cls)
            probe = store_cls(str(probe_path))
            probe.insert(key)
            if int(probe.retirement_queue_snapshot()["queue_count"]) == target:
                return index, key
        store_cls(str(path)).insert(key)
        index += 1
    raise AssertionError("v0.43 did not find next descriptor-producing insertion")


def _prepare_pre_append_fixture(path: Path) -> dict[str, Any]:
    store = DeepMiddleLiveTailEvacuationRetirementDescriptorPrimaryStore(str(path))
    store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=4096)
    index = _insert_until_queue_count(store, index=0, target=2)
    before = store.retirement_queue_snapshot()
    if [int(row["descriptor_page"]) for row in before["descriptors"]] != [0, 2]:
        raise AssertionError("v0.43 pre-append queue drifted")
    trigger_index, trigger_key = _find_next_enqueue_trigger(
        path,
        DeepMiddleLiveTailEvacuationRetirementDescriptorPrimaryStore,
        index=index,
        target=3,
    )
    return {
        "trigger_index": trigger_index,
        "trigger_key": trigger_key,
        "keys": tuple(_next_key(i) for i in range(trigger_index + 1)),
        "before": store.retirement_queue_snapshot(),
    }


def _prepare_pre_reuse_fixture(path: Path, store_cls) -> dict[str, Any]:
    store = store_cls(str(path))
    store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=4096)
    index = _insert_until_queue_count(store, index=0, target=5)
    initial = store.retirement_queue_snapshot()
    if [int(row["descriptor_page"]) for row in initial["descriptors"]] != [0, 2, 4, 6, 8]:
        raise AssertionError("v0.43 initial descriptor layout drifted")
    _reclaim_to_queue_depth(store, target_depth=3)
    before = store.retirement_queue_snapshot()
    if [int(row["descriptor_page"]) for row in before["descriptors"]] != [4, 6, 8]:
        raise AssertionError("v0.43 pre-reuse live queue drifted")
    if [int(row["descriptor_page"]) for row in before["free_descriptors"]] != [2, 0]:
        raise AssertionError("v0.43 pre-reuse FREE chain drifted")
    trigger_index, trigger_key = _find_next_enqueue_trigger(
        path,
        store_cls,
        index=index,
        target=4,
    )
    return {
        "trigger_index": trigger_index,
        "trigger_key": trigger_key,
        "keys": tuple(_next_key(i) for i in range(trigger_index + 1)),
        "before": store.retirement_queue_snapshot(),
    }


def _advance_to_deep_fixture(path: Path, store_cls) -> dict[str, Any]:
    fixture = _prepare_pre_reuse_fixture(path, store_cls)
    store = store_cls(str(path))
    trace = store.insert(str(fixture["trigger_key"]))
    ready = store.retirement_queue_snapshot()
    if [int(row["descriptor_page"]) for row in ready["descriptors"]] != [4, 6, 8, 2]:
        raise AssertionError("v0.43 ready queue is not [4,6,8,2]")
    if [int(row["descriptor_page"]) for row in ready["free_descriptors"]] != [0]:
        raise AssertionError("v0.43 ready FREE list is not [0]")
    fixture["ready"] = ready
    fixture["trigger_trace"] = trace
    return fixture


def _semantic_state(store, keys: tuple[str, ...]) -> dict[str, Any]:
    return {
        "state": store.committed_state(keys),
        "queue": store.retirement_queue_snapshot(),
        "arena": store.descriptor_arena_diagnostic(),
    }


def _require_scan_free(recovery: dict[str, Any]) -> None:
    if int(recovery["logical_work"]) != 0:
        raise AssertionError("v0.43 recovery required logical redo")
    if int(recovery["generation_pages_scanned"]) != 0:
        raise AssertionError("v0.43 recovery scanned generation pages")
    if int(recovery["mapping_nodes_scanned"]) != 0:
        raise AssertionError("v0.43 recovery scanned radix nodes")
    if int(recovery.get("retirement_descriptors_scanned", 0)) != 0:
        raise AssertionError("v0.43 recovery scanned retirement descriptors")


def _recover_twice(store) -> tuple[dict[str, Any], dict[str, Any]]:
    one = store.recover()
    two = store.recover()
    _require_scan_free(one)
    _require_scan_free(two)
    if int(two["physical_truncated_bytes"]) != 0:
        raise AssertionError("v0.43 second recovery changed primary physical length")
    if int(two["retirement_descriptor_arena_truncated_bytes"]) != 0:
        raise AssertionError("v0.43 second recovery changed descriptor arena length")
    return one, two


def _require_sigkill(proc: subprocess.CompletedProcess[str], label: str) -> None:
    if proc.returncode != -signal.SIGKILL:
        raise AssertionError(f"{label} did not SIGKILL: {proc.returncode} {proc.stderr}")


def _worker(path: Path, command: str, *, failpoint: str, key: str | None = None):
    args = [sys.executable, str(WORKER), "--file", str(path), command]
    if key is not None:
        args.extend(["--key", key])
    args.extend(["--failpoint", failpoint])
    return subprocess.run(args, cwd=ROOT, text=True, capture_output=True, check=False)


def _v042_control_case() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v043-v042-control-") as tmp:
        path = Path(tmp) / "primary.pages"
        fixture = _advance_to_deep_fixture(
            path,
            MiddleLiveTailEvacuationRetirementDescriptorPrimaryStore,
        )
        store = MiddleLiveTailEvacuationRetirementDescriptorPrimaryStore(str(path))
        before = store.retirement_queue_snapshot()
        trace = store.evacuate_middle_live_retirement_arena_tail_step()
        after = store.retirement_queue_snapshot()
        if trace.released or int(trace.retirement_descriptor_preads) != 0:
            raise AssertionError("v0.42 control did not refuse before descriptor I/O")
        if before != after:
            raise AssertionError("v0.42 control changed state")
        return {
            "trigger_key": fixture["trigger_key"],
            "queue_before": before,
            "trace": trace.to_dict(),
            "queue_after": after,
            "exact_state_unchanged": True,
        }


def _candidate_case() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v043-release-") as tmp:
        path = Path(tmp) / "primary.pages"
        fixture = _advance_to_deep_fixture(
            path,
            DeepMiddleLiveTailEvacuationRetirementDescriptorPrimaryStore,
        )
        store = DeepMiddleLiveTailEvacuationRetirementDescriptorPrimaryStore(str(path))
        before = store.retirement_queue_snapshot()
        arena_before = store.descriptor_arena_diagnostic()
        if int(before["physical_tail_page"]) != 8:
            raise AssertionError("v0.43 physical tail drifted")
        if int(before["physical_tail_predecessor_page"]) != 6:
            raise AssertionError("v0.43 physical-tail predecessor drifted")
        if int(before["physical_tail_predecessor_predecessor_page"]) != 4:
            raise AssertionError("v0.43 second reverse hop drifted")

        old_tail_inc = int(before["physical_tail_incarnation"])
        old_dest_inc = int(before["descriptor_free_head_incarnation"])
        successor_inc = int(before["tail_incarnation"])
        old_tail = store.retirement_descriptor_reference(
            8, old_tail_inc, expected_status=RETIREMENT_STATUS_QUEUED
        )
        trace = store.evacuate_deep_middle_live_retirement_arena_tail_step()
        after = store.retirement_queue_snapshot()
        arena_after = store.descriptor_arena_diagnostic()

        if not trace.released:
            raise AssertionError("v0.43 candidate did not release the deep live tail")
        if int(trace.retirement_descriptor_preads) != 10 or int(trace.retirement_descriptor_pwrites) != 2:
            raise AssertionError("v0.43 candidate work drifted from 10R/2W")
        if int(trace.retirement_descriptors_scanned) != 0 or int(trace.live_descriptor_relocations) != 1:
            raise AssertionError("v0.43 candidate performed non-local work")
        if [int(row["descriptor_page"]) for row in after["descriptors"]] != [4, 6, 0, 2]:
            raise AssertionError("v0.43 relocated queue is not [4,6,0,2]")
        if after["free_descriptors"]:
            raise AssertionError("v0.43 candidate did not consume the sole FREE destination")
        if int(after["descriptor_arena_pages"]) != 8:
            raise AssertionError("v0.43 candidate did not shrink 10 -> 8 pages")
        if int(after["physical_tail_page"]) != 6 or int(after["physical_tail_predecessor_page"]) != 4:
            raise AssertionError("v0.43 new physical-tail authority drifted")
        if after["physical_tail_predecessor_predecessor_page"] is not None:
            raise AssertionError("v0.43 new physical tail should have queue-head predecessor")
        if int(after["tail_page"]) != 2 or int(after["tail_predecessor_page"]) != 0:
            raise AssertionError("v0.43 logical-tail authority drifted")
        if int(arena_before["arena_file_bytes"]) != 10 * PAGE_SIZE:
            raise AssertionError("v0.43 pre-relocation arena length drifted")
        if int(arena_after["arena_file_bytes"]) != 8 * PAGE_SIZE:
            raise AssertionError("v0.43 post-relocation arena length drifted")

        stale_tail_rejected = False
        try:
            store.retirement_descriptor_reference(8, old_tail_inc, expected_status=RETIREMENT_STATUS_QUEUED)
        except RuntimeError:
            stale_tail_rejected = True
        stale_destination_rejected = False
        try:
            store.retirement_descriptor_reference(0, old_dest_inc, expected_status=RETIREMENT_STATUS_QUEUED)
        except RuntimeError:
            stale_destination_rejected = True
        if not stale_tail_rejected or not stale_destination_rejected:
            raise AssertionError("v0.43 stale identity rejection failed")

        successor = store.retirement_descriptor_reference(
            2, successor_inc, expected_status=RETIREMENT_STATUS_QUEUED
        )
        relocated = store.retirement_descriptor_reference(
            0, int(trace.destination_new_incarnation or 0), expected_status=RETIREMENT_STATUS_QUEUED
        )
        for name in ("generation", "cursor_header_page", "remaining_segments"):
            if int(relocated[name]) != int(old_tail[name]):
                raise AssertionError(f"v0.43 relocated payload drifted: {name}")
        if int(relocated["next_descriptor_page"] or -1) != 2:
            raise AssertionError("v0.43 relocated successor page drifted")
        if int(relocated["next_descriptor_incarnation"] or -1) != successor_inc:
            raise AssertionError("v0.43 relocated successor incarnation drifted")

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
            "successor_identity_preserved": int(successor["incarnation"]) == successor_inc,
            "relocated_payload_preserved": True,
        }


def _relocation_crash_matrix() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v043-relocate-crash-") as tmp:
        directory = Path(tmp)
        base_path = directory / "base.pages"
        fixture = _advance_to_deep_fixture(
            base_path,
            DeepMiddleLiveTailEvacuationRetirementDescriptorPrimaryStore,
        )
        keys = tuple(fixture["keys"])
        base = DeepMiddleLiveTailEvacuationRetirementDescriptorPrimaryStore(str(base_path))
        pre = _semantic_state(base, keys)
        clean_path = directory / "clean.pages"
        _copy_store(base_path, clean_path, DeepMiddleLiveTailEvacuationRetirementDescriptorPrimaryStore)
        clean = DeepMiddleLiveTailEvacuationRetirementDescriptorPrimaryStore(str(clean_path))
        clean_trace = clean.evacuate_deep_middle_live_retirement_arena_tail_step()
        post = _semantic_state(clean, keys)
        if not clean_trace.released:
            raise AssertionError("v0.43 relocation crash oracle did not release")

        cases = []
        for failpoint in EVACUATION_FAILPOINTS:
            crash_path = directory / f"crash-{failpoint}.pages"
            _copy_store(base_path, crash_path, DeepMiddleLiveTailEvacuationRetirementDescriptorPrimaryStore)
            proc = _worker(crash_path, "evacuate", failpoint=failpoint)
            _require_sigkill(proc, f"relocation/{failpoint}")
            crashed = DeepMiddleLiveTailEvacuationRetirementDescriptorPrimaryStore(str(crash_path))
            before_recovery = crashed.descriptor_arena_diagnostic()
            one, two = _recover_twice(crashed)
            recovered = _semantic_state(crashed, keys)
            expected = pre if failpoint in EVACUATION_PRECOMMIT else post
            if recovered != expected:
                raise AssertionError(f"v0.43 relocation {failpoint} recovered wrong state")
            expected_truncated = 2 * PAGE_SIZE if failpoint in EVACUATION_POSTCOMMIT_PRETRUNCATE else 0
            if int(one["retirement_descriptor_arena_truncated_bytes"]) != expected_truncated:
                raise AssertionError(f"v0.43 relocation {failpoint} recovery truncation drifted")
            cases.append(
                {
                    "failpoint": failpoint,
                    "expected_state": "pre" if failpoint in EVACUATION_PRECOMMIT else "post",
                    "arena_before_recovery": before_recovery,
                    "first_recovery": one,
                    "second_recovery": two,
                    "exact_committed_state_match": True,
                    "recovery_scan_free": True,
                    "second_recovery_idempotent": True,
                }
            )
        return {
            "case_count": len(cases),
            "failpoints": list(EVACUATION_FAILPOINTS),
            "clean_trace": clean_trace.to_dict(),
            "cases": cases,
            "all_exact_committed_state_match": True,
            "all_recovery_scan_free": True,
            "all_second_recovery_idempotent": True,
        }


def _insert_crash_matrix(*, mode: str) -> dict[str, Any]:
    if mode not in {"append", "reuse"}:
        raise ValueError(mode)
    with tempfile.TemporaryDirectory(prefix=f"dic-v043-{mode}-crash-") as tmp:
        directory = Path(tmp)
        base_path = directory / "base.pages"
        fixture = (
            _prepare_pre_append_fixture(base_path)
            if mode == "append"
            else _prepare_pre_reuse_fixture(
                base_path,
                DeepMiddleLiveTailEvacuationRetirementDescriptorPrimaryStore,
            )
        )
        trigger_key = str(fixture["trigger_key"])
        keys = tuple(fixture["keys"])
        base = DeepMiddleLiveTailEvacuationRetirementDescriptorPrimaryStore(str(base_path))
        pre = _semantic_state(base, keys)
        clean_path = directory / "clean.pages"
        _copy_store(base_path, clean_path, DeepMiddleLiveTailEvacuationRetirementDescriptorPrimaryStore)
        clean = DeepMiddleLiveTailEvacuationRetirementDescriptorPrimaryStore(str(clean_path))
        clean_trace = clean.insert(trigger_key)
        post = _semantic_state(clean, keys)
        post_queue = post["queue"]
        if mode == "append":
            if [int(row["descriptor_page"]) for row in post_queue["descriptors"]] != [0, 2, 4]:
                raise AssertionError("v0.43 append oracle topology drifted")
            if int(post_queue["physical_tail_page"]) != 4:
                raise AssertionError("v0.43 append oracle physical tail drifted")
            if int(post_queue["physical_tail_predecessor_page"]) != 2:
                raise AssertionError("v0.43 append oracle predecessor drifted")
            if int(post_queue["physical_tail_predecessor_predecessor_page"]) != 0:
                raise AssertionError("v0.43 append oracle second reverse hop drifted")
            failpoints = APPEND_FAILPOINTS
        else:
            if [int(row["descriptor_page"]) for row in post_queue["descriptors"]] != [4, 6, 8, 2]:
                raise AssertionError("v0.43 reuse oracle topology drifted")
            if int(post_queue["physical_tail_page"]) != 8:
                raise AssertionError("v0.43 reuse oracle physical tail drifted")
            if int(post_queue["physical_tail_predecessor_page"]) != 6:
                raise AssertionError("v0.43 reuse oracle predecessor drifted")
            if int(post_queue["physical_tail_predecessor_predecessor_page"]) != 4:
                raise AssertionError("v0.43 reuse oracle did not preserve second reverse hop")
            failpoints = REUSE_FAILPOINTS

        cases = []
        for failpoint in failpoints:
            crash_path = directory / f"crash-{failpoint}.pages"
            _copy_store(base_path, crash_path, DeepMiddleLiveTailEvacuationRetirementDescriptorPrimaryStore)
            proc = _worker(crash_path, "insert", failpoint=failpoint, key=trigger_key)
            _require_sigkill(proc, f"{mode}/{failpoint}")
            crashed = DeepMiddleLiveTailEvacuationRetirementDescriptorPrimaryStore(str(crash_path))
            before_recovery = crashed.descriptor_arena_diagnostic()
            one, two = _recover_twice(crashed)
            recovered = _semantic_state(crashed, keys)
            expected = post if failpoint == "committed" else pre
            if recovered != expected:
                raise AssertionError(f"v0.43 {mode} {failpoint} recovered wrong state")
            expected_arena_truncated = 0
            if mode == "append" and failpoint != "committed":
                expected_arena_truncated = 2 * PAGE_SIZE
            if int(one["retirement_descriptor_arena_truncated_bytes"]) != expected_arena_truncated:
                raise AssertionError(f"v0.43 {mode} {failpoint} arena recovery drifted")
            cases.append(
                {
                    "failpoint": failpoint,
                    "expected_state": "post" if failpoint == "committed" else "pre",
                    "arena_before_recovery": before_recovery,
                    "first_recovery": one,
                    "second_recovery": two,
                    "exact_committed_state_match": True,
                    "recovery_scan_free": True,
                    "second_recovery_idempotent": True,
                }
            )
        return {
            "mode": mode,
            "case_count": len(cases),
            "failpoints": list(failpoints),
            "trigger_index": int(fixture["trigger_index"]),
            "trigger_key": trigger_key,
            "clean_trace": {
                "retirement_descriptor_reuses": int(clean_trace.retirement_descriptor_reuses),
                "retirement_descriptor_preads": int(clean_trace.retirement_descriptor_preads),
                "retirement_descriptor_pwrites": int(clean_trace.retirement_descriptor_pwrites),
                "retirement_queue_count": int(clean_trace.retirement_queue_count),
                "retirement_descriptor_free_count": int(clean_trace.retirement_descriptor_free_count),
                "retirement_arena_pages": int(clean_trace.retirement_arena_pages),
            },
            "pre_queue": pre["queue"],
            "post_queue": post["queue"],
            "cases": cases,
            "all_exact_committed_state_match": True,
            "all_recovery_scan_free": True,
            "all_second_recovery_idempotent": True,
        }


def _deeper_prefix_refusal() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v043-deeper-refusal-") as tmp:
        path = Path(tmp) / "primary.pages"
        store = DeepMiddleLiveTailEvacuationRetirementDescriptorPrimaryStore(str(path))
        store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=4096)
        index = _insert_until_queue_count(store, index=0, target=6)
        _reclaim_to_queue_depth(store, target_depth=4)
        trigger_index, trigger_key = _find_next_enqueue_trigger(
            path,
            DeepMiddleLiveTailEvacuationRetirementDescriptorPrimaryStore,
            index=index,
            target=5,
        )
        store.insert(trigger_key)
        before = store.retirement_queue_snapshot()
        trace = store.evacuate_deep_middle_live_retirement_arena_tail_step()
        after = store.retirement_queue_snapshot()
        if trace.released or int(trace.retirement_descriptor_preads) != 0:
            raise AssertionError("v0.43 deeper prefix was not refused before descriptor I/O")
        if before != after:
            raise AssertionError("v0.43 deeper-prefix refusal changed state")
        return {
            "trigger_index": trigger_index,
            "trigger_key": trigger_key,
            "queue_before": before,
            "trace": trace.to_dict(),
            "queue_after": after,
            "exact_state_unchanged": True,
        }


def run_deep_middle_live_tail_evacuation_experiment() -> dict[str, Any]:
    return {
        "experiment": "v0.43-one-extra-hop-physical-tail-authority",
        "survived": True,
        "v042_control": _v042_control_case(),
        "deep_tail_evacuation": _candidate_case(),
        "relocation_crash_matrix": _relocation_crash_matrix(),
        "append_authority_crashes": _insert_crash_matrix(mode="append"),
        "reuse_authority_crashes": _insert_crash_matrix(mode="reuse"),
        "deeper_prefix_refusal": _deeper_prefix_refusal(),
        "claim_boundary": {
            "queue_count": 4,
            "physical_tail_predecessor_is_queue_head": False,
            "physical_tail_predecessor_predecessor_is_queue_head": True,
            "physical_tail_successor_is_queue_tail": True,
            "sole_free_destination": True,
            "queue_or_history_walk": False,
        },
    }
