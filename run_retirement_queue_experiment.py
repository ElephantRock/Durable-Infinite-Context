from __future__ import annotations

import json
from pathlib import Path

from simulator.normalized_membership import run_v016_normalized_case
from simulator.retirement_queue import (
    DEQUEUE_RECLAIM_FAILPOINTS,
    EMPTY_ENQUEUE_FAILPOINTS,
    NONEMPTY_ENQUEUE_FAILPOINTS,
    PARTIAL_RECLAIM_FAILPOINTS,
    SEGMENT_BUDGET,
    run_enqueue_crash_matrices,
    run_enqueue_scaling_controls,
    run_real_queue_cycles,
    run_reclaim_crash_matrices,
)

ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "retirement_queue_results.json"


def _require_semantic_guard(row: dict) -> None:
    required = (
        "membership_equal",
        "materialization_equal",
        "head_index_equal",
        "all_derived_fresh",
        "full_assembly_equal",
        "partial_assembly_equal",
    )
    if not all(bool(row[name]) for name in required):
        raise AssertionError("v0.16 semantic guard failed before v0.34 experiment")


def run() -> dict:
    semantic_guard = run_v016_normalized_case(
        entity_count=128,
        predicate_count=16,
        history_depth=8,
        changed_count=1,
    )
    _require_semantic_guard(semantic_guard)

    controls = run_enqueue_scaling_controls()
    cycles = run_real_queue_cycles()
    enqueue_crashes = run_enqueue_crash_matrices()
    reclaim_crashes = run_reclaim_crash_matrices()

    if controls["candidate_enqueue_descriptor_pages"] != 2:
        raise AssertionError("retirement descriptor page bound drifted")
    if controls["candidate_enqueue_max_descriptor_preads"] != 2:
        raise AssertionError("enqueue descriptor-read bound drifted")
    if controls["candidate_enqueue_max_descriptor_pwrites"] != 2:
        raise AssertionError("enqueue descriptor-write bound drifted")
    if controls["candidate_enqueue_history_walks"] != 0:
        raise AssertionError("candidate enqueue introduced history traversal")

    if cycles["queue_before_cleanup"]["queue_count"] != 3:
        raise AssertionError("real primary failed to accumulate three retirement backlogs")
    if cycles["max_enqueue_descriptor_preads"] > 2:
        raise AssertionError("real enqueue descriptor reads escaped constant bound")
    if cycles["max_enqueue_descriptor_pwrites"] > 2:
        raise AssertionError("real enqueue descriptor writes escaped constant bound")
    if cycles["max_enqueue_descriptor_pages"] != 2:
        raise AssertionError("real enqueue descriptor allocation escaped two-page bound")
    if cycles["max_reclaim_segments_per_step"] > SEGMENT_BUDGET:
        raise AssertionError("real queue reclaim exceeded segment budget")
    if cycles["max_reclaim_descriptor_preads"] > 2:
        raise AssertionError("real queue reclaim traversed more than the head descriptor")
    if cycles["max_reclaim_descriptor_pwrites"] > 1:
        raise AssertionError("real queue reclaim rewrote more than one descriptor")
    if cycles["max_reclaim_pages_appended"] != 0:
        raise AssertionError("real queue reclaim appended storage")
    if not cycles["all_129_keys_visible"]:
        raise AssertionError("real retirement queue cycle lost live keys")

    empty = enqueue_crashes["empty_queue"]
    nonempty = enqueue_crashes["nonempty_queue"]
    if empty["failpoints"] != list(EMPTY_ENQUEUE_FAILPOINTS):
        raise AssertionError("empty-queue enqueue failpoint set drifted")
    if nonempty["failpoints"] != list(NONEMPTY_ENQUEUE_FAILPOINTS):
        raise AssertionError("nonempty-queue enqueue failpoint set drifted")
    for matrix in (empty, nonempty):
        if not matrix["all_exact_committed_state_match"]:
            raise AssertionError("enqueue crash exposed mixed committed state")
        if not matrix["all_recovery_scan_free"]:
            raise AssertionError("enqueue recovery introduced scan-based repair")
        if not matrix["all_second_recovery_idempotent"]:
            raise AssertionError("enqueue second recovery lost idempotence")

    partial = reclaim_crashes["partial_head"]
    dequeue = reclaim_crashes["dequeue_head"]
    if partial["failpoints"] != list(PARTIAL_RECLAIM_FAILPOINTS):
        raise AssertionError("partial-reclaim failpoint set drifted")
    if dequeue["failpoints"] != list(DEQUEUE_RECLAIM_FAILPOINTS):
        raise AssertionError("dequeue-reclaim failpoint set drifted")
    for matrix in (partial, dequeue):
        if not matrix["all_exact_committed_state_match"]:
            raise AssertionError("queued reclaim crash exposed mixed committed state")
        if not matrix["all_live_keys_visible"]:
            raise AssertionError("queued reclaim crash hid live data")
        if not matrix["all_recovery_scan_free"]:
            raise AssertionError("queued reclaim recovery introduced scan-based repair")
        if not matrix["all_second_recovery_idempotent"]:
            raise AssertionError("queued reclaim second recovery lost idempotence")

    out = {
        "experiment": "v0.34_retirement_queue",
        "semantic_guard": {
            "membership_equal": semantic_guard["membership_equal"],
            "materialization_equal": semantic_guard["materialization_equal"],
            "head_index_equal": semantic_guard["head_index_equal"],
            "all_derived_fresh": semantic_guard["all_derived_fresh"],
            "full_assembly_equal": semantic_guard["full_assembly_equal"],
            "partial_assembly_equal": semantic_guard["partial_assembly_equal"],
        },
        "enqueue_scaling_controls": controls,
        "real_queue_cycles": cycles,
        "enqueue_crash_matrices": enqueue_crashes,
        "reclaim_crash_matrices": reclaim_crashes,
        "observe": (
            "v0.33 makes retired primary generations segment-exclusive but blocks future growth until "
            "the single scalar retirement cursor drains. Cleanup progress therefore becomes an admission "
            "dependency even though each generation already has an independent ownership chain."
        ),
        "first_principle": (
            "generation completion should publish one bounded retirement obligation without traversing "
            "older obligations. Cleanup should consume only the committed FIFO head plus an explicit "
            "segment budget, and restart should recover queue roots without history discovery."
        ),
        "hypothesis_under_test": (
            "a FIFO of fixed dual-copy retirement descriptors, rooted by scalar head/tail/count fields in "
            "the superblock, can decouple future migration from cleanup backlog. Enqueue appends one "
            "two-page descriptor and updates at most one tail copy; reclaim reads only the head descriptor."
        ),
        "prediction": (
            "flat serialized manifests and head-to-tail enqueue controls grow with queued generations, "
            "while the candidate keeps enqueue at two appended descriptor pages, at most two descriptor "
            "preads and two descriptor pwrites. Real primary growth must accumulate multiple retirement "
            "generations without cleanup, FIFO reclaim must stay within the segment budget with zero "
            "append, and process death around descriptor publication/update/dequeue must expose exact "
            "pre/post committed states with queue-scan-free recovery."
        ),
        "result": (
            "survives only if the fixed experiment preserves bounded enqueue/reclaim work and exact crash "
            "visibility. Descriptor pages remain append-only, so total retirement metadata still grows "
            "with completed-generation history; this experiment does not claim constant total storage."
        ),
    }
    RESULTS_PATH.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    return out


if __name__ == "__main__":
    run()
