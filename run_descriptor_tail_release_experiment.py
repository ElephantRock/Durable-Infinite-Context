from __future__ import annotations

import json
from pathlib import Path

from simulator.normalized_membership import run_v016_normalized_case
from simulator.trimmable_retirement_descriptors import run_tail_release_falsification

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
    control = _run_phase("real_tail_release_control", run_tail_release_falsification)

    if not control["falsified"]:
        raise AssertionError("v0.36 tail-release control failed to falsify candidate")
    if int(control["candidate_history_walks"]) != 0:
        raise AssertionError("v0.36 control introduced descriptor-history work")
    if int(control["candidate_physical_pages_released"]) != 0:
        raise AssertionError("v0.36 control unexpectedly released physical pages")
    for name in ("single_descriptor_case", "three_descriptor_peak_case"):
        row = control[name]
        if bool(row["free_head_is_physical_tail"]):
            raise AssertionError("v0.36 real free head unexpectedly equals physical tail")
        if int(row["committed_suffix_pages_after_free_head"]) <= 0:
            raise AssertionError("v0.36 real free head lacks committed suffix evidence")

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
        "tail_release_control": control,
        "observe": (
            "v0.35 stops descriptor storage from following completed-generation history once reusable "
            "capacity exists, but every allocated pair remains retained after a backlog peak."
        ),
        "first_principle": (
            "file-tail truncation can release a buried free object without relocation only when that object "
            "already occupies the committed physical suffix. A current-root-only mechanism must reject the "
            "candidate rather than search historical/free-list state for a convenient tail object."
        ),
        "hypothesis_under_test": (
            "after the real retirement queue drains, the current descriptor free-list head may also be the "
            "committed append tail, allowing one descriptor pair to be returned with zero history walks."
        ),
        "prediction": (
            "if the hypothesis is true, `free_head_page + 2 == next_physical_page` in a real one-descriptor "
            "case and/or a real three-descriptor peak case. If committed pages remain above the free head in "
            "both cases, head-only tail release is structurally inapplicable and must be rejected."
        ),
        "result": (
            "falsified: real append-local data/metadata placement leaves committed pages above the current "
            "descriptor free-list head after cleanup. Tail-only release returns zero pages without scanning. "
            "Any surviving bounded reduction mechanism needs an additional capability such as segregated "
            "placement, maintained tail-addressable metadata, relocation, or a broader allocator able to "
            "reuse buried descriptor pairs; this experiment does not choose among those alternatives."
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
