from __future__ import annotations

import json
from pathlib import Path

import run_recovery_experiment as experiment

ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "recovery_results.json"


def require_equal(label: str, expected, observed) -> None:
    if expected != observed:
        raise AssertionError(f"{label}: expected {expected!r}, observed {observed!r}")


def row_key(row: dict, *, locality: bool = False):
    if locality:
        return (row["entity_count"], row["operation"], row["crash_phase"])
    return (row["operation"], row["crash_phase"])


def compare_rows(recorded_rows: list[dict], observed_rows: list[dict], *, locality: bool = False) -> None:
    observed_by_key = {row_key(row, locality=locality): row for row in observed_rows}
    for expected in recorded_rows:
        key = row_key(expected, locality=locality)
        if key not in observed_by_key:
            raise AssertionError(f"missing observed recovery row: {key}")
        observed = observed_by_key[key]
        for field, expected_value in expected.items():
            require_equal(f"{key}.{field}", expected_value, observed[field])

        for boolean_field in (
            "read_blocked_before_recovery",
            "materialization_equal",
            "semantic_check",
            "all_derived_fresh",
            "journal_empty",
        ):
            require_equal(f"{key}.{boolean_field}", True, observed[boolean_field])


def main() -> None:
    recorded_text = RESULTS_PATH.read_text()
    recorded = json.loads(recorded_text)
    observed = experiment.run()

    require_equal("experiment", recorded["experiment"], observed["experiment"])
    require_equal(
        "phase_matrix_entity_count",
        recorded["phase_matrix_entity_count"],
        observed["phase_matrix_entity_count"],
    )
    require_equal("operations", recorded["operations"], observed["operations"])
    require_equal("crash_phases", recorded["crash_phases"], observed["crash_phases"])
    compare_rows(recorded["phase_rows"], observed["phase_rows"])
    compare_rows(recorded["locality_rows"], observed["locality_rows"], locality=True)

    # run() writes a verbose generated object; restore the compact recorded ledger.
    RESULTS_PATH.write_text(recorded_text)
    print("RECORDED_RECOVERY_RESULTS_MATCH_EXECUTABLE_EXPERIMENT")


if __name__ == "__main__":
    main()
