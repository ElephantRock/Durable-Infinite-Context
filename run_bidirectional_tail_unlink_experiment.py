from __future__ import annotations

import json
from pathlib import Path

from simulator.bidirectional_tail_scaling import run_bidirectional_tail_scaling_experiment
from simulator.bidirectional_tail_unlink import run_bidirectional_tail_unlink_experiment
from simulator.bidirectional_topology_crash import run_bidirectional_topology_crash_experiment
from simulator.normalized_membership import run_v016_normalized_case

ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "bidirectional_tail_unlink_results.json"
DIAGNOSTIC_PATH = ROOT / "bidirectional_tail_unlink_diagnostic.json"


def _write_failure_diagnostic(phase: str, exc: Exception) -> None:
    payload = {
        "experiment": "v0.39_bidirectional_free_tail_unlink",
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
        raise AssertionError("v0.16 semantic guard failed before v0.39 experiment")


def _validate_candidate(row: dict) -> None:
    if not bool(row["survived"]):
        raise AssertionError("v0.39 non-head tail-unlink candidate did not survive")
    if int(row["candidate_descriptor_history_walks"]) != 0:
        raise AssertionError("v0.39 candidate introduced descriptor-history work")
    if int(row["candidate_relocations"]) != 0:
        raise AssertionError("v0.39 candidate introduced descriptor relocation")

    control = row["v038_control"]
    if bool(control["trace"]["released"]):
        raise AssertionError("v0.38 control unexpectedly released buried physical tail")
    if int(control["trace"]["retirement_descriptor_preads"]) != 0:
        raise AssertionError("v0.38 control searched descriptor storage")
    if int(control["retained_arena_pages"]) != 6:
        raise AssertionError("v0.39 control fixture no longer retains six arena pages")

    release = row["non_head_release"]
    if int(release["released_arena_pages"]) != 2 or int(release["released_arena_bytes"]) != 8192:
        raise AssertionError("v0.39 did not release exactly one descriptor pair")
    if int(release["queue_before"]["descriptor_free_head_page"]) != 2:
        raise AssertionError("v0.39 release fixture no longer buries physical tail behind page 2")
    trace = release["trace"]
    if bool(trace["tail_was_free_head"]):
        raise AssertionError("v0.39 release accidentally tested head-aligned tail")
    if int(trace["predecessor_page"]) != 2:
        raise AssertionError("v0.39 did not directly identify tail predecessor")
    if int(trace["retirement_descriptor_preads"]) != 4:
        raise AssertionError("v0.39 non-head unlink read count drifted")
    if int(trace["retirement_descriptor_pwrites"]) != 1:
        raise AssertionError("v0.39 non-head unlink write count drifted")
    if int(trace["retirement_descriptors_scanned"]) != 0:
        raise AssertionError("v0.39 non-head unlink scanned descriptor history")
    if int(release["queue_after"]["descriptor_arena_pages"]) != 4:
        raise AssertionError("v0.39 release fixture did not finish at four arena pages")

    reuse = row["reuse_head_predecessor_repair"]
    if not bool(reuse["new_head_predecessor_cleared"]):
        raise AssertionError("v0.39 free-head reuse did not clear new head predecessor")
    if int(reuse["queue_after"]["descriptor_free_head_page"]) != 4:
        raise AssertionError("v0.39 free-head reuse did not advance head to page 4")

    identity = row["identity_after_non_head_shrink"]
    if not bool(identity["stale_rejected_after_shrink"]):
        raise AssertionError("v0.39 committed frontier exposed truncated stale descriptor")
    if not bool(identity["incarnation_advanced"]):
        raise AssertionError("v0.39 tail address reuse did not advance incarnation")
    if not bool(identity["stale_rejected_after_reuse"]):
        raise AssertionError("v0.39 stale identity aliased reused physical tail")
    if int(identity["arena_pages_after_reuse"]) != 6:
        raise AssertionError("v0.39 identity fixture did not re-extend arena")

    crashes = row["crash_matrix"]
    if int(crashes["case_count"]) != 6:
        raise AssertionError("v0.39 shrink crash-matrix case count drifted")
    if not bool(crashes["all_exact_committed_state_match"]):
        raise AssertionError("v0.39 crash recovery committed state drifted")
    if not bool(crashes["all_recovery_scan_free"]):
        raise AssertionError("v0.39 recovery scanned history")
    if not bool(crashes["all_second_recovery_idempotent"]):
        raise AssertionError("v0.39 second recovery was not idempotent")


def _validate_topology_crashes(row: dict) -> None:
    if int(row["case_count"]) != 8:
        raise AssertionError("v0.39 topology crash-matrix case count drifted")
    if not bool(row["all_exact_committed_state_match"]):
        raise AssertionError("v0.39 predecessor topology publication is not crash exact")
    if not bool(row["all_recovery_scan_free"]):
        raise AssertionError("v0.39 topology recovery scanned history")
    if not bool(row["all_second_recovery_idempotent"]):
        raise AssertionError("v0.39 topology second recovery was not idempotent")
    push = row["predecessor_push"]
    pop = row["free_head_reuse"]
    if int(push["case_count"]) != 4 or int(pop["case_count"]) != 4:
        raise AssertionError("v0.39 topology crash submatrix drifted")
    if int(push["clean_post_state"]["queue"]["descriptor_free_head_page"]) != 2:
        raise AssertionError("v0.39 clean predecessor push did not publish page 2 head")
    if int(pop["clean_post_state"]["queue"]["descriptor_free_head_page"]) != 4:
        raise AssertionError("v0.39 clean free-head reuse did not publish page 4 head")


def _validate_scaling(row: dict) -> None:
    if list(row["target_descriptor_counts"]) != [3, 4, 5, 6]:
        raise AssertionError("v0.39 free-chain scaling domain drifted")
    if not bool(row["all_constant_shrink_work"]):
        raise AssertionError("v0.39 shrink work depends on free-chain length")
    if [int(item["free_chain_length_before"]) for item in row["rows"]] != [2, 3, 4, 5]:
        raise AssertionError("v0.39 free-chain lengths drifted")
    for item in row["rows"]:
        if int(item["retirement_descriptor_preads"]) != 4:
            raise AssertionError("v0.39 scaled shrink read count drifted")
        if int(item["retirement_descriptor_pwrites"]) != 1:
            raise AssertionError("v0.39 scaled shrink write count drifted")
        if int(item["retirement_descriptors_scanned"]) != 0:
            raise AssertionError("v0.39 scaled shrink scanned descriptor history")
        if int(item["candidate_relocations"]) != 0:
            raise AssertionError("v0.39 scaled shrink relocated live descriptors")
        if int(item["arena_pages_before"]) - int(item["arena_pages_after"]) != 2:
            raise AssertionError("v0.39 scaled shrink did not release exactly one pair")


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
    candidate = _run_phase("bidirectional_tail_unlink", run_bidirectional_tail_unlink_experiment)
    _run_phase("candidate_validation", lambda: _validate_candidate(candidate))
    topology_crashes = _run_phase(
        "bidirectional_topology_crashes", run_bidirectional_topology_crash_experiment
    )
    _run_phase(
        "topology_crash_validation", lambda: _validate_topology_crashes(topology_crashes)
    )
    scaling = _run_phase("bidirectional_tail_scaling", run_bidirectional_tail_scaling_experiment)
    _run_phase("scaling_validation", lambda: _validate_scaling(scaling))

    out = {
        "experiment": "v0.39_bidirectional_free_tail_unlink",
        "semantic_guard": {
            "membership_equal": semantic_guard["membership_equal"],
            "materialization_equal": semantic_guard["materialization_equal"],
            "head_index_equal": semantic_guard["head_index_equal"],
            "all_derived_fresh": semantic_guard["all_derived_fresh"],
            "full_assembly_equal": semantic_guard["full_assembly_equal"],
            "partial_assembly_equal": semantic_guard["partial_assembly_equal"],
        },
        "bidirectional_tail_unlink": candidate,
        "topology_maintenance_crashes": topology_crashes,
        "free_chain_scaling": scaling,
        "observe": (
            "v0.38 can shrink a live descriptor arena only when the free-list head is itself the physical tail; "
            "a free physical tail buried behind another free node remains unreclaimable without stronger current topology."
        ),
        "first_principle": (
            "bounded arbitrary unlink requires direct authority for both adjacent current-state links of the target; "
            "searching a singly linked free list for the target predecessor would make work depend on pool size."
        ),
        "hypothesis_under_test": (
            "encoding one tagged predecessor identity in fields unused by FREE descriptors, and maintaining only the "
            "immediately affected link on push/pop, is sufficient to detach a directly addressed physical free tail "
            "with bounded work while preserving crash publication order and ABA-resistant identity."
        ),
        "prediction": (
            "on a real free chain [2 -> 4] with page 4 as physical tail and one live queued descriptor, v0.38 will "
            "no-op while v0.39 will read only tail and predecessor, rewrite only predecessor, publish arena 6 -> 4 "
            "pages, reject stale page-4 identity across later reuse, recover exactly across six shrink SIGKILL stages, "
            "publish predecessor push/free-head reuse exactly across eight additional process-crash cases, and hold "
            "shrink work at 4 descriptor preads / 1 pwrite / 0 scans as free-chain length grows from 2 to 5."
        ),
        "result": (
            "candidate result is determined by the executable v0.39 simulator and is bounded to current free-topology "
            "authority; it does not claim historical traversal, hardware torn-write safety, or filesystem block release."
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
