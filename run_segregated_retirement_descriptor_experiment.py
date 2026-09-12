from __future__ import annotations

import json
from pathlib import Path

from simulator.normalized_membership import run_v016_normalized_case
from simulator.segregated_retirement_descriptors import (
    run_segregated_descriptor_arena_experiment,
)

ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "segregated_retirement_descriptor_results.json"
DIAGNOSTIC_PATH = ROOT / "segregated_retirement_descriptor_diagnostic.json"


def _write_failure_diagnostic(phase: str, exc: Exception) -> None:
    payload = {
        "experiment": "v0.37_segregated_retirement_descriptor_arena",
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
        raise AssertionError("v0.16 semantic guard failed before v0.37 experiment")


def _validate_candidate(row: dict) -> None:
    if not bool(row["survived"]):
        raise AssertionError("v0.37 segregated arena candidate did not survive")
    if int(row["candidate_descriptor_history_walks"]) != 0:
        raise AssertionError("v0.37 candidate introduced descriptor-history work")
    if int(row["candidate_relocations"]) != 0:
        raise AssertionError("v0.37 candidate introduced descriptor relocation")

    peak = row["peak_release"]
    if int(peak["peak_descriptor_pairs"]) != 3:
        raise AssertionError("v0.37 real peak descriptor count drifted")
    if int(peak["peak_arena_pages"]) != 6:
        raise AssertionError("v0.37 real peak arena page count drifted")
    if int(peak["released_arena_pages"]) != 6:
        raise AssertionError("v0.37 did not release the six-page real peak")
    if int(peak["queue_after_drain"]["descriptor_arena_pages"]) != 0:
        raise AssertionError("v0.37 retained committed arena pages after drain")
    if int(peak["arena_after_drain"]["arena_file_bytes"]) != 0:
        raise AssertionError("v0.37 retained sidecar file length after drain")

    identity = row["identity_after_reset"]
    if not bool(identity["same_arena_page_reused"]):
        raise AssertionError("v0.37 did not exercise post-reset page reuse")
    if not bool(identity["incarnation_advanced"]):
        raise AssertionError("v0.37 descriptor incarnation did not advance across reset")
    if not bool(identity["stale_identity_rejected"]):
        raise AssertionError("v0.37 stale pre-reset descriptor identity aliased new state")

    history = row["history_cycles"]
    if int(history["cycle_count"]) < 4:
        raise AssertionError("v0.37 history control did not cross four reset cycles")
    if not bool(history["all_cycles_return_to_zero_committed_arena_pages"]):
        raise AssertionError("v0.37 history retained committed arena capacity")
    if not bool(history["all_cycles_return_to_zero_arena_file_bytes"]):
        raise AssertionError("v0.37 history retained sidecar file length")

    crashes = row["crash_matrices"]
    if int(crashes["case_count"]) != 26:
        raise AssertionError("v0.37 crash matrix case count drifted")
    for name in (
        "fresh_empty_queue",
        "fresh_nonempty_queue",
        "partial_head",
        "final_arena_reset",
    ):
        case = crashes[name]
        if not bool(case["all_exact_committed_state_match"]):
            raise AssertionError(f"v0.37 {name} crash state drifted")
        if not bool(case["all_recovery_scan_free"]):
            raise AssertionError(f"v0.37 {name} recovery scanned history")
        if not bool(case["all_second_recovery_idempotent"]):
            raise AssertionError(f"v0.37 {name} second recovery was not idempotent")


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
    candidate = _run_phase(
        "segregated_descriptor_arena",
        run_segregated_descriptor_arena_experiment,
    )
    _run_phase("candidate_validation", lambda: _validate_candidate(candidate))

    out = {
        "experiment": "v0.37_segregated_retirement_descriptor_arena",
        "semantic_guard": {
            "membership_equal": semantic_guard["membership_equal"],
            "materialization_equal": semantic_guard["materialization_equal"],
            "head_index_equal": semantic_guard["head_index_equal"],
            "all_derived_fresh": semantic_guard["all_derived_fresh"],
            "full_assembly_equal": semantic_guard["full_assembly_equal"],
            "partial_assembly_equal": semantic_guard["partial_assembly_equal"],
        },
        "segregated_arena": candidate,
        "observe": (
            "v0.36 shows that logical descriptor freedom is insufficient for physical release when descriptor "
            "pairs are interleaved below unrelated committed suffix pages."
        ),
        "first_principle": (
            "bounded physical release requires reclaimable objects to occupy an independently reclaimable "
            "placement domain. If that domain can be reset only when it has zero committed live references, "
            "publication can remain O(1); reusable identity continuity must live outside the reset domain."
        ),
        "hypothesis_under_test": (
            "a sidecar retirement-descriptor arena whose committed length and monotonic incarnation source are "
            "rooted in the primary superblock can return all descriptor file length on queue drain without "
            "descriptor-history scans, relocation, or ABA aliasing."
        ),
        "prediction": (
            "a real three-descriptor peak will occupy six sidecar pages and return to zero after drain; repeated "
            "growth/drain cycles will also return to zero; page zero will be safely reused only under a newer "
            "incarnation; and SIGKILL across arena write/sync, primary publication, and post-commit truncation "
            "will expose exact committed state with scan-free restart convergence."
        ),
        "result": (
            "survived bounded falsification: the segregated sidecar returns the tested three-pair peak to zero "
            "file length when the retirement queue drains, repeats that reset across growth cycles, rejects the "
            "demonstrated stale pre-reset identity after page-zero reuse, and preserves exact committed state in "
            "the tested SIGKILL matrix. This does not yet reclaim excess arena capacity while the queue remains "
            "nonempty and does not establish filesystem block deallocation or hardware power-loss safety."
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
