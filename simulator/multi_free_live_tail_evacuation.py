from __future__ import annotations

import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from simulator.retired_generation_reclamation import _sparse_copy
from storage.bounded_live_tail_evacuation_retirement_descriptor_primary import (
    LiveTailEvacuationRetirementDescriptorPrimaryStore as V040BoundedStore,
)
from storage.fixed_page_primary import PAGE_SIZE
from storage.multi_free_live_tail_evacuation_retirement_descriptor_primary import (
    LiveTailEvacuationRetirementDescriptorPrimaryStore,
)
from storage.retirement_descriptor_pool import RETIREMENT_STATUS_FREE, RETIREMENT_STATUS_QUEUED

ROOT = Path(__file__).resolve().parent.parent
WORKER = ROOT / "multi_free_live_tail_evacuation_worker.py"
SEGMENT_BUDGET = 2
FREE_CHAIN_TARGET_COUNTS = (4, 5, 6, 7)
QUEUE_DEPTH_TARGET_COUNTS = (4, 5, 6, 7)
EVACUATION_FAILPOINTS = (
    "retirement_live_tail_destination_staged",
    "retirement_live_tail_predecessor_staged",
    "retirement_live_tail_free_successor_staged",
    "retirement_live_tail_dependencies_synced",
    "committed",
    "retirement_live_tail_relocation_committed",
    "retirement_arena_truncated",
    "retirement_live_tail_relocation_synced",
)
PRECOMMIT_FAILPOINTS = set(EVACUATION_FAILPOINTS[:4])
POSTCOMMIT_PRETRUNCATE_FAILPOINTS = {
    "committed",
    "retirement_live_tail_relocation_committed",
}


def _next_key(index: int) -> str:
    return f"k-{index:04d}"


def _insert_until_queue_count(store, *, index: int, target: int, cap: int = 65536) -> int:
    while int(store.retirement_queue_snapshot()["queue_count"]) < target:
        if index >= cap:
            raise AssertionError(f"v0.41 fixture did not reach queue count {target}")
        store.insert(_next_key(index))
        index += 1
    if int(store.retirement_queue_snapshot()["queue_count"]) != target:
        raise AssertionError("v0.41 fixture overshot requested queue count")
    return index


def _reclaim_to_queue_depth(store, *, target_depth: int) -> None:
    while int(store.retirement_queue_snapshot()["queue_count"]) > target_depth:
        trace = store.reclaim_step(budget=SEGMENT_BUDGET)
        if int(trace.retirement_descriptors_scanned) != 0:
            raise AssertionError("v0.41 setup reclaim scanned descriptor history")
    if int(store.retirement_queue_snapshot()["queue_count"]) != target_depth:
        raise AssertionError("v0.41 setup reclaim overshot requested live queue depth")


