from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import run_subject_fanout_experiment as experiment


ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "subject_fanout_results.json"


def require_equal(label: str, expected: Any, observed: Any) -> None:
    if expected != observed:
        raise AssertionError(f"{label}: expected {expected!r}, observed {observed!r}")


def require_safety(observed: dict[str, Any]) -> None:
    control = observed["discriminating_control"]
    fixed = observed["discriminating_fixed"]
    require_equal("control.materialization_equal", True, control["materialization_equal"])
    require_equal("control.all_derived_fresh", True, control["all_derived_fresh"])
    require_equal("fixed.materialization_equal", True, fixed["materialization_equal"])
    require_equal("fixed.all_derived_fresh", True, fixed["all_derived_fresh"])
    require_equal("fixed.head_index_equal", True, fixed["head_index_equal"])
    require_equal("fixed.head_lookup_uses_index", True, fixed["head_lookup_uses_index"])
    require_equal("fixed.head_refresh_uses_index", True, fixed["head_refresh_uses_index"])
    if fixed["total_recovery_work"] >= control["total_recovery_work"]:
        raise AssertionError("v0.14 does not beat the deep-history v0.13 control")

    history = observed["history_rows"]
    require_equal("history row count", len(observed["history_depths"]), len(history))
    control_work = [row["control_work"] for row in history]
    if any(b <= a for a, b in zip(control_work, control_work[1:])):
        raise AssertionError(f"v0.13 control work is not increasing with H: {control_work}")
    require_equal("fixed H work cardinality", 1, len({row["fixed_work"] for row in history}))
    require_equal(
        "fixed H canonical-read cardinality",
        1,
        len({row["fixed_canonical_rows_read"] for row in history}),
    )

    predicates = observed["predicate_rows"]
    require_equal("predicate row count", len(observed["predicate_counts"]), len(predicates))
    p_work = [row["total_work"] for row in predicates]
    if any(b <= a for a, b in zip(p_work, p_work[1:])):
        raise AssertionError(f"true live-predicate work is not increasing with P: {p_work}")
    for row in predicates:
        require_equal(
            f"P={row['predicate_count']}.head_rows_read",
            row["predicate_count"],
            row["head_rows_read"],
        )

    global_rows = observed["global_rows"]
    require_equal("global row count", len(observed["global_cardinalities"]), len(global_rows))
    require_equal("global-N work cardinality", 1, len({row["total_work"] for row in global_rows}))

    fallback = observed["head_fallback"]
    require_equal(
        "fallback deadline after move",
        fallback["fallback_assertion_id"],
        fallback["after_move"].get("deadline"),
    )
    if "renamed_deadline" not in fallback["after_move"]:
        raise AssertionError("predicate move did not create renamed head")
    if "renamed_deadline" in fallback["after_delete"]:
        raise AssertionError("predicate delete left stale renamed head")
    require_equal("move materialization parity", True, fallback["move_materialization_equal"])
    require_equal("delete materialization parity", True, fallback["delete_materialization_equal"])
    require_equal("move head parity", True, fallback["move_head_index_equal"])
    require_equal("delete head parity", True, fallback["delete_head_index_equal"])
    require_equal("head lookup indexed", True, fallback["head_lookup_uses_index"])
    require_equal("head refresh indexed", True, fallback["head_refresh_uses_index"])


def main() -> None:
    recorded_text = RESULTS_PATH.read_text()
    recorded = json.loads(recorded_text)
    try:
        observed = experiment.run()
        require_safety(observed)
        require_equal("committed v0.14 evidence", recorded, observed)
        print("RECORDED_SUBJECT_FANOUT_RESULTS_MATCH_EXECUTABLE_EXPERIMENT")
    finally:
        RESULTS_PATH.write_text(recorded_text)


if __name__ == "__main__":
    main()
