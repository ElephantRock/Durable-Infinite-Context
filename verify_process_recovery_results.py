from __future__ import annotations

import json
from pathlib import Path

import run_process_recovery_experiment as experiment

ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "process_recovery_results.json"


def require_equal(label: str, expected, observed) -> None:
    if expected != observed:
        raise AssertionError(f"{label}: expected {expected!r}, observed {observed!r}")


def phase_key(row: dict) -> tuple[str, str]:
    return row["operation"], row["failpoint"]


def locality_key(row: dict) -> tuple[int, str, str]:
    return row["entity_count"], row["operation"], row["failpoint"]


def compact_phase(row: dict) -> list:
    return [
        row["operation"],
        row["failpoint"],
        row["durable_phase_after_crash"],
        row["canonical_visible_after_crash"],
        row["journal_rows_after_crash"],
        row["invalid_nodes_after_crash"],
        row["rebuilding_nodes_after_crash"],
        row["read_blocked_before_recovery"],
        row["recovery_trace"]["canonical_mutations"],
        row["recovery_work"],
        row["full_rebuild_work"],
    ]


def compact_locality(row: dict) -> list:
    return [
        row["entity_count"],
        row["recovery_work"],
        row["recovery_trace"]["reinvalidated_nodes"],
        row["recovery_trace"]["retired_nodes"],
        row["full_rebuild_work"],
    ]


def main() -> None:
    recorded_text = RESULTS_PATH.read_text()
    recorded = json.loads(recorded_text)
    observed = experiment.run()

    try:
        require_equal("experiment", recorded["experiment"], observed["experiment"])
        require_equal("storage", recorded["storage"], observed["storage"])
        require_equal(
            "phase_matrix_entity_count",
            recorded["phase_matrix_entity_count"],
            observed["phase_matrix_entity_count"],
        )
        require_equal("operations", recorded["operations"], observed["operations"])
        require_equal("failpoints", recorded["failpoints"], observed["failpoints"])
        require_equal(
            "locality_cardinalities",
            recorded["locality_cardinalities"],
            observed["locality_cardinalities"],
        )
        require_equal("invariant", recorded["invariant"], observed["invariant"])
        require_equal("scope", recorded["scope"], observed["scope"])

        observed_phase = observed["phase_rows"]
        expected_phase = recorded["phase_rows"]
        require_equal("phase row count", len(expected_phase), len(observed_phase))
        phase_keys = [phase_key(row) for row in observed_phase]
        require_equal("unique observed phase keys", len(phase_keys), len(set(phase_keys)))
        expected_phase_by_key = {(row[0], row[1]): row for row in expected_phase}
        require_equal("unique recorded phase keys", len(expected_phase), len(expected_phase_by_key))
        for row in observed_phase:
            key = phase_key(row)
            if key not in expected_phase_by_key:
                raise AssertionError(f"unexpected observed phase row: {key}")
            require_equal(f"phase {key}", expected_phase_by_key[key], compact_phase(row))
            for field in recorded["required_true"]:
                require_equal(f"phase {key}.{field}", True, row[field])

        observed_locality = observed["locality_rows"]
        expected_locality = recorded["locality_rows"]
        require_equal("locality row count", len(expected_locality), len(observed_locality))
        locality_keys = [locality_key(row) for row in observed_locality]
        require_equal("unique observed locality keys", len(locality_keys), len(set(locality_keys)))
        expected_locality_by_n = {row[0]: row for row in expected_locality}
        require_equal(
            "unique recorded locality cardinalities",
            len(expected_locality),
            len(expected_locality_by_n),
        )
        for row in observed_locality:
            n = row["entity_count"]
            if n not in expected_locality_by_n:
                raise AssertionError(f"unexpected locality cardinality: {n}")
            require_equal(f"locality {n}", expected_locality_by_n[n], compact_locality(row))
            for field in recorded["required_true"]:
                require_equal(f"locality {n}.{field}", True, row[field])

        print("RECORDED_PROCESS_RECOVERY_RESULTS_MATCH_EXECUTABLE_EXPERIMENT")
    finally:
        # The executable experiment writes a verbose raw object to this path.
        # Restore the compact committed evidence ledger even if comparison fails.
        RESULTS_PATH.write_text(recorded_text)


if __name__ == "__main__":
    main()