def _build_fixture(
    path: Path,
    store_cls,
    *,
    target_descriptor_count: int,
    live_queue_depth: int,
) -> dict[str, Any]:
    if target_descriptor_count < 3:
        raise ValueError("v0.41 fixture requires at least three descriptors")
    if live_queue_depth < 2 or live_queue_depth >= target_descriptor_count:
        raise ValueError("v0.41 fixture requires at least two live descriptors and one FREE descriptor")

    store = store_cls(str(path))
    store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=4096)
    index = _insert_until_queue_count(store, index=0, target=target_descriptor_count)
    initial = store.retirement_queue_snapshot()
    expected_initial_pages = list(range(0, 2 * target_descriptor_count, 2))
    observed_initial_pages = [int(row["descriptor_page"]) for row in initial["descriptors"]]
    if observed_initial_pages != expected_initial_pages:
        raise AssertionError(f"v0.41 initial descriptor layout drifted: {observed_initial_pages}")

    _reclaim_to_queue_depth(store, target_depth=live_queue_depth)
    ready = store.retirement_queue_snapshot()
    free_count = target_descriptor_count - live_queue_depth
    expected_free_pages = list(range(0, 2 * free_count, 2))[::-1]
    expected_live_pages = list(range(2 * free_count, 2 * target_descriptor_count, 2))
    free_pages = [int(row["descriptor_page"]) for row in ready["free_descriptors"]]
    live_pages = [int(row["descriptor_page"]) for row in ready["descriptors"]]
    if free_pages != expected_free_pages:
        raise AssertionError(f"v0.41 FREE-chain layout drifted: {free_pages}")
    if live_pages != expected_live_pages:
        raise AssertionError(f"v0.41 live-queue layout drifted: {live_pages}")
    if int(ready["descriptor_free_count"]) != free_count:
        raise AssertionError("v0.41 FREE-count drifted")
    if int(ready["descriptor_arena_pages"]) != 2 * target_descriptor_count:
        raise AssertionError("v0.41 descriptor arena frontier drifted")
    if int(ready["tail_page"]) != expected_live_pages[-1]:
        raise AssertionError("v0.41 physical tail is not the logical queue tail")
    if int(ready["tail_predecessor_page"]) != expected_live_pages[-2]:
        raise AssertionError("v0.41 queue-tail predecessor authority drifted")
    if int(ready["descriptor_free_head_page"]) != expected_free_pages[0]:
        raise AssertionError("v0.41 committed FREE head drifted")

    if free_count >= 2:
        first = ready["free_descriptors"][0]
        second = ready["free_descriptors"][1]
        if int(first["next_descriptor_page"]) != int(second["descriptor_page"]):
            raise AssertionError("v0.41 free head does not directly name its successor")
        if int(first["next_descriptor_incarnation"]) != int(second["descriptor_incarnation"]):
            raise AssertionError("v0.41 free-head successor incarnation drifted")
        if int(second.get("prev_descriptor_page", -1)) != int(first["descriptor_page"]):
            raise AssertionError("v0.41 free successor does not name current head as predecessor")
        if int(second.get("prev_descriptor_incarnation", -1)) != int(first["descriptor_incarnation"]):
            raise AssertionError("v0.41 free successor predecessor incarnation drifted")

    return {
        "next_key_index": index,
        "keys": tuple(_next_key(i) for i in range(index)),
        "initial": initial,
        "ready": ready,
        "target_descriptor_count": target_descriptor_count,
        "live_queue_depth": live_queue_depth,
        "free_chain_length": free_count,
        "tail_page": int(ready["tail_page"]),
        "tail_incarnation": int(ready["tail_incarnation"]),
        "predecessor_page": int(ready["tail_predecessor_page"]),
        "predecessor_incarnation": int(ready["tail_predecessor_incarnation"]),
        "destination_page": int(ready["descriptor_free_head_page"]),
        "destination_incarnation": int(ready["descriptor_free_head_incarnation"]),
        "successor_page": (
            int(ready["free_descriptors"][1]["descriptor_page"])
            if free_count >= 2
            else None
        ),
        "successor_incarnation": (
            int(ready["free_descriptors"][1]["descriptor_incarnation"])
            if free_count >= 2
            else None
        ),
    }


def _v040_multiple_free_control_case() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v041-v040-control-") as tmp:
        path = Path(tmp) / "primary.pages"
        fixture = _build_fixture(
            path,
            V040BoundedStore,
            target_descriptor_count=4,
            live_queue_depth=2,
        )
        store = V040BoundedStore(str(path))
        before = store.retirement_queue_snapshot()
        arena_before = store.descriptor_arena_diagnostic()
        trace = store.evacuate_live_retirement_arena_tail_step()
        after = store.retirement_queue_snapshot()
        arena_after = store.descriptor_arena_diagnostic()
        if trace.released:
            raise AssertionError("v0.40 control unexpectedly relocated a multiple-FREE live tail")
        if int(trace.retirement_descriptor_preads) != 0 or int(trace.retirement_descriptor_pwrites) != 0:
            raise AssertionError("v0.40 multiple-FREE control performed descriptor I/O")
        if int(trace.retirement_descriptors_scanned) != 0 or int(trace.live_descriptor_relocations) != 0:
            raise AssertionError("v0.40 multiple-FREE control performed non-local work")
        if before != after or arena_before != arena_after:
            raise AssertionError("v0.40 multiple-FREE control changed state")
        return {
            "keys_inserted": int(fixture["next_key_index"]),
            "queue_before": before,
            "arena_before": arena_before,
            "trace": trace.to_dict(),
            "queue_after": after,
            "arena_after": arena_after,
            "exact_state_unchanged": True,
        }


