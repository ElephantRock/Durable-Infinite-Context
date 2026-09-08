from __future__ import annotations

import json
from pathlib import Path

from simulator.durable_hybrid import (
    FAILPOINTS,
    SCENARIOS,
    run_crash_case,
    run_persistent_common_envelope,
    run_persistent_overflow_envelope,
)
from simulator.normalized_membership import run_v016_normalized_case


ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "durable_hybrid_results.json"


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
        raise AssertionError(
            "v0.16 exact semantic guard failed before v0.23 durable-hybrid experiment"
        )


def run() -> dict:
    semantic_guard = run_v016_normalized_case(
        entity_count=128,
        predicate_count=16,
        history_depth=8,
        changed_count=1,
    )
    _require_semantic_guard(semantic_guard)

    crash_rows: list[dict] = []
    for scenario in SCENARIOS:
        for failpoint in FAILPOINTS:
            case = run_crash_case(scenario, failpoint)
            row = case.to_dict()
            crash_rows.append(row)
            print(
                "DURABLE_HYBRID_CRASH",
                scenario,
                failpoint,
                {
                    "committed": row["expected_committed"],
                    "visible": row["target_visible_after_crash"],
                    "path": row["target_path_after_crash"],
                    "snapshot": row["exact_snapshot_match"],
                    "audit": row["audit_valid"],
                    "recovery_work": row["recovery_one"]["logical_work"],
                },
            )

    if len(crash_rows) != len(SCENARIOS) * len(FAILPOINTS):
        raise AssertionError("crash matrix cardinality drifted")
    if not all(row["exact_snapshot_match"] for row in crash_rows):
        raise AssertionError("a SIGKILL crash exposed a torn logical snapshot")
    if not all(row["audit_valid"] and row["existing_keys_found"] for row in crash_rows):
        raise AssertionError("a SIGKILL crash lost/duplicated durable membership")
    if not all(row["recovery_idempotent"] for row in crash_rows):
        raise AssertionError("application-level recovery was not idempotent")
    if any(int(row["recovery_one"]["logical_work"]) != 0 for row in crash_rows):
        raise AssertionError("single-transaction hybrid required unexpected application redo")
    if any(row["journal_mode"] != "wal" or int(row["synchronous"]) != 2 for row in crash_rows):
        raise AssertionError("durable hybrid crash matrix did not run under WAL + FULL")

    uncommitted = [
        row for row in crash_rows if row["failpoint"] != "committed"
    ]
    committed = [row for row in crash_rows if row["failpoint"] == "committed"]
    if any(row["target_visible_after_crash"] for row in uncommitted):
        raise AssertionError("uncommitted hybrid mutation survived SIGKILL")
    if any(not row["target_visible_after_crash"] for row in committed):
        raise AssertionError("committed hybrid mutation disappeared after SIGKILL")

    common = run_persistent_common_envelope()
    if common["global_max_source_slots_scanned"] > 8:
        raise AssertionError("persistent migration source scan exceeded fixed budget")
    if common["global_max_rows_moved"] > 8:
        raise AssertionError("persistent migration moved more rows than source budget")
    if common["global_max_primary_mutation_work"] > common["primary_mutation_work_bound"]:
        raise AssertionError("persistent common-path primary work exceeded modeled bound")
    if any(row["overflow_rows"] != 0 for row in common["rows"]):
        raise AssertionError("ordinary persistent workload used exceptional overflow")
    if any(row["lookup_overflow_checks"] != 0 for row in common["rows"]):
        raise AssertionError("ordinary successful read was contaminated by overflow")
    if any(row["lookup_metadata_rows"] != 1 for row in common["rows"]):
        raise AssertionError("ordinary read metadata work exceeded singleton descriptor")

    overflow = run_persistent_overflow_envelope()
    heights = [row["overflow_btree_height"] for row in overflow["rows"]]
    if heights != sorted(heights):
        raise AssertionError(f"persistent overflow B-tree height regressed: {heights}")
    if overflow["max_primary_mutation_work"] > 200:
        raise AssertionError("non-migrating overflow admission exceeded bounded primary cap")
    if overflow["overflow_logical_row_write_per_admission"] != 1:
        raise AssertionError("overflow admission logical-row write count drifted")

    migration_overflow_committed = next(
        row
        for row in committed
        if row["scenario"] == "migration_to_overflow"
    )
    if migration_overflow_committed["control_insert_trace"]["migration_rows_to_overflow"] <= 0:
        raise AssertionError("crash matrix failed to exercise migration-to-overflow boundary")
    if not migration_overflow_committed["control_insert_trace"]["migration_completed"]:
        raise AssertionError("migration-to-overflow committed control did not complete")

    for row in common["rows"]:
        print("DURABLE_HYBRID_COMMON", row)
    for row in overflow["rows"]:
        print("DURABLE_HYBRID_OVERFLOW", row)

    out = {
        "experiment": "v0.23_durable_hybrid_admission",
        "semantic_guard": {
            "membership_equal": semantic_guard["membership_equal"],
            "materialization_equal": semantic_guard["materialization_equal"],
            "head_index_equal": semantic_guard["head_index_equal"],
            "all_derived_fresh": semantic_guard["all_derived_fresh"],
            "full_assembly_equal": semantic_guard["full_assembly_equal"],
            "partial_assembly_equal": semantic_guard["partial_assembly_equal"],
        },
        "storage": {
            "engine": "sqlite3",
            "journal_mode": "wal",
            "synchronous": "FULL",
            "process_failure": "SIGKILL",
            "transaction_scope": (
                "one logical admission plus one bounded source-migration step plus all "
                "generation/overflow metadata in one SQLite transaction"
            ),
        },
        "scenarios": list(SCENARIOS),
        "failpoints": list(FAILPOINTS),
        "crash_rows": crash_rows,
        "common_envelope": common,
        "overflow_envelope": overflow,
        "observed_overflow_btree_heights": heights,
        "hypothesis_under_test": (
            "bounded primary generation migration and explicit overflow admission can be made "
            "process-crash atomic by committing the logical admission, bounded migration step, "
            "and routing metadata in one SQLite WAL transaction, without making successful "
            "ordinary reads query overflow"
        ),
        "prediction": (
            "route/final uncommitted SIGKILL must expose the exact pre-operation state; "
            "post-commit SIGKILL must expose the exact control post-operation state. No case "
            "may lose or duplicate membership. Reopening SQLite should require zero "
            "application-level repair; ordinary successful reads should inspect one singleton "
            "metadata row and never query overflow; each mutation should scan at most eight "
            "source slots and bounded primary placement work should stay below 1808 modeled "
            "operations for migration budget eight"
        ),
        "result": (
            "survives only if the fixed crash matrix, common-path envelope, and explicit "
            "overflow envelope all satisfy those invariants. Even if it survives, this does "
            "not prove physical direct-address page locality because the persistent primary "
            "tables themselves are SQLite B-trees, and overflow B-tree insertion/page-write "
            "amplification is not directly measured by Python sqlite3"
        ),
        "revision": (
            "if crash atomicity survives, next isolate the remaining storage-engine locality "
            "and write-amplification questions: direct-address primary persistence, WAL/page "
            "write accounting, and whether an overflow-presence discriminator can remain "
            "bounded without becoming another global comparison index"
        ),
        "measurement_scope": (
            "logical primary bucket/stash page probes, bounded placement/migration work, exact "
            "SQLite logical state after real SIGKILL, and dbstat overflow B-tree geometry. "
            "No physical OS/device I/O, hardware power-loss beyond the tested SQLite stack, "
            "multi-writer, distributed consistency, or production latency claim"
        ),
    }
    RESULTS_PATH.write_text(json.dumps(out, indent=2))
    print("DURABLE_HYBRID_RESULTS_JSON")
    print(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    run()
