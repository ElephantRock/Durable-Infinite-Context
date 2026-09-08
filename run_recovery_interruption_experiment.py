from __future__ import annotations

import json
from pathlib import Path

from simulator.normalized_membership import run_v016_normalized_case
from simulator.recovery_interruption import (
    RECOVERY_INTERRUPTION_CASES,
    run_recovery_interruption_matrix,
)

ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "recovery_interruption_results.json"


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
        raise AssertionError("v0.16 semantic guard failed before v0.26 experiment")


def run() -> dict:
    semantic_guard = run_v016_normalized_case(
        entity_count=128,
        predicate_count=16,
        history_depth=8,
        changed_count=1,
    )
    _require_semantic_guard(semantic_guard)

    matrix = run_recovery_interruption_matrix()
    rows = matrix["rows"]
    expected_cases = sum(len(v) for v in RECOVERY_INTERRUPTION_CASES.values())
    if int(matrix["cases"]) != expected_cases or len(rows) != expected_cases:
        raise AssertionError("v0.26 recovery interruption matrix cardinality drifted")
    if expected_cases != 8:
        raise AssertionError("v0.26 fixed matrix must contain eight cases")

    if not all(row["interrupted_snapshot_exact"] for row in rows):
        raise AssertionError("interrupted recovery exposed a torn committed snapshot")
    if not all(row["existing_keys_found_after_interrupt"] for row in rows):
        raise AssertionError("interrupted recovery lost pre-existing membership")
    if any(row["abandoned_visible_after_interrupt"] for row in rows):
        raise AssertionError("interrupted recovery exposed abandoned membership")
    if not all(row["converged_to_exact_snapshot"] for row in rows):
        raise AssertionError("restarted recovery failed to converge to exact snapshot")
    if not all(row["final_future_rows"] == 0 and row["final_tail_bytes"] == 0 for row in rows):
        raise AssertionError("restarted recovery left cleanup residue")
    if not all(row["final_audit_valid"] for row in rows):
        raise AssertionError("restarted recovery failed final membership audit")
    if not all(int(row["retry_one"]["logical_redo"]) == 0 for row in rows):
        raise AssertionError("first retry required application logical redo")
    if not all(int(row["retry_two"]["logical_redo"]) == 0 for row in rows):
        raise AssertionError("second retry required application logical redo")
    if not all(
        int(row["retry_two"]["deleted_future_overflow_rows"]) == 0
        and int(row["retry_two"]["reclaimed_tail_bytes"]) == 0
        for row in rows
    ):
        raise AssertionError("second retry was not cleanup-idempotent")

    future_uncommitted = [
        row for row in rows if row["failpoint"] == "future_delete_uncommitted"
    ]
    if not future_uncommitted or not all(
        int(row["future_rows_after_interrupt"]) == 1 for row in future_uncommitted
    ):
        raise AssertionError("uncommitted SQLite cleanup did not roll back after SIGKILL")

    future_committed = [
        row for row in rows if row["failpoint"] == "future_delete_committed"
    ]
    if not future_committed or not all(
        int(row["future_rows_after_interrupt"]) == 0 for row in future_committed
    ):
        raise AssertionError("committed SQLite cleanup did not survive SIGKILL")

    tail_rows = [
        row for row in rows if row["failpoint"] in {"tail_truncated", "tail_synced"}
    ]
    if not tail_rows or not all(int(row["tail_bytes_after_interrupt"]) == 0 for row in tail_rows):
        raise AssertionError("SIGKILL model did not retain completed file-size truncate")

    resurrection_rows = [row for row in rows if row["resurrection_checked"]]
    if not resurrection_rows:
        raise AssertionError("v0.26 did not exercise later-epoch resurrection control")
    if any(bool(row["abandoned_visible_after_epoch_advance"]) for row in resurrection_rows):
        raise AssertionError("abandoned future row resurrected after recovery interruption")
    if not all(bool(row["replacement_visible_after_epoch_advance"]) for row in resurrection_rows):
        raise AssertionError("later epoch-advancing replacement admission was lost")

    out = {
        "experiment": "v0.26_recovery_interruption",
        "semantic_guard": {
            "membership_equal": semantic_guard["membership_equal"],
            "materialization_equal": semantic_guard["materialization_equal"],
            "head_index_equal": semantic_guard["head_index_equal"],
            "all_derived_fresh": semantic_guard["all_derived_fresh"],
            "full_assembly_equal": semantic_guard["full_assembly_equal"],
            "partial_assembly_equal": semantic_guard["partial_assembly_equal"],
        },
        "matrix": matrix,
        "hypothesis_under_test": (
            "v0.25 future-row deletion and fixed-tail truncation are restart-convergent "
            "under process SIGKILL at their internal commit and durability boundaries"
        ),
        "prediction": (
            "an uncommitted future-row DELETE rolls back; a committed DELETE survives; "
            "ftruncate changes the observed file-size frontier before the explicit fixed-file "
            "fsync in the tested process-crash model; every interruption preserves the exact "
            "committed logical snapshot and repeated restart converges with zero logical redo"
        ),
        "result": (
            "survives only if all eight natural/synthetic-control cases preserve committed state, "
            "converge to zero future rows and zero stale tail, remain idempotent on the second "
            "retry, and prevent abandoned-row resurrection after a later epoch advance"
        ),
        "measurement_scope": (
            "real process SIGKILL around SQLite DELETE transaction boundaries and fixed-file "
            "ftruncate/fsync boundaries, exact committed logical snapshots, indexed future-row "
            "visibility, file-length residue, and application logical redo. A truncate observed "
            "after SIGKILL before fsync is not a power-loss guarantee; SQLite internal WAL/fsync "
            "work, filesystem/device I/O, multi-writer recovery, and production latency are not measured."
        ),
    }
    RESULTS_PATH.write_text(json.dumps(out, indent=2))
    print("RECOVERY_INTERRUPTION_RESULTS_JSON")
    print(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    run()
