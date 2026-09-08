from __future__ import annotations

import json
from pathlib import Path

from simulator.normalized_membership import run_v016_normalized_case
from simulator.persistence_fault_model import (
    DEFAULT_STALE_TAIL_BYTES,
    run_persistence_fault_matrix,
)

ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "persistence_fault_results.json"


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
        raise AssertionError("v0.16 semantic guard failed before v0.27 experiment")


def run() -> dict:
    semantic_guard = run_v016_normalized_case(
        entity_count=128,
        predicate_count=16,
        history_depth=8,
        changed_count=1,
    )
    _require_semantic_guard(semantic_guard)

    matrix = run_persistence_fault_matrix()
    rows = matrix["rows"]
    if int(matrix["cases"]) != 9 or len(rows) != 9:
        raise AssertionError("v0.27 fixed power-loss matrix must contain nine cases")
    if not all(row["logical_snapshot_exact_after_power_loss"] for row in rows):
        raise AssertionError("modeled power loss exposed a torn committed logical snapshot")
    if not all(row["converged"] and row["second_retry_noop"] for row in rows):
        raise AssertionError("derivation-based recovery did not restart-converge")
    if not all(int(row["retry_one"]["logical_redo"]) == 0 for row in rows):
        raise AssertionError("v0.27 recovery required application logical redo")

    uncommitted = [row for row in rows if row["failpoint"] == "future_delete_uncommitted"]
    if not uncommitted or not all(int(row["future_rows_after_power_loss"]) == 1 for row in uncommitted):
        raise AssertionError("uncommitted future-row deletion did not roll back in persistence model")

    committed = [row for row in rows if row["failpoint"] == "future_delete_committed"]
    if not committed or not all(int(row["future_rows_after_power_loss"]) == 0 for row in committed):
        raise AssertionError("committed future-row deletion did not survive persistence model")

    unsynced_tail = [row for row in rows if row["failpoint"] == "tail_truncated"]
    if not unsynced_tail or not all(
        int(row["durable_tail_bytes_after_power_loss"]) == DEFAULT_STALE_TAIL_BYTES
        for row in unsynced_tail
    ):
        raise AssertionError("unsynced truncate incorrectly survived modeled power loss")

    synced_tail = [row for row in rows if row["failpoint"] == "tail_synced"]
    if not synced_tail or not all(int(row["durable_tail_bytes_after_power_loss"]) == 0 for row in synced_tail):
        raise AssertionError("synced truncate did not survive modeled power loss")

    marker = matrix["marker_counterexample"]
    if marker["marker_protocol_converged"]:
        raise AssertionError("premature durable cleanup marker unexpectedly converged")
    if int(marker["stranded_tail_bytes"]) != DEFAULT_STALE_TAIL_BYTES:
        raise AssertionError("marker counterexample stranded-tail size drifted")
    if not marker["derived_protocol_can_repair"]:
        raise AssertionError("derivation-based recovery could not repair marker counterexample residue")

    scaling = matrix["reclamation_scaling"]["rows"]
    capacities = [32, 128, 512, 2048]
    expected_bytes = [4096 * (capacity + 2) for capacity in capacities]
    if [int(row["initial_capacity"]) for row in scaling] != capacities:
        raise AssertionError("v0.27 reclamation capacities drifted")
    if [int(row["retry_reclaimed_tail_bytes"]) for row in scaling] != expected_bytes:
        raise AssertionError("v0.27 retry reclamation volume drifted")

    out = {
        "experiment": "v0.27_persistence_fault_model",
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
            "the v0.25/v0.26 derivation-based cleanup protocol is restart-convergent under an "
            "explicit volatile-versus-durable persistence model, while a durable cleanup-complete "
            "marker written before the fixed-file fsync is unsafe"
        ),
        "prediction": (
            "an uncommitted SQLite delete is lost, a committed delete survives, an unsynced "
            "ftruncate is lost, and a synced truncate survives. Re-deriving residue on every "
            "restart must converge, whereas trusting a marker durably committed before the file "
            "fsync must strand stale tail after modeled power loss"
        ),
        "result": (
            "the derivation-based protocol survives the nine-case abstract power-loss matrix and "
            "the premature-marker protocol is falsified by a deterministic stranded-tail control. "
            "Retry reclamation volume still scales as 4096(C+2)=Theta(C)"
        ),
        "measurement_scope": (
            "deterministic abstract persistence model with separate volatile and durable file-size "
            "frontiers plus atomic durable SQLite-transaction commits. This is not a hardware test, "
            "does not model torn sectors, drive write caches, filesystem journaling, SQLite WAL "
            "internals, multi-writer execution, latency, or device write amplification. It tests "
            "protocol ordering and restart convergence only."
        ),
    }
    RESULTS_PATH.write_text(json.dumps(out, indent=2))
    print("PERSISTENCE_FAULT_RESULTS_JSON")
    print(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    run()
