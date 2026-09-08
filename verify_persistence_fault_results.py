from __future__ import annotations

import hashlib
import json
from pathlib import Path

import run_persistence_fault_experiment


ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "persistence_fault_results.json"
EXPECTED_RESULT_SHA256 = "4d55252a4d2358d1c815a74b35cfcec4fd862f39be01c7d38cd5a7b66ab6c234"
EXPECTED_ARTIFACT_SHA256 = "a33559cdf1b37a5a3ac2d9a4bb3467ad376d8ddfd63ca6fb1cfedb37b8e93202"


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
        raise AssertionError("v0.27 semantic guard failed")


def _require_matrix(out: dict) -> None:
    matrix = out["matrix"]
    rows = matrix["rows"]
    if int(matrix["cases"]) != 9 or len(rows) != 9:
        raise AssertionError("v0.27 matrix cardinality drifted")

    expected = {
        ("future_overflow", "future_delete_uncommitted"),
        ("future_overflow", "future_delete_committed"),
        ("fixed_tail", "tail_truncated"),
        ("fixed_tail", "tail_synced"),
        ("combined", "future_delete_uncommitted"),
        ("combined", "future_delete_committed"),
        ("combined", "after_future_cleanup"),
        ("combined", "tail_truncated"),
        ("combined", "tail_synced"),
    }
    observed = {(row["residue_kind"], row["failpoint"]) for row in rows}
    if observed != expected:
        raise AssertionError("v0.27 fixed power-loss coverage drifted")

    for row in rows:
        if not row["logical_snapshot_exact_after_power_loss"]:
            raise AssertionError("v0.27 power loss exposed torn committed logical state")
        if not row["converged"]:
            raise AssertionError("v0.27 derived recovery did not restart-converge")
        if not row["second_retry_noop"]:
            raise AssertionError("v0.27 second completed recovery was not a no-op")
        if int(row["retry_one"]["logical_redo"]) != 0 or int(row["retry_two"]["logical_redo"]) != 0:
            raise AssertionError("v0.27 recovery required application logical redo")
        final_state = row["final_state"]
        if int(final_state["durable_future_rows"]) != 0:
            raise AssertionError("v0.27 final state retained future overflow residue")
        if int(final_state["durable_tail_bytes"]) != 0:
            raise AssertionError("v0.27 final state retained durable stale tail")
        if int(final_state["volatile_tail_bytes"]) != 0:
            raise AssertionError("v0.27 final state retained volatile stale tail")

    uncommitted = [row for row in rows if row["failpoint"] == "future_delete_uncommitted"]
    if len(uncommitted) != 2 or any(int(row["future_rows_after_power_loss"]) != 1 for row in uncommitted):
        raise AssertionError("v0.27 uncommitted SQLite delete durability boundary drifted")

    committed = [row for row in rows if row["failpoint"] == "future_delete_committed"]
    if len(committed) != 2 or any(int(row["future_rows_after_power_loss"]) != 0 for row in committed):
        raise AssertionError("v0.27 committed SQLite delete durability boundary drifted")

    unsynced = [row for row in rows if row["failpoint"] == "tail_truncated"]
    if len(unsynced) != 2 or any(int(row["durable_tail_bytes_after_power_loss"]) != 139264 for row in unsynced):
        raise AssertionError("v0.27 unsynced truncate no longer reverts to durable file length")

    synced = [row for row in rows if row["failpoint"] == "tail_synced"]
    if len(synced) != 2 or any(int(row["durable_tail_bytes_after_power_loss"]) != 0 for row in synced):
        raise AssertionError("v0.27 synced truncate no longer survives modeled power loss")

    marker = matrix["marker_counterexample"]
    if marker["marker_protocol_converged"]:
        raise AssertionError("v0.27 premature durable marker unexpectedly converged")
    if int(marker["stranded_tail_bytes"]) != 139264:
        raise AssertionError("v0.27 marker counterexample stranded-tail size drifted")
    after_power_loss = marker["after_power_loss"]
    if not bool(after_power_loss["durable_tail_clean_marker"]):
        raise AssertionError("v0.27 marker counterexample lost its durable marker")
    if int(after_power_loss["durable_tail_bytes"]) != 139264:
        raise AssertionError("v0.27 marker counterexample no longer strands durable tail")
    if not marker["derived_protocol_can_repair"]:
        raise AssertionError("v0.27 derivation-based recovery could not repair marker residue")

    scaling = matrix["reclamation_scaling"]["rows"]
    capacities = [32, 128, 512, 2048]
    expected_bytes = [4096 * (capacity + 2) for capacity in capacities]
    if [int(row["initial_capacity"]) for row in scaling] != capacities:
        raise AssertionError("v0.27 reclamation capacity sweep drifted")
    if [int(row["durable_tail_after_unsynced_truncate_power_loss"]) for row in scaling] != expected_bytes:
        raise AssertionError("v0.27 unsynced power-loss residue scaling drifted")
    if [int(row["retry_reclaimed_tail_bytes"]) for row in scaling] != expected_bytes:
        raise AssertionError("v0.27 retry reclamation scaling drifted")
    if any(int(row["final_durable_tail_bytes"]) != 0 for row in scaling):
        raise AssertionError("v0.27 scaling retry failed to reclaim durable tail")


def main() -> None:
    recorded_text = RESULTS_PATH.read_text()
    recorded = json.loads(recorded_text)
    try:
        out = run_persistence_fault_experiment.run()
        _require_semantic_guard(out)
        _require_matrix(out)

        actual_sha = _sha256(RESULTS_PATH)
        if actual_sha != EXPECTED_RESULT_SHA256:
            raise AssertionError(
                "v0.27 executable evidence drifted from initial successful CI artifact: "
                f"expected {EXPECTED_RESULT_SHA256}, observed {actual_sha}"
            )
        if recorded != out:
            raise AssertionError("committed v0.27 ledger differs from executable experiment object")

        print("RECORDED_PERSISTENCE_FAULT_RESULTS_MATCH_EXECUTABLE_EXPERIMENT")
        print(f"RESULT_SHA256={actual_sha}")
        print(f"INITIAL_ARTIFACT_SHA256={EXPECTED_ARTIFACT_SHA256}")
    finally:
        RESULTS_PATH.write_text(recorded_text)


if __name__ == "__main__":
    main()
