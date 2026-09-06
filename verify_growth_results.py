from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import run_growth_experiment as experiment


ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "growth_results.json"


def require_equal(label: str, expected: Any, observed: Any) -> None:
    if expected != observed:
        raise AssertionError(f"{label}: expected {expected!r}, observed {observed!r}")


def require_safety(observed: dict[str, Any]) -> None:
    control = observed["control"]
    require_equal("control.canonical_moved", True, control["canonical_moved"])
    require_equal("control.target_context_present", False, control["target_context_present"])
    require_equal("control.target_derived_count", 0, control["target_derived_count"])
    require_equal("control.old_subject_retired", True, control["old_subject_retired"])
    require_equal("control.materialization_equal", False, control["materialization_equal"])
    require_equal("control.all_derived_fresh", True, control["all_derived_fresh"])

    fixed = observed["created"]
    require_equal("created.canonical_moved", True, fixed["canonical_moved"])
    require_equal("created.target_context_present", True, fixed["target_context_present"])
    require_equal("created.target_derived_count", 4, fixed["target_derived_count"])
    require_equal("created.old_subject_retired", True, fixed["old_subject_retired"])
    require_equal("created.materialization_equal", True, fixed["materialization_equal"])
    require_equal("created.all_derived_fresh", True, fixed["all_derived_fresh"])

    work = set()
    for row in observed["locality_rows"]:
        n = row["entity_count"]
        work.add(row["recovery_work"])
        require_equal(f"locality[{n}].canonical_moved", True, row["canonical_moved"])
        require_equal(
            f"locality[{n}].target_context_present",
            True,
            row["target_context_present"],
        )
        require_equal(f"locality[{n}].target_derived_count", 4, row["target_derived_count"])
        require_equal(f"locality[{n}].old_subject_retired", True, row["old_subject_retired"])
        require_equal(f"locality[{n}].materialization_equal", True, row["materialization_equal"])
        require_equal(f"locality[{n}].all_derived_fresh", True, row["all_derived_fresh"])
        if row["recovery_work"] >= row["full_rebuild_work"]:
            raise AssertionError(
                f"locality[{n}] local recovery not cheaper than rebuild: "
                f"{row['recovery_work']} >= {row['full_rebuild_work']}"
            )
    require_equal("fixed growth recovery work cardinality", 1, len(work))


def main() -> None:
    recorded_text = RESULTS_PATH.read_text()
    recorded = json.loads(recorded_text)
    try:
        observed = experiment.run()
        require_safety(observed)
        require_equal("committed growth evidence", recorded, observed)
        print("RECORDED_GROWTH_RESULTS_MATCH_EXECUTABLE_EXPERIMENT")
    finally:
        RESULTS_PATH.write_text(recorded_text)


if __name__ == "__main__":
    main()
