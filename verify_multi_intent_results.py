from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import run_multi_intent_experiment as experiment

ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "multi_intent_results.json"


def require_equal(label: str, expected: Any, observed: Any) -> None:
    if expected != observed:
        raise AssertionError(f"{label}: expected {expected!r}, observed {observed!r}")


def queue_vector(counts: dict[str, int]) -> list[int]:
    return [
        int(counts["queued"]),
        int(counts["active"]),
        int(counts["done"]),
        int(counts["conflict"]),
    ]


def compact(observed: dict[str, Any]) -> dict[str, Any]:
    concurrent = observed["concurrent_admission"]
    conflict = observed["same_key_conflict"]
    phase_reads = observed["phase_aware_reads"]
    overlap = observed["overlapping_derived"]

    return {
        "experiment": observed["experiment"],
        "storage": observed["storage"],
        "concurrent_admission": {
            "entity_count": concurrent["entity_count"],
            "writer_count": concurrent["writer_count"],
            "admitted": concurrent["admitted"],
            "unique_sequences": concurrent["unique_sequences"],
            "sequence_min": concurrent["sequence_min"],
            "sequence_max": concurrent["sequence_max"],
            "queue_before": queue_vector(concurrent["queue_before"]),
            "queue_after": queue_vector(concurrent["queue_after"]),
            "promotions": concurrent["drain"]["promotions"],
            "conflicts": concurrent["drain"]["conflicts"],
            "recovery_rounds": concurrent["drain"]["recovery_rounds"],
            "logical_work": concurrent["drain"]["trace"]["logical_work"],
        },
        "same_key_conflict": {
            "entity_count": conflict["entity_count"],
            "index": conflict["index"],
            "first_base_version": conflict["first_base_version"],
            "second_base_version": conflict["second_base_version"],
            "retry_base_version": conflict["retry_base_version"],
            "statuses": conflict["statuses"],
            "final_value": conflict["final_value"],
            "first_drain_logical_work": conflict["first_drain"]["trace"]["logical_work"],
            "second_drain_logical_work": conflict["second_drain"]["trace"]["logical_work"],
        },
        "phase_aware_reads": {
            "entity_count": phase_reads["entity_count"],
            "durable_phase": phase_reads["durable_phase"],
            "affected_read_blocked": phase_reads["affected_read_blocked"],
            "queued_read_admitted": phase_reads["queued_read_admitted"],
            "unrelated_read_admitted": phase_reads["unrelated_read_admitted"],
            "queue_after_crash": queue_vector(phase_reads["queue_after_crash"]),
            "queue_final": queue_vector(phase_reads["queue_final"]),
            "recovery_rounds": phase_reads["drain"]["recovery_rounds"],
            "logical_work": phase_reads["drain"]["trace"]["logical_work"],
            "process_failure": phase_reads["process_failure"],
        },
        "overlapping_derived": {
            "entity_count": overlap["entity_count"],
            "index": overlap["index"],
            "distinct_write_keys": overlap["distinct_write_keys"],
            "shared_read_keys": overlap["shared_read_keys"],
            "queue_final": queue_vector(overlap["queue_final"]),
            "recovery_rounds": overlap["drain"]["recovery_rounds"],
            "logical_work": overlap["drain"]["trace"]["logical_work"],
        },
        "locality_cardinalities": observed["locality_cardinalities"],
        "locality_rows": [
            [
                row["entity_count"],
                row["intent_count"],
                row["base_recovery_work"],
                row["queue_logical_work"],
                row["total_logical_work"],
                row["full_rebuild_work"],
            ]
            for row in observed["locality_rows"]
        ],
        "required_true": [
            "concurrent_admission.semantic_check",
            "concurrent_admission.materialization_equal",
            "concurrent_admission.queue_lookup_uses_index",
            "same_key_conflict.semantic_check",
            "same_key_conflict.materialization_equal",
            "phase_aware_reads.semantic_check",
            "phase_aware_reads.materialization_equal",
            "overlapping_derived.semantic_check",
            "overlapping_derived.materialization_equal",
            "locality.semantic_check",
            "locality.materialization_equal",
            "locality.queue_lookup_uses_index",
            "locality.affected_traversal_uses_index",
        ],
        "invariant": observed["invariant"],
        "scope": observed["scope"],
    }


def require_safety(observed: dict[str, Any]) -> None:
    for section in (
        "concurrent_admission",
        "same_key_conflict",
        "phase_aware_reads",
        "overlapping_derived",
    ):
        require_equal(f"{section}.semantic_check", True, observed[section]["semantic_check"])
        require_equal(
            f"{section}.materialization_equal",
            True,
            observed[section]["materialization_equal"],
        )
    require_equal(
        "concurrent_admission.queue_lookup_uses_index",
        True,
        observed["concurrent_admission"]["queue_lookup_uses_index"],
    )
    for row in observed["locality_rows"]:
        n = row["entity_count"]
        for field in (
            "semantic_check",
            "materialization_equal",
            "queue_lookup_uses_index",
            "affected_traversal_uses_index",
        ):
            require_equal(f"locality[{n}].{field}", True, row[field])


def main() -> None:
    recorded_text = RESULTS_PATH.read_text()
    recorded = json.loads(recorded_text)
    try:
        observed = experiment.run()
        require_safety(observed)
        require_equal("compact committed evidence", recorded, compact(observed))
        print("RECORDED_MULTI_INTENT_RESULTS_MATCH_EXECUTABLE_EXPERIMENT")
    finally:
        # The experiment writes a verbose raw object to this path. Restore the
        # compact committed evidence ledger even when a comparison fails.
        RESULTS_PATH.write_text(recorded_text)


if __name__ == "__main__":
    main()
