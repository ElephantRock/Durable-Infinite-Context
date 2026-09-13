from __future__ import annotations

import json
from pathlib import Path

from simulator.normalized_membership import run_v016_normalized_case
from simulator.partial_tail_shrink import run_partial_tail_shrink_experiment

ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "partial_tail_shrink_results.json"
DIAGNOSTIC_PATH = ROOT / "partial_tail_shrink_diagnostic.json"


def _write_failure_diagnostic(phase: str, exc: Exception) -> None:
    payload = {
        "experiment": "v0.38_partial_retirement_descriptor_tail_shrink",
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
        raise AssertionError("v0.16 semantic guard failed before v0.38 experiment")


def _validate_candidate(row: dict) -> None:
    if not bool(row["survived"]):
        raise AssertionError("v0.38 partial tail-shrink candidate did not survive")
    if int(row["candidate_descriptor_history_walks"]) != 0:
        raise AssertionError("v0.38 candidate introduced descriptor-history work")
    if int(row["candidate_relocations"]) != 0:
        raise AssertionError("v0.38 candidate introduced descriptor relocation")

    control = row["v037_control"]
    if int(control["retained_arena_pages"]) != 6:
        raise AssertionError("v0.38 control no longer retains the six-page aligned arena")

    nonaligned = row["nonaligned_noop"]
    if bool(nonaligned["trace"]["released"]):
        raise AssertionError("v0.38 non-aligned candidate searched for a release target")
    if int(nonaligned["trace"]["retirement_descriptor_preads"]) != 0:
        raise AssertionError("v0.38 non-aligned no-op read descriptor history")

    release = row["live_backlog_release"]
    if not bool(release["live_backlog_preserved"]):
        raise AssertionError("v0.38 partial shrink did not preserve live backlog")
    if int(release["released_arena_pages"]) != 2:
        raise AssertionError("v0.38 did not release exactly one descriptor pair")
    if int(release["released_arena_bytes"]) != 8192:
        raise AssertionError("v0.38 released byte count drifted")
    if int(release["queue_before"]["descriptor_arena_pages"]) != 6:
        raise AssertionError("v0.38 release fixture did not start at six arena pages")
    if int(release["queue_after"]["descriptor_arena_pages"]) != 4:
        raise AssertionError("v0.38 release fixture did not finish at four arena pages")
    if int(release["trace"]["retirement_descriptor_preads"]) != 2:
        raise AssertionError("v0.38 candidate did not perform exactly one dual-copy descriptor read")
    if int(release["trace"]["retirement_descriptors_scanned"]) != 0:
        raise AssertionError("v0.38 candidate scanned descriptor history")

    identity = row["identity_after_partial_shrink"]
    if not bool(identity["stale_rejected_after_shrink"]):
        raise AssertionError("v0.38 committed frontier exposed truncated stale descriptor")
    if not bool(identity["incarnation_advanced"]):
        raise AssertionError("v0.38 partial-tail address reuse did not advance incarnation")
    if not bool(identity["stale_rejected_after_reuse"]):
        raise AssertionError("v0.38 stale pre-shrink identity aliased reused tail address")
    if int(identity["arena_pages_after_reuse"]) != 6:
        raise AssertionError("v0.38 identity fixture did not re-extend the arena")

    crashes = row["crash_matrix"]
    if int(crashes["case_count"]) != 5:
        raise AssertionError("v0.38 shrink crash-matrix case count drifted")
    if not bool(crashes["all_exact_committed_state_match"]):
        raise AssertionError("v0.38 shrink crash state drifted")
    if not bool(crashes["all_recovery_scan_free"]):
        raise AssertionError("v0.38 shrink recovery scanned history")
    if not bool(crashes["all_second_recovery_idempotent"]):
        raise AssertionError("v0.38 second recovery was not idempotent")


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
    candidate = _run_phase("partial_tail_shrink", run_partial_tail_shrink_experiment)
    _run_phase("candidate_validation", lambda: _validate_candidate(candidate))

    out = {
        "experiment": "v0.38_partial_retirement_descriptor_tail_shrink",
        "semantic_guard": {
            "membership_equal": semantic_guard["membership_equal"],
            "materialization_equal": semantic_guard["materialization_equal"],
            "head_index_equal": semantic_guard["head_index_equal"],
            "all_derived_fresh": semantic_guard["all_derived_fresh"],
            "full_assembly_equal": semantic_guard["full_assembly_equal"],
            "partial_assembly_equal": semantic_guard["partial_assembly_equal"],
        },
        "partial_tail_shrink": candidate,
        "observe": (
            "v0.37 can return the segregated descriptor arena to zero only when the retirement queue fully drains; "
            "a non-empty queue can still retain an otherwise releasable physical tail pair."
        ),
        "first_principle": (
            "partial physical shrink can remain bounded only when the exact removable tail object and the metadata "
            "needed to detach it are directly reachable from authoritative current-state roots, without searching "
            "descriptor history or relocating live records."
        ),
        "hypothesis_under_test": (
            "if the descriptor free-list head is exactly the physical arena tail, one maintenance transaction can "
            "detach that pair in O(1) metadata work, publish a shorter committed arena frontier, and truncate the "
            "sidecar while live retirement descriptors remain queued."
        ),
        "prediction": (
            "the v0.37 control will retain a six-page arena in the aligned live-backlog fixture, while v0.38 will "
            "return exactly two pages / 8,192 bytes, preserve live queue state, reject the stale truncated identity, "
            "reuse the same physical address only under a newer incarnation, and converge scan-free across SIGKILL "
            "before and after authoritative shrink publication."
        ),
        "result": (
            "candidate result is determined by the executable v0.38 simulator and remains bounded to the tested "
            "free-list-head/arena-tail alignment case; arbitrary free-tail discovery inside the singly linked free "
            "list is explicitly outside this version's claim."
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
