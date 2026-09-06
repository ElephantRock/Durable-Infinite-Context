from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import run_compositional_profile_experiment as experiment


ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "compositional_profile_results.json"


def require_equal(label: str, expected: Any, observed: Any) -> None:
    if expected != observed:
        raise AssertionError(f"{label}: expected {expected!r}, observed {observed!r}")


def require_strictly_increasing(label: str, values: list[int]) -> None:
    if any(b <= a for a, b in zip(values, values[1:])):
        raise AssertionError(f"{label} is not strictly increasing: {values}")


def require_safety(observed: dict[str, Any]) -> None:
    control = observed["discriminating_control"]
    fixed = observed["discriminating_fixed"]

    require_equal("control.materialization_equal", True, control["materialization_equal"])
    require_equal("control.all_derived_fresh", True, control["all_derived_fresh"])
    require_equal("control.head_index_equal", True, control["head_index_equal"])
    require_equal("fixed.materialization_equal", True, fixed["materialization_equal"])
    require_equal("fixed.all_derived_fresh", True, fixed["all_derived_fresh"])
    require_equal("fixed.head_index_equal", True, fixed["head_index_equal"])
    require_equal("fixed.full_assembly_equal", True, fixed["full_assembly_equal"])
    require_equal("fixed.partial_assembly_equal", True, fixed["partial_assembly_equal"])
    require_equal("fixed.profile_has_embedded_evidence", False, fixed["profile_has_embedded_evidence"])
    require_equal("full logical profile parity", control["persisted_profile"], fixed["full_profile"])
    if fixed["recovery"]["total_work"] >= control["recovery"]["total_work"]:
        raise AssertionError("v0.15 did not beat the P=32,K=1 v0.14 monolithic control")

    predicate_rows = observed["predicate_rows"]
    require_equal("predicate row count", len(observed["predicate_counts"]), len(predicate_rows))
    require_strictly_increasing(
        "v0.14 monolithic maintenance work",
        [row["monolithic_work"] for row in predicate_rows],
    )
    require_equal(
        "K=1 compositional maintenance work cardinality",
        1,
        len({row["compositional_work"] for row in predicate_rows}),
    )
    require_equal(
        "K=1 partial logical assembly work cardinality",
        1,
        len({row["partial_assembly_work"] for row in predicate_rows}),
    )
    require_strictly_increasing(
        "full assembly work",
        [row["full_assembly_work"] for row in predicate_rows],
    )
    require_strictly_increasing(
        "manifest serialized size",
        [row["manifest_storage_bytes"] for row in predicate_rows],
    )
    for row in predicate_rows:
        if row["predicate_count"] > 1 and row["manifest_storage_bytes"] >= row["monolithic_profile_storage_bytes"]:
            raise AssertionError("manifest did not reduce persisted profile payload size")

    changed_rows = observed["changed_rows"]
    require_equal("changed row count", len(observed["changed_counts"]), len(changed_rows))
    require_strictly_increasing(
        "compositional maintenance work by K",
        [row["compositional_work"] for row in changed_rows],
    )
    require_strictly_increasing(
        "partial logical assembly work by K",
        [row["partial_assembly_work"] for row in changed_rows],
    )
    require_equal(
        "full assembly work cardinality at fixed P",
        1,
        len({row["full_assembly_work"] for row in changed_rows}),
    )

    history_rows = observed["history_rows"]
    require_equal("history row count", len(observed["history_depths"]), len(history_rows))
    require_equal(
        "maintenance H work cardinality",
        1,
        len({row["maintenance_work"] for row in history_rows}),
    )
    require_equal(
        "partial assembly H work cardinality",
        1,
        len({row["partial_assembly_work"] for row in history_rows}),
    )

    global_rows = observed["global_rows"]
    require_equal("global row count", len(observed["global_cardinalities"]), len(global_rows))
    require_equal(
        "maintenance global-N work cardinality",
        1,
        len({row["maintenance_work"] for row in global_rows}),
    )
    require_equal(
        "partial assembly global-N work cardinality",
        1,
        len({row["partial_assembly_work"] for row in global_rows}),
    )

    topology = observed["manifest_topology"]
    require_equal("topology before", ["deadline", "facet_001"], topology["before_predicates"])
    require_equal(
        "topology after add",
        ["deadline", "facet_001", "facet_added"],
        topology["after_add_predicates"],
    )
    require_equal("topology after delete", ["deadline", "facet_001"], topology["after_delete_predicates"])
    require_equal("topology add parity", True, topology["add_materialization_equal"])
    require_equal("topology delete parity", True, topology["delete_materialization_equal"])
    require_equal("topology head parity", True, topology["head_index_equal"])
    require_equal("topology freshness", True, topology["all_derived_fresh"])

    read_safety = observed["read_safety"]
    require_equal("unrelated partial remains readable", True, read_safety["unrelated_partial_present"])
    require_equal("affected partial blocked", True, read_safety["affected_partial_blocked"])
    require_equal("full profile blocked", True, read_safety["full_profile_blocked"])
    require_equal("post-recovery full parity", True, read_safety["final_full_equal"])
    require_equal("post-recovery materialization parity", True, read_safety["materialization_equal"])


def main() -> None:
    recorded_text = RESULTS_PATH.read_text()
    recorded = json.loads(recorded_text)
    try:
        observed = experiment.run()
        require_safety(observed)
        require_equal("committed v0.15 evidence", recorded, observed)
        print("RECORDED_COMPOSITIONAL_PROFILE_RESULTS_MATCH_EXECUTABLE_EXPERIMENT")
    finally:
        RESULTS_PATH.write_text(recorded_text)


if __name__ == "__main__":
    main()
