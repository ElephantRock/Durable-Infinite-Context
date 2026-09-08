from __future__ import annotations

import hashlib
import json
from pathlib import Path

import run_recovery_interruption_experiment


ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "recovery_interruption_results.json"
EXPECTED_RESULT_SHA256 = "01bf1acc4593bd3573d3df4799de072760c38e6c163eb7fddae68880c7fbc89f"
EXPECTED_ARTIFACT_SHA256 = "978e2954fd73cc982922eb895e19f7b7e976a93141b4841a66bc9f92089672b3"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_semantic_guard(out: dict) -> None:
    required = (
        "membership_equal",
        "materialization_equal",
        "head_index_equal",
        "all_derived_fresh",
        "full_assembly_equal",
        "partial_assembly_equal",
    )
    if not all(bool(out["semantic_guard"][name]) for name in required):
        raise AssertionError("v0.26 semantic guard failed")


def _require_matrix(out: dict) -> None:
    matrix = out["matrix"]
    rows = matrix["rows"]
    if int(matrix["cases"]) != 8 or len(rows) != 8:
        raise AssertionError("v0.26 matrix cardinality drifted")
    if int(matrix["natural_cases"]) != 4 or int(matrix["synthetic_combined_cases"]) != 4:
        raise AssertionError("v0.26 natural/control split drifted")

    expected = {
        ("future_overflow", "future_delete_uncommitted", False),
        ("future_overflow", "future_delete_committed", False),
        ("fixed_tail", "tail_truncated", False),
        ("fixed_tail", "tail_synced", False),
        ("combined_control", "future_delete_uncommitted", True),
        ("combined_control", "future_delete_committed", True),
        ("combined_control", "tail_truncated", True),
        ("combined_control", "tail_synced", True),
    }
    observed = {
        (row["residue_kind"], row["failpoint"], bool(row["synthetic_combined_control"]))
        for row in rows
    }
    if observed != expected:
        raise AssertionError("v0.26 fixed interruption coverage drifted")

    for row in rows:
        if not row["interrupted_snapshot_exact"]:
            raise AssertionError("v0.26 interrupted cleanup exposed torn committed state")
        if not row["existing_keys_found_after_interrupt"]:
            raise AssertionError("v0.26 interrupted cleanup lost pre-existing membership")
        if row["abandoned_visible_after_interrupt"]:
            raise AssertionError("v0.26 interrupted cleanup exposed abandoned membership")
        if not row["converged_to_exact_snapshot"]:
            raise AssertionError("v0.26 restart did not converge to exact committed state")
        if int(row["final_future_rows"]) != 0 or int(row["final_tail_bytes"]) != 0:
            raise AssertionError("v0.26 restart left cleanup residue")
        if not row["final_audit_valid"]:
            raise AssertionError("v0.26 final membership audit failed")

        for retry_name in ("retry_one", "retry_two"):
            retry = row[retry_name]
            if int(retry["logical_redo"]) != 0:
                raise AssertionError("v0.26 recovery required application logical redo")
            if not retry["cleanup_epoch_index_used"]:
                raise AssertionError("v0.26 recovery lost indexed future-row discovery")

        retry_two = row["retry_two"]
        if any(
            int(retry_two[name]) != 0
            for name in (
                "deleted_future_overflow_rows",
                "reclaimed_tail_bytes",
                "truncate_calls",
                "fixed_file_fsyncs",
                "cleanup_sqlite_commits",
            )
        ):
            raise AssertionError("v0.26 second completed recovery was not a cleanup no-op")

    uncommitted = [row for row in rows if row["failpoint"] == "future_delete_uncommitted"]
    if len(uncommitted) != 2 or any(int(row["future_rows_after_interrupt"]) != 1 for row in uncommitted):
        raise AssertionError("v0.26 uncommitted SQLite DELETE rollback boundary drifted")
    if any(int(row["retry_one"]["deleted_future_overflow_rows"]) != 1 for row in uncommitted):
        raise AssertionError("v0.26 retry did not delete rolled-back future row")

    committed = [row for row in rows if row["failpoint"] == "future_delete_committed"]
    if len(committed) != 2 or any(int(row["future_rows_after_interrupt"]) != 0 for row in committed):
        raise AssertionError("v0.26 committed SQLite DELETE durability boundary drifted")

    natural_tail = [row for row in rows if row["residue_kind"] == "fixed_tail"]
    if len(natural_tail) != 2 or any(int(row["pre_tail_bytes"]) != 139264 for row in natural_tail):
        raise AssertionError("v0.26 natural stale-tail fixture drifted")

    tail_interrupts = [row for row in rows if row["failpoint"] in {"tail_truncated", "tail_synced"}]
    if len(tail_interrupts) != 4 or any(int(row["tail_bytes_after_interrupt"]) != 0 for row in tail_interrupts):
        raise AssertionError("v0.26 process-SIGKILL file-size observation drifted")

    resurrection = [row for row in rows if row["resurrection_checked"]]
    if len(resurrection) != 6:
        raise AssertionError("v0.26 resurrection-control coverage drifted")
    if any(bool(row["abandoned_visible_after_epoch_advance"]) for row in resurrection):
        raise AssertionError("v0.26 abandoned future row resurrected")
    if not all(bool(row["replacement_visible_after_epoch_advance"]) for row in resurrection):
        raise AssertionError("v0.26 replacement admission was lost")


def main() -> None:
    recorded_text = RESULTS_PATH.read_text()
    recorded = json.loads(recorded_text)
    try:
        out = run_recovery_interruption_experiment.run()
        _require_semantic_guard(out)
        _require_matrix(out)

        actual_sha = _sha256(RESULTS_PATH)
        if actual_sha != EXPECTED_RESULT_SHA256:
            raise AssertionError(
                "v0.26 executable evidence drifted from initial successful CI artifact: "
                f"expected {EXPECTED_RESULT_SHA256}, observed {actual_sha}"
            )
        if recorded != out:
            raise AssertionError("committed v0.26 ledger differs from executable experiment object")

        print("RECORDED_RECOVERY_INTERRUPTION_RESULTS_MATCH_EXECUTABLE_EXPERIMENT")
        print(f"RESULT_SHA256={actual_sha}")
        print(f"INITIAL_ARTIFACT_SHA256={EXPECTED_ARTIFACT_SHA256}")
    finally:
        RESULTS_PATH.write_text(recorded_text)


if __name__ == "__main__":
    main()
