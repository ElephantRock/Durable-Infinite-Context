from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import run_topology_experiment as experiment


ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "topology_results.json"


def require_equal(label: str, expected: Any, observed: Any) -> None:
    if expected != observed:
        raise AssertionError(f"{label}: expected {expected!r}, observed {observed!r}")


def compact_case(case: dict[str, Any], *, control: bool) -> dict[str, Any]:
    out = {
        "store": case["store"],
        "entity_count": case["entity_count"],
        "moved_index": case["moved_index"],
        "target_index": case["target_index"],
        "admission_read_keys": case["admission_read_keys"],
        "promotion_read_keys": case["promotion_read_keys"],
        "read_keys_changed": case["read_keys_changed"],
        "stale_read_blocked": case["stale_read_blocked"],
        "stale_read_admitted": case["stale_read_admitted"],
        "unrelated_read_admitted": case["unrelated_read_admitted"],
        "semantic_check": case["semantic_check"],
        "materialization_equal": case["materialization_equal"],
        "first_recovery_work": case["first_recovery_work"],
        "second_recovery_work": case["second_recovery_work"],
        "total_recovery_work": case["total_recovery_work"],
        "full_rebuild_work": case["full_rebuild_work"],
    }
    if control:
        out["stale_value_visible"] = case["stale_value_visible"]
        # Keep the committed key order stable.
        ordered = {
            key: out[key]
            for key in (
                "store",
                "entity_count",
                "moved_index",
                "target_index",
                "admission_read_keys",
                "promotion_read_keys",
                "read_keys_changed",
                "stale_read_blocked",
                "stale_read_admitted",
                "stale_value_visible",
                "unrelated_read_admitted",
                "semantic_check",
                "materialization_equal",
                "first_recovery_work",
                "second_recovery_work",
                "total_recovery_work",
                "full_rebuild_work",
            )
        }
        return ordered
    out["revalidation_lookup_uses_index"] = case["revalidation_lookup_uses_index"]
    return {
        key: out[key]
        for key in (
            "store",
            "entity_count",
            "moved_index",
            "target_index",
            "admission_read_keys",
            "promotion_read_keys",
            "read_keys_changed",
            "stale_read_blocked",
            "stale_read_admitted",
            "unrelated_read_admitted",
            "semantic_check",
            "materialization_equal",
            "revalidation_lookup_uses_index",
            "first_recovery_work",
            "second_recovery_work",
            "total_recovery_work",
            "full_rebuild_work",
        )
    }


def compact(observed: dict[str, Any]) -> dict[str, Any]:
    return {
        "experiment": observed["experiment"],
        "control": compact_case(observed["control"], control=True),
        "revalidated": compact_case(observed["revalidated"], control=False),
        "locality_cardinalities": observed["locality_cardinalities"],
        "locality_rows": [
            [
                row["entity_count"],
                row["first_recovery_work"],
                row["second_recovery_work"],
                row["total_recovery_work"],
                row["full_rebuild_work"],
            ]
            for row in observed["locality_rows"]
        ],
        "required_true": [
            "control.stale_read_admitted",
            "control.stale_value_visible",
            "control.unrelated_read_admitted",
            "control.semantic_check",
            "control.materialization_equal",
            "revalidated.read_keys_changed",
            "revalidated.stale_read_blocked",
            "revalidated.unrelated_read_admitted",
            "revalidated.semantic_check",
            "revalidated.materialization_equal",
            "revalidated.revalidation_lookup_uses_index",
            "locality.stale_read_blocked",
            "locality.unrelated_read_admitted",
            "locality.semantic_check",
            "locality.materialization_equal",
            "locality.revalidation_lookup_uses_index",
        ],
        "invariant": observed["invariant"],
        "mechanism": observed["mechanism"],
        "scope": observed["scope"],
    }


def require_safety(observed: dict[str, Any]) -> None:
    control = observed["control"]
    require_equal("control.stale_read_admitted", True, control["stale_read_admitted"])
    require_equal("control.stale_value_visible", True, control["stale_value_visible"])
    require_equal("control.semantic_check", True, control["semantic_check"])
    require_equal("control.materialization_equal", True, control["materialization_equal"])

    fixed = observed["revalidated"]
    require_equal("revalidated.read_keys_changed", True, fixed["read_keys_changed"])
    require_equal("revalidated.stale_read_blocked", True, fixed["stale_read_blocked"])
    require_equal("revalidated.stale_read_admitted", False, fixed["stale_read_admitted"])
    require_equal("revalidated.unrelated_read_admitted", True, fixed["unrelated_read_admitted"])
    require_equal("revalidated.semantic_check", True, fixed["semantic_check"])
    require_equal("revalidated.materialization_equal", True, fixed["materialization_equal"])
    require_equal(
        "revalidated.revalidation_lookup_uses_index",
        True,
        fixed["revalidation_lookup_uses_index"],
    )

    work = set()
    for row in observed["locality_rows"]:
        n = row["entity_count"]
        work.add(row["total_recovery_work"])
        for field in (
            "stale_read_blocked",
            "unrelated_read_admitted",
            "semantic_check",
            "materialization_equal",
            "revalidation_lookup_uses_index",
        ):
            require_equal(f"locality[{n}].{field}", True, row[field])
    require_equal("fixed-intent recovery work cardinality", 1, len(work))


def main() -> None:
    recorded_text = RESULTS_PATH.read_text()
    recorded = json.loads(recorded_text)
    try:
        observed = experiment.run()
        require_safety(observed)
        require_equal("compact committed evidence", recorded, compact(observed))
        print("RECORDED_TOPOLOGY_RESULTS_MATCH_EXECUTABLE_EXPERIMENT")
    finally:
        RESULTS_PATH.write_text(recorded_text)


if __name__ == "__main__":
    main()