def _candidate_multiple_free_case() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v041-release-") as tmp:
        path = Path(tmp) / "primary.pages"
        fixture = _build_fixture(
            path,
            LiveTailEvacuationRetirementDescriptorPrimaryStore,
            target_descriptor_count=4,
            live_queue_depth=2,
        )
        store = LiveTailEvacuationRetirementDescriptorPrimaryStore(str(path))
        before = store.retirement_queue_snapshot()
        arena_before = store.descriptor_arena_diagnostic()

        stale_tail_page = int(fixture["tail_page"])
        stale_tail_incarnation = int(fixture["tail_incarnation"])
        stale_destination_page = int(fixture["destination_page"])
        stale_destination_incarnation = int(fixture["destination_incarnation"])
        successor_page = int(fixture["successor_page"])
        successor_incarnation = int(fixture["successor_incarnation"])

        trace = store.evacuate_live_retirement_arena_tail_step()
        after = store.retirement_queue_snapshot()
        arena_after = store.descriptor_arena_diagnostic()

        if not trace.released or not trace.tail_was_queued or not trace.tail_was_queue_tail:
            raise AssertionError("v0.41 did not evacuate the multiple-FREE live queue tail")
        if int(trace.predecessor_page or -1) != 4 or int(trace.destination_page or -1) != 2:
            raise AssertionError("v0.41 relocation did not use the named predecessor/destination")
        if int(trace.retirement_descriptor_preads) != 8:
            raise AssertionError("v0.41 multiple-FREE relocation should perform four dual-copy reads")
        if int(trace.retirement_descriptor_pwrites) != 3:
            raise AssertionError("v0.41 multiple-FREE relocation should perform three descriptor rewrites")
        if int(trace.retirement_descriptors_scanned) != 0 or int(trace.live_descriptor_relocations) != 1:
            raise AssertionError("v0.41 relocation scanned history or reported wrong relocation count")
        if int(before["descriptor_arena_pages"]) != 8 or int(after["descriptor_arena_pages"]) != 6:
            raise AssertionError("v0.41 committed arena frontier did not shrink 8 -> 6")
        if int(arena_before["arena_file_bytes"]) != 8 * PAGE_SIZE:
            raise AssertionError("v0.41 pre-relocation arena length drifted")
        if int(arena_after["arena_file_bytes"]) != 6 * PAGE_SIZE:
            raise AssertionError("v0.41 post-relocation arena length drifted")
        if [int(row["descriptor_page"]) for row in after["descriptors"]] != [4, 2]:
            raise AssertionError("v0.41 relocated queue is not [4,2]")
        if [int(row["descriptor_page"]) for row in after["free_descriptors"]] != [0]:
            raise AssertionError("v0.41 surviving FREE chain is not [0]")
        if int(after["descriptor_free_count"]) != 1 or int(after["descriptor_free_head_page"]) != 0:
            raise AssertionError("v0.41 did not publish page 0 as the sole surviving FREE head")
        if int(after["tail_page"]) != 2 or int(after["tail_predecessor_page"]) != 4:
            raise AssertionError("v0.41 relocated queue-tail authority drifted")
        free_head = after["free_descriptors"][0]
        if "prev_descriptor_page" in free_head or "prev_descriptor_incarnation" in free_head:
            raise AssertionError("v0.41 new free head retained the consumed destination as predecessor")
        if int(free_head["descriptor_incarnation"]) != successor_incarnation:
            raise AssertionError("v0.41 surviving free successor identity changed")

        stale_tail_rejected = False
        try:
            store.retirement_descriptor_reference(
                stale_tail_page,
                stale_tail_incarnation,
                expected_status=RETIREMENT_STATUS_QUEUED,
            )
        except RuntimeError:
            stale_tail_rejected = True
        if not stale_tail_rejected:
            raise AssertionError("v0.41 truncated live-tail identity remained visible")

        stale_destination_rejected = False
        try:
            store.retirement_descriptor_reference(
                stale_destination_page,
                stale_destination_incarnation,
                expected_status=RETIREMENT_STATUS_QUEUED,
            )
        except RuntimeError:
            stale_destination_rejected = True
        if not stale_destination_rejected:
            raise AssertionError("v0.41 old FREE destination identity aliased relocated live state")

        surviving_successor = store.retirement_descriptor_reference(
            successor_page,
            successor_incarnation,
            expected_status=RETIREMENT_STATUS_FREE,
        )
        successor_prev_page, successor_prev_incarnation = store._free_prev(surviving_successor)
        if successor_prev_page is not None or successor_prev_incarnation is not None:
            raise AssertionError("v0.41 surviving free successor retained stale predecessor authority")

        current_tail = store.retirement_descriptor_reference(
            int(after["tail_page"]),
            int(after["tail_incarnation"]),
            expected_status=RETIREMENT_STATUS_QUEUED,
        )
        return {
            "keys_inserted": int(fixture["next_key_index"]),
            "queue_before": before,
            "arena_before": arena_before,
            "trace": trace.to_dict(),
            "queue_after": after,
            "arena_after": arena_after,
            "released_arena_pages": 2,
            "released_arena_bytes": 2 * PAGE_SIZE,
            "stale_tail_rejected": stale_tail_rejected,
            "stale_destination_rejected": stale_destination_rejected,
            "successor_identity_preserved": int(surviving_successor["incarnation"]) == successor_incarnation,
            "successor_predecessor_cleared": True,
            "current_destination_generation": int(current_tail["generation"]),
        }


