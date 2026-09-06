from __future__ import annotations

import json
from pathlib import Path

from simulator.multi_intent import (
    run_concurrent_admission_case,
    run_multi_intent_locality_case,
    run_overlapping_derived_case,
    run_phase_aware_read_case,
    run_same_key_conflict_case,
)


ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "multi_intent_results.json"


def run() -> dict:
    concurrent = run_concurrent_admission_case(entity_count=128, writer_count=8)
    print(
        "MULTI_CONCURRENT",
        {
            "writers": concurrent["writer_count"],
            "admitted": concurrent["admitted"],
            "unique_sequences": concurrent["unique_sequences"],
            "done": concurrent["queue_after"]["done"],
            "conflicts": concurrent["queue_after"]["conflict"],
        },
    )

    conflict = run_same_key_conflict_case(entity_count=64, index=7)
    print(
        "MULTI_CONFLICT",
        {
            "statuses": conflict["statuses"],
            "base_versions": [
                conflict["first_base_version"],
                conflict["second_base_version"],
                conflict["retry_base_version"],
            ],
            "final_value": conflict["final_value"],
        },
    )

    phase_reads = run_phase_aware_read_case(
        entity_count=64,
        active_index=5,
        queued_index=17,
    )
    print(
        "MULTI_READS",
        {
            "phase": phase_reads["durable_phase"],
            "affected_blocked": phase_reads["affected_read_blocked"],
            "queued_admitted": phase_reads["queued_read_admitted"],
            "unrelated_admitted": phase_reads["unrelated_read_admitted"],
            "done": phase_reads["queue_final"]["done"],
        },
    )

    overlap = run_overlapping_derived_case(entity_count=64, index=9)
    print(
        "MULTI_OVERLAP",
        {
            "distinct_write_keys": overlap["distinct_write_keys"],
            "shared_read_keys": overlap["shared_read_keys"],
            "done": overlap["queue_final"]["done"],
        },
    )

    locality_rows = []
    for entity_count in (100, 1_000, 10_000, 50_000):
        row = run_multi_intent_locality_case(entity_count)
        locality_rows.append(row)
        print(
            "MULTI_SCALE",
            entity_count,
            {
                "base_recovery_work": row["base_recovery_work"],
                "queue_work": row["queue_logical_work"],
                "total_work": row["total_logical_work"],
                "full_rebuild": row["full_rebuild_work"],
            },
        )

    total_work = {row["total_logical_work"] for row in locality_rows}
    if len(total_work) != 1:
        raise AssertionError(
            f"fixed three-intent recovery work grew with unrelated cardinality: {total_work}"
        )

    if concurrent["admitted"] != concurrent["writer_count"]:
        raise AssertionError("not all concurrent writers durably admitted an intent")
    if concurrent["queue_after"]["conflict"] != 0:
        raise AssertionError("independent concurrent writers conflicted")
    if conflict["statuses"] != ["done", "conflict", "done"]:
        raise AssertionError("same-key stale-base conflict was not explicit")
    if conflict["final_value"] != 73:
        raise AssertionError("same-key retry did not converge to the fresh version")
    if not (
        phase_reads["affected_read_blocked"]
        and phase_reads["queued_read_admitted"]
        and phase_reads["unrelated_read_admitted"]
    ):
        raise AssertionError("phase-aware read admission violated locality")
    if not overlap["distinct_write_keys"] or not overlap["shared_read_keys"]:
        raise AssertionError("overlap control did not exercise intended topology")

    required_true = (
        concurrent["semantic_check"],
        concurrent["materialization_equal"],
        concurrent["queue_lookup_uses_index"],
        conflict["semantic_check"],
        conflict["materialization_equal"],
        phase_reads["semantic_check"],
        phase_reads["materialization_equal"],
        overlap["semantic_check"],
        overlap["materialization_equal"],
        *[row["semantic_check"] for row in locality_rows],
        *[row["materialization_equal"] for row in locality_rows],
        *[row["queue_lookup_uses_index"] for row in locality_rows],
        *[row["affected_traversal_uses_index"] for row in locality_rows],
    )
    if not all(required_true):
        raise AssertionError("v0.10 safety/correctness condition failed")

    out = {
        "experiment": "v0.10_durable_multi_intent_concurrency",
        "storage": {
            "engine": "sqlite3",
            "journal_mode": "wal",
            "synchronous": "FULL",
            "physical_writer_model": "sqlite_single_writer",
            "logical_intents": "durable_ordered_queue",
            "process_failure": "SIGKILL",
        },
        "concurrent_admission": concurrent,
        "same_key_conflict": conflict,
        "phase_aware_reads": phase_reads,
        "overlapping_derived": overlap,
        "locality_cardinalities": [100, 1_000, 10_000, 50_000],
        "locality_rows": locality_rows,
        "invariant": (
            "ordered durable admission plus optimistic canonical versions plus the v0.9 "
            "single-flight recovery engine must prevent lost updates, recover multiple intents "
            "after process death, and block only derived reads that can actually be stale"
        ),
        "scope": (
            "single SQLite database; concurrent OS-process intent admission; SQLite-serialized "
            "physical writes; synthetic oracle assertions; no distributed or multi-database claim"
        ),
    }
    RESULTS_PATH.write_text(json.dumps(out, indent=2))
    print("MULTI_INTENT_RESULTS_JSON")
    print(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    run()
