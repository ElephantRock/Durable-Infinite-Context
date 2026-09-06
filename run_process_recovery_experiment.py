from __future__ import annotations

import json
from pathlib import Path

from simulator.process_recovery import FAILPOINTS, OPERATIONS, run_process_crash_case


ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "process_recovery_results.json"


def run() -> dict:
    phase_rows: list[dict] = []
    for operation in OPERATIONS:
        for failpoint in FAILPOINTS:
            case = run_process_crash_case(100, operation, failpoint)
            row = case.to_dict()
            phase_rows.append(row)
            print(
                "PROCESS_PHASE",
                operation,
                failpoint,
                {
                    "durable_phase": row["durable_phase_after_crash"],
                    "canonical_visible": row["canonical_visible_after_crash"],
                    "recovery_work": row["recovery_work"],
                    "canonical_recovery_mutations": row["recovery_trace"]["canonical_mutations"],
                    "equal": row["materialization_equal"],
                },
            )

    # The canonical mutation and CANONICAL_APPLIED marker share one SQLite
    # transaction. Recovery applies the canonical operation exactly once only when
    # the durable phase is still PREPARED; every later durable phase must trust the
    # committed canonical transaction and perform zero redundant canonical writes.
    canonical_apply_on_recovery = {"prepared_committed", "canonical_uncommitted"}
    for row in phase_rows:
        expected_mutations = 1 if row["failpoint"] in canonical_apply_on_recovery else 0
        if row["recovery_trace"]["canonical_mutations"] != expected_mutations:
            raise AssertionError(
                f"canonical recovery mutation mismatch for {row['failpoint']}: "
                f"expected {expected_mutations}, observed "
                f"{row['recovery_trace']['canonical_mutations']}"
            )
        if row["failpoint"] == "finalized_committed" and row["recovery_work"] != 0:
            raise AssertionError("fully finalized transaction unexpectedly required recovery work")

    locality_rows: list[dict] = []
    for entity_count in (100, 1_000, 10_000, 50_000):
        case = run_process_crash_case(
            entity_count,
            "delete_assertion",
            "partial_rebuild_committed",
        )
        row = case.to_dict()
        locality_rows.append(row)
        print(
            "PROCESS_SCALE",
            entity_count,
            {
                "recovery_work": row["recovery_work"],
                "reinvalidated": row["recovery_trace"]["reinvalidated_nodes"],
                "full_rebuild": row["full_rebuild_work"],
                "fraction": round(row["work_fraction_vs_full_rebuild"], 10),
            },
        )

    work_values = {row["recovery_work"] for row in locality_rows}
    if len(work_values) != 1:
        raise AssertionError(f"fixed-region process recovery work grew with cardinality: {work_values}")

    out = {
        "experiment": "v0.9_persistent_wal_process_crash_recovery",
        "storage": {
            "engine": "sqlite3",
            "journal_mode": "wal",
            "synchronous": "FULL",
            "process_failure": "SIGKILL",
        },
        "phase_matrix_entity_count": 100,
        "operations": list(OPERATIONS),
        "failpoints": list(FAILPOINTS),
        "phase_rows": phase_rows,
        "locality_cardinalities": [100, 1_000, 10_000, 50_000],
        "locality_rows": locality_rows,
        "invariant": (
            "transactional persistent intent plus atomic canonical/phase commit plus indexed "
            "affected-region invalidation must recover in a fresh process without stale reads "
            "or recovery work proportional to unrelated total memory"
        ),
        "scope": (
            "single SQLite database in WAL mode with synchronous=FULL; one in-flight writer; "
            "local filesystem process-crash experiment; no distributed or concurrent-writer claim"
        ),
    }
    RESULTS_PATH.write_text(json.dumps(out, indent=2))
    print("PROCESS_RECOVERY_RESULTS_JSON")
    print(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    run()
