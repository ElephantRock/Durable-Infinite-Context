from __future__ import annotations

import json
from pathlib import Path

from simulator.normalized_membership import run_v016_normalized_case
from simulator.trimmable_retirement_crash_matrices import (
    FRESH_AFTER_RELEASE_FAILPOINTS,
    TRIM_FAILPOINTS,
    run_trimmable_retirement_crash_matrices,
)
from simulator.trimmable_retirement_descriptors import run_tail_release_cycle

ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "descriptor_tail_release_results.json"
DIAGNOSTIC_PATH = ROOT / "descriptor_tail_release_diagnostic.json"


def _write_failure_diagnostic(phase: str, exc: Exception) -> None:
    payload = {
        "experiment": "v0.36_descriptor_tail_release",
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
        raise AssertionError("v0.16 semantic guard failed before v0.36 experiment")


def _require_matrix(matrix: dict, failpoints: tuple[str, ...]) -> None:
    if matrix["failpoints"] != list(failpoints):
        raise AssertionError("v0.36 crash failpoint set drifted")
    if not matrix["all_exact_committed_state_match"]:
        raise AssertionError("v0.36 crash matrix exposed mixed committed state")
    if not matrix["all_recovery_scan_free"]:
        raise AssertionError("v0.36 crash recovery introduced scan-based repair")
    if not matrix["all_second_recovery_idempotent"]:
        raise AssertionError("v0.36 second recovery lost idempotence")


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
    cycle = _run_phase("tail_release_cycle", run_tail_release_cycle)
    crashes = _run_phase("crash_matrices", run_trimmable_retirement_crash_matrices)

    release = cycle["tail_release_trace"]
    blocked = cycle["second_release_trace"]
    if int(release["physical_pages_released"]) != 2:
        raise AssertionError("v0.36 did not release one descriptor pair")
    if int(cycle["descriptor_pool_after_peak_release"]) != 2:
        raise AssertionError("v0.36 retained historical descriptor peak after eligible release")
    if bool(blocked["tail_release_eligible"]):
        raise AssertionError("v0.36 scanned/trimmed through an interleaved non-tail descriptor")
    if int(blocked["retirement_descriptor_preads"]) != 2:
        raise AssertionError("v0.36 blocked non-tail release was not head-local")
    if not cycle["released_identity"]["rejected_immediately_after_release"]:
        raise AssertionError("v0.36 released descriptor identity remained resolvable")
    if not cycle["released_identity"]["rejected_after_frontier_regrowth"]:
        raise AssertionError("v0.36 released descriptor identity resurrected after later appends")
    if [int(row["descriptor_incarnation"]) for row in cycle["after_regrowth_queue"]["descriptors"]] != [4, 5, 6]:
        raise AssertionError("v0.36 global descriptor incarnation counter reset")
    if int(cycle["descriptor_pool_after_live_demand_returns"]) != 3:
        raise AssertionError("v0.36 descriptor pool failed to regrow when live demand returned")
    if not cycle["all_513_keys_visible"]:
        raise AssertionError("v0.36 real release/regrowth cycle lost live primary keys")

    _require_matrix(crashes["tail_release"], TRIM_FAILPOINTS)
    _require_matrix(crashes["fresh_after_release"], FRESH_AFTER_RELEASE_FAILPOINTS)
    if not crashes["fresh_after_release"]["all_released_identity_rejected"]:
        raise AssertionError("v0.36 released identity survived later fresh allocation")
    if int(crashes["case_count"]) != 8:
        raise AssertionError("v0.36 crash matrix count drifted")

    out = {
        "experiment": "v0.36_descriptor_tail_release",
        "semantic_guard": {
            "membership_equal": semantic_guard["membership_equal"],
            "materialization_equal": semantic_guard["materialization_equal"],
            "head_index_equal": semantic_guard["head_index_equal"],
            "all_derived_fresh": semantic_guard["all_derived_fresh"],
            "full_assembly_equal": semantic_guard["full_assembly_equal"],
            "partial_assembly_equal": semantic_guard["partial_assembly_equal"],
        },
        "tail_release_cycle": cycle,
        "crash_matrices": crashes,
        "observe": (
            "v0.35 stops descriptor storage from following completed-generation history once reusable "
            "capacity exists, but every allocated pair remains in the internal pool after backlog peaks."
        ),
        "first_principle": (
            "physical capacity can be returned without history scans only when releasable storage is "
            "already identified by current roots and lies at a reclaimable physical boundary. Releasing "
            "a physical address must also preserve logical identity across any later reuse of that address."
        ),
        "hypothesis_under_test": (
            "release only the committed descriptor free-list head when its two-page pair is exactly the "
            "committed append tail. Publish the shorter frontier before derived physical truncation, keep "
            "the step head-local, and allocate descriptor incarnations from one committed monotonic counter "
            "so physical release cannot reset descriptor identity."
        ),
        "prediction": (
            "the real three-descriptor pool should shrink to two pages-pairs after one constant-local tail "
            "release; a second step should stop at the interleaved non-tail boundary rather than scan; "
            "later live demand should regrow the pool only when needed, released stale identities must remain "
            "rejected, and SIGKILL before/after frontier publication and later fresh allocation must recover "
            "exact state without descriptor scans or logical redo."
        ),
        "result": (
            "survives only as an opportunistic tail-release mechanism. It does not claim arbitrary excess "
            "descriptor compaction: non-tail free descriptors remain retained unless a future mechanism can "
            "relocate or segregate them without moving historical work onto the foreground path."
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
