from __future__ import annotations

import json
from pathlib import Path

from simulator.normalized_membership import run_v016_normalized_case
from simulator.recyclable_retirement_crash_matrices import (
    DEQUEUE_RECYCLE_FAILPOINTS,
    FRESH_EMPTY_FAILPOINTS,
    FRESH_NONEMPTY_FAILPOINTS,
    PARTIAL_RECLAIM_FAILPOINTS,
    REUSE_EMPTY_FAILPOINTS,
    REUSE_NONEMPTY_FAILPOINTS,
    run_recycling_crash_matrices,
)
from simulator.recyclable_retirement_descriptors import (
    run_descriptor_storage_controls,
    run_real_recycling_cycles,
)

ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "recyclable_retirement_descriptor_results.json"
DIAGNOSTIC_PATH = ROOT / "recyclable_retirement_descriptor_diagnostic.json"


def _write_failure_diagnostic(phase: str, exc: Exception) -> None:
    payload = {
        "experiment": "v0.35_recyclable_retirement_descriptors",
        "phase": phase,
        "exception_type": type(exc).__name__,
        "exception_message": str(exc),
    }
    DIAGNOSTIC_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _run_phase(phase: str, fn):
    try:
        return fn()
    except Exception as exc:
        _write_failure_diagnostic(phase, exc)
        raise


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
        raise AssertionError("v0.16 semantic guard failed before v0.35 experiment")


def _require_crash_matrix(matrix: dict, expected_failpoints: tuple[str, ...]) -> None:
    if matrix["failpoints"] != list(expected_failpoints):
        raise AssertionError("v0.35 crash failpoint set drifted")
    if not matrix["all_exact_committed_state_match"]:
        raise AssertionError("v0.35 crash matrix exposed mixed committed state")
    if not matrix["all_recovery_scan_free"]:
        raise AssertionError("v0.35 crash recovery introduced scan-based repair")
    if not matrix["all_second_recovery_idempotent"]:
        raise AssertionError("v0.35 second recovery lost idempotence")


def run() -> dict:
    semantic_guard = _run_phase(
        "semantic_guard",
        lambda: run_v016_normalized_case(
            entity_count=128,
            predicate_count=16,
            history_depth=8,
            changed_count=1,
        ),
    )
    _run_phase("semantic_guard_validation", lambda: _require_semantic_guard(semantic_guard))
    controls = _run_phase("storage_controls", run_descriptor_storage_controls)
    cycles = _run_phase("real_recycling_cycles", run_real_recycling_cycles)
    crashes = _run_phase("crash_matrices", run_recycling_crash_matrices)

    if cycles["initial_descriptor_pages_appended"] != 6:
        raise AssertionError("three-descriptor initial pool did not append six pages")
    if cycles["descriptor_pages_appended_after_pool_established"] != 0:
        raise AssertionError("descriptor pool continued to grow after recyclable capacity existed")
    if cycles["descriptor_pool_count_after_five_generations"] != 3:
        raise AssertionError("descriptor pool grew with completed-generation history")
    if not cycles["stale_reference"]["stale_reference_rejected"]:
        raise AssertionError("tagged reference failed to reject ABA-style stale identity")
    if not cycles["all_258_keys_visible"]:
        raise AssertionError("real v0.35 cycle lost live primary keys")

    _require_crash_matrix(crashes["fresh_empty_queue"], FRESH_EMPTY_FAILPOINTS)
    _require_crash_matrix(crashes["fresh_nonempty_queue"], FRESH_NONEMPTY_FAILPOINTS)
    _require_crash_matrix(crashes["empty_queue_reuse"], REUSE_EMPTY_FAILPOINTS)
    _require_crash_matrix(crashes["nonempty_queue_reuse"], REUSE_NONEMPTY_FAILPOINTS)
    _require_crash_matrix(crashes["partial_head"], PARTIAL_RECLAIM_FAILPOINTS)
    _require_crash_matrix(crashes["dequeue_to_free"], DEQUEUE_RECYCLE_FAILPOINTS)
    if int(crashes["case_count"]) != 29:
        raise AssertionError("v0.35 crash matrix count drifted")
    for name in ("partial_head", "dequeue_to_free"):
        if not crashes[name]["all_live_keys_visible"]:
            raise AssertionError("v0.35 reclaim crash hid live primary keys")

    out = {
        "experiment": "v0.35_recyclable_retirement_descriptors",
        "semantic_guard": {
            "membership_equal": semantic_guard["membership_equal"],
            "materialization_equal": semantic_guard["materialization_equal"],
            "head_index_equal": semantic_guard["head_index_equal"],
            "all_derived_fresh": semantic_guard["all_derived_fresh"],
            "full_assembly_equal": semantic_guard["full_assembly_equal"],
            "partial_assembly_equal": semantic_guard["partial_assembly_equal"],
        },
        "storage_controls": controls,
        "real_recycling_cycles": cycles,
        "crash_matrices": crashes,
        "observe": (
            "v0.34 bounds queue publication and reclaim work but leaves each dequeued dual-copy "
            "retirement descriptor permanently allocated, so descriptor storage grows with completed "
            "generation history even after the queue drains."
        ),
        "first_principle": (
            "recycling a physical descriptor address is safe only when logical descriptor identity "
            "survives reuse. Foreground enqueue/reclaim must remain bounded by current queue/free heads, "
            "and restart must not reconstruct descriptor identity by scanning history."
        ),
        "hypothesis_under_test": (
            "tag every queue/free descriptor reference with a uint64 incarnation. Dequeue publishes the "
            "page pair as FREE under its current incarnation; reuse writes the next incarnation and "
            "publishes only that tagged reference. Readers select the newest committed physical copy "
            "before checking the expected tag, so an obsolete copy cannot satisfy a stale reference."
        ),
        "prediction": (
            "an append-only control grows two pages per completed generation, while a serial recyclable "
            "pool stays at one dual-copy descriptor. In the real primary, the initial three-descriptor "
            "pool should serve later generation retirements with zero new descriptor-page append, a "
            "non-empty enqueue should remain bounded by one free-head read plus one queue-tail read, the "
            "old `(page, incarnation)` identity should be rejected after the same page is reused, and "
            "SIGKILL around fresh tagged publication, tagged partial update, free publication, reuse, "
            "and tail linking must expose exact pre/post committed state."
        ),
        "result": (
            "survives only if descriptor storage stops following completed-generation history after a "
            "sufficient reusable pool exists, stale tagged references are rejected, foreground reuse is "
            "head-local, and all fixed process-crash cases recover without descriptor-history scans."
        ),
    }
    RESULTS_PATH.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    return out


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        if not DIAGNOSTIC_PATH.exists():
            _write_failure_diagnostic("post_phase_validation", exc)
        raise