def _sole_free_compatibility_case() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v041-sole-free-") as tmp:
        path = Path(tmp) / "primary.pages"
        fixture = _build_fixture(
            path,
            LiveTailEvacuationRetirementDescriptorPrimaryStore,
            target_descriptor_count=3,
            live_queue_depth=2,
        )
        store = LiveTailEvacuationRetirementDescriptorPrimaryStore(str(path))
        before = store.retirement_queue_snapshot()
        trace = store.evacuate_live_retirement_arena_tail_step()
        after = store.retirement_queue_snapshot()
        if not trace.released:
            raise AssertionError("v0.41 generalized candidate regressed the sole-FREE v0.40 geometry")
        if int(trace.retirement_descriptor_preads) != 6 or int(trace.retirement_descriptor_pwrites) != 2:
            raise AssertionError("v0.41 sole-FREE compatibility work drifted")
        if int(trace.retirement_descriptors_scanned) != 0 or int(trace.live_descriptor_relocations) != 1:
            raise AssertionError("v0.41 sole-FREE compatibility introduced non-local work")
        if int(before["descriptor_free_count"]) != 1 or int(after["descriptor_free_count"]) != 0:
            raise AssertionError("v0.41 sole-FREE compatibility free count drifted")
        return {
            "keys_inserted": int(fixture["next_key_index"]),
            "queue_before": before,
            "trace": trace.to_dict(),
            "queue_after": after,
        }


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


def _require_scan_free(recovery: dict[str, Any]) -> None:
    if int(recovery["logical_work"]) != 0:
        raise AssertionError("v0.41 recovery required logical redo")
    if int(recovery["generation_pages_scanned"]) != 0:
        raise AssertionError("v0.41 recovery scanned generation pages")
    if int(recovery["mapping_nodes_scanned"]) != 0:
        raise AssertionError("v0.41 recovery scanned radix nodes")
    if int(recovery.get("retirement_descriptors_scanned", 0)) != 0:
        raise AssertionError("v0.41 recovery scanned retirement descriptors")


def _recover_twice(
    store: LiveTailEvacuationRetirementDescriptorPrimaryStore,
) -> tuple[dict[str, Any], dict[str, Any]]:
    one = store.recover()
    two = store.recover()
    _require_scan_free(one)
    _require_scan_free(two)
    if int(two["physical_truncated_bytes"]) != 0:
        raise AssertionError("v0.41 second recovery changed primary physical length")
    if int(two["retirement_descriptor_arena_truncated_bytes"]) != 0:
        raise AssertionError("v0.41 second recovery changed descriptor arena length")
    return one, two


def _worker(path: Path, *, failpoint: str) -> subprocess.CompletedProcess[str]:
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


def _require_sigkill(proc: subprocess.CompletedProcess[str], label: str) -> None:
    if proc.returncode != -signal.SIGKILL:
        raise AssertionError(f"{label} did not SIGKILL: {proc.returncode} {proc.stderr}")


def _crash_matrix() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v041-crash-") as tmp:
        directory = Path(tmp)
        base_path = directory / "base.pages"
        fixture = _build_fixture(
            base_path,
            LiveTailEvacuationRetirementDescriptorPrimaryStore,
            target_descriptor_count=4,
            live_queue_depth=2,
        )
        keys = tuple(fixture["keys"])
        base = LiveTailEvacuationRetirementDescriptorPrimaryStore(str(base_path))
        pre = _semantic_state(base, keys)

        clean_path = directory / "clean.pages"
        _copy_candidate_store(base_path, clean_path)
        clean = LiveTailEvacuationRetirementDescriptorPrimaryStore(str(clean_path))
        clean_trace = clean.evacuate_live_retirement_arena_tail_step()
        if not clean_trace.released:
            raise AssertionError("v0.41 clean crash oracle did not relocate multiple-FREE live tail")
        post = _semantic_state(clean, keys)

        cases: list[dict[str, Any]] = []
        for failpoint in EVACUATION_FAILPOINTS:
            crash_path = directory / f"crash-{failpoint}.pages"
            _copy_candidate_store(base_path, crash_path)
            proc = _worker(crash_path, failpoint=failpoint)
            _require_sigkill(proc, failpoint)

            crashed = LiveTailEvacuationRetirementDescriptorPrimaryStore(str(crash_path))
            before_recovery = crashed.descriptor_arena_diagnostic()
            one, two = _recover_twice(crashed)
            recovered = _semantic_state(crashed, keys)
            expected = pre if failpoint in PRECOMMIT_FAILPOINTS else post
            if recovered != expected:
                raise AssertionError(f"v0.41 {failpoint} recovered wrong committed state")

            expected_truncated = (
                2 * PAGE_SIZE if failpoint in POSTCOMMIT_PRETRUNCATE_FAILPOINTS else 0
            )
            observed_truncated = int(one["retirement_descriptor_arena_truncated_bytes"])
            if observed_truncated != expected_truncated:
                raise AssertionError(
                    f"v0.41 {failpoint} recovery truncated {observed_truncated}, "
                    f"expected {expected_truncated}"
                )
            cases.append(
                {
                    "failpoint": failpoint,
                    "expected_state": "pre" if failpoint in PRECOMMIT_FAILPOINTS else "post",
                    "arena_before_recovery": before_recovery,
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
            "all_exact_committed_state_match": all(
                bool(row["exact_committed_state_match"]) for row in cases
            ),
            "all_recovery_scan_free": all(bool(row["recovery_scan_free"]) for row in cases),
            "all_second_recovery_idempotent": all(
                bool(row["second_recovery_idempotent"]) for row in cases
            ),
        }


def _free_chain_scaling_case(target_descriptor_count: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"dic-v041-free-scale-{target_descriptor_count}-") as tmp:
        path = Path(tmp) / "primary.pages"
        fixture = _build_fixture(
            path,
            LiveTailEvacuationRetirementDescriptorPrimaryStore,
            target_descriptor_count=target_descriptor_count,
            live_queue_depth=2,
        )
        store = LiveTailEvacuationRetirementDescriptorPrimaryStore(str(path))
        before = store.retirement_queue_snapshot()
        trace = store.evacuate_live_retirement_arena_tail_step()
        after = store.retirement_queue_snapshot()
        if not trace.released:
            raise AssertionError("v0.41 free-chain scaling candidate did not release live tail")
        if int(trace.retirement_descriptor_preads) != 8 or int(trace.retirement_descriptor_pwrites) != 3:
            raise AssertionError("v0.41 relocation work depends on free-chain length")
        if int(trace.retirement_descriptors_scanned) != 0 or int(trace.live_descriptor_relocations) != 1:
            raise AssertionError("v0.41 free-chain scaling performed non-local work")
        if int(after["descriptor_free_count"]) != int(before["descriptor_free_count"]) - 1:
            raise AssertionError("v0.41 free-chain scaling free count drifted")
        if int(after["descriptor_free_head_page"]) != int(fixture["successor_page"]):
            raise AssertionError("v0.41 free-chain scaling did not publish direct successor as new head")
        if "prev_descriptor_page" in after["free_descriptors"][0]:
            raise AssertionError("v0.41 scaled new free head retained predecessor")
        return {
            "target_descriptor_count": target_descriptor_count,
            "keys_inserted": int(fixture["next_key_index"]),
            "live_queue_depth_before": 2,
            "free_chain_length_before": int(fixture["free_chain_length"]),
            "physical_tail_page": int(fixture["tail_page"]),
            "predecessor_page": int(fixture["predecessor_page"]),
            "destination_page": int(fixture["destination_page"]),
            "successor_page": int(fixture["successor_page"]),
            "arena_pages_before": int(before["descriptor_arena_pages"]),
            "arena_pages_after": int(after["descriptor_arena_pages"]),
            "retirement_descriptor_preads": int(trace.retirement_descriptor_preads),
            "retirement_descriptor_pwrites": int(trace.retirement_descriptor_pwrites),
            "retirement_descriptors_scanned": int(trace.retirement_descriptors_scanned),
            "live_descriptor_relocations": int(trace.live_descriptor_relocations),
        }


def _free_chain_scaling() -> dict[str, Any]:
    rows = [_free_chain_scaling_case(target) for target in FREE_CHAIN_TARGET_COUNTS]
    return {
        "target_descriptor_counts": list(FREE_CHAIN_TARGET_COUNTS),
        "rows": rows,
        "all_constant_relocation_work": all(
            int(row["retirement_descriptor_preads"]) == 8
            and int(row["retirement_descriptor_pwrites"]) == 3
            and int(row["retirement_descriptors_scanned"]) == 0
            and int(row["live_descriptor_relocations"]) == 1
            for row in rows
        ),
    }


def _queue_depth_scaling_case(target_descriptor_count: int) -> dict[str, Any]:
    live_queue_depth = target_descriptor_count - 2
    with tempfile.TemporaryDirectory(prefix=f"dic-v041-queue-scale-{target_descriptor_count}-") as tmp:
        path = Path(tmp) / "primary.pages"
        fixture = _build_fixture(
            path,
            LiveTailEvacuationRetirementDescriptorPrimaryStore,
            target_descriptor_count=target_descriptor_count,
            live_queue_depth=live_queue_depth,
        )
        if int(fixture["free_chain_length"]) != 2:
            raise AssertionError("v0.41 queue-depth scaling must hold FREE-chain length at two")
        store = LiveTailEvacuationRetirementDescriptorPrimaryStore(str(path))
        before = store.retirement_queue_snapshot()
        trace = store.evacuate_live_retirement_arena_tail_step()
        after = store.retirement_queue_snapshot()
        if not trace.released:
            raise AssertionError("v0.41 queue-depth scaling candidate did not release live tail")
        if int(trace.retirement_descriptor_preads) != 8 or int(trace.retirement_descriptor_pwrites) != 3:
            raise AssertionError("v0.41 relocation work depends on live queue depth")
        if int(trace.retirement_descriptors_scanned) != 0 or int(trace.live_descriptor_relocations) != 1:
            raise AssertionError("v0.41 queue-depth scaling performed non-local work")
        if int(after["descriptor_free_head_page"]) != 0:
            raise AssertionError("v0.41 queue-depth scaling new free head drifted")
        return {
            "target_descriptor_count": target_descriptor_count,
            "keys_inserted": int(fixture["next_key_index"]),
            "live_queue_depth_before": live_queue_depth,
            "free_chain_length_before": 2,
            "physical_tail_page": int(fixture["tail_page"]),
            "predecessor_page": int(fixture["predecessor_page"]),
            "destination_page": int(fixture["destination_page"]),
            "successor_page": int(fixture["successor_page"]),
            "arena_pages_before": int(before["descriptor_arena_pages"]),
            "arena_pages_after": int(after["descriptor_arena_pages"]),
            "retirement_descriptor_preads": int(trace.retirement_descriptor_preads),
            "retirement_descriptor_pwrites": int(trace.retirement_descriptor_pwrites),
            "retirement_descriptors_scanned": int(trace.retirement_descriptors_scanned),
            "live_descriptor_relocations": int(trace.live_descriptor_relocations),
        }


def _queue_depth_scaling() -> dict[str, Any]:
    rows = [_queue_depth_scaling_case(target) for target in QUEUE_DEPTH_TARGET_COUNTS]
    return {
        "target_descriptor_counts": list(QUEUE_DEPTH_TARGET_COUNTS),
        "rows": rows,
        "all_constant_relocation_work": all(
            int(row["retirement_descriptor_preads"]) == 8
            and int(row["retirement_descriptor_pwrites"]) == 3
            and int(row["retirement_descriptors_scanned"]) == 0
            and int(row["live_descriptor_relocations"]) == 1
            for row in rows
        ),
    }


def run_multi_free_live_tail_evacuation_experiment() -> dict[str, Any]:
    control = _v040_multiple_free_control_case()
    candidate = _candidate_multiple_free_case()
    sole_free = _sole_free_compatibility_case()
    crashes = _crash_matrix()
    free_scaling = _free_chain_scaling()
    queue_scaling = _queue_depth_scaling()
    return {
        "experiment": "v0.41-multiple-free-live-tail-evacuation",
        "survived": True,
        "v040_multiple_free_control": control,
        "multiple_free_live_tail_evacuation": candidate,
        "sole_free_compatibility": sole_free,
        "crash_matrix": crashes,
        "free_chain_scaling": free_scaling,
        "queue_depth_scaling": queue_scaling,
        "candidate_free_chain_walks": 0,
        "candidate_queue_walks": 0,
        "claim": (
            "When the physical descriptor tail is also the logical queue tail, consuming the "
            "current FREE head as relocation destination can remain bounded even when that head "
            "has a successor: directly rewrite the named successor's predecessor to null before "
            "publishing it as the new free head. The tested multiple-FREE path uses four direct "
            "dual-copy reads, three descriptor rewrites, zero queue/free-chain/history scans, "
            "and one live relocation independent of tested FREE-chain length and queue depth."
        ),
        "nonclaims": [
            "The physical tail must also be the logical retirement-queue tail.",
            "The relocation destination remains the current FREE-list head; arbitrary destination selection is not tested.",
            "Only the directly named new FREE head is repaired; no free-chain walk is performed.",
            "File-length truncation is not a claim about filesystem allocated-block reclamation.",
            "Process SIGKILL evidence is not a hardware power-loss or torn-write proof.",
            "Descriptor page/incarnation authority remains finite-width.",
        ],
    }
