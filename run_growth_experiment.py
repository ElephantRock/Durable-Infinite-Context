from __future__ import annotations

import json
from pathlib import Path

from simulator.growth import (
    run_growth_locality_case,
    run_v011_growth_control,
    run_v012_growth_creation_case,
)


ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "growth_results.json"


def run() -> dict:
    control = run_v011_growth_control(entity_count=64, moved_index=40)
    print(
        "GROWTH_V011_CONTROL",
        {
            "canonical_moved": control["canonical_moved"],
            "target_context_present": control["target_context_present"],
            "target_derived_count": control["target_derived_count"],
            "materialization_equal": control["materialization_equal"],
        },
    )

    fixed = run_v012_growth_creation_case(entity_count=64, moved_index=40)
    print(
        "GROWTH_V012_CREATED",
        {
            "canonical_moved": fixed["canonical_moved"],
            "target_context_present": fixed["target_context_present"],
            "target_derived_count": fixed["target_derived_count"],
            "materialization_equal": fixed["materialization_equal"],
            "recovery_work": fixed["recovery_work"],
        },
    )

    locality_rows = []
    for entity_count in (100, 1_000, 10_000, 50_000):
        row = run_growth_locality_case(entity_count)
        locality_rows.append(row)
        print(
            "GROWTH_SCALE",
            entity_count,
            {
                "recovery_work": row["recovery_work"],
                "full_rebuild": row["full_rebuild_work"],
                "target_derived_count": row["target_derived_count"],
            },
        )

    if not control["canonical_moved"]:
        raise AssertionError("v0.11 growth control did not move canonical assertion")
    if control["target_context_present"] or control["target_derived_count"] != 0:
        raise AssertionError("v0.11 growth control unexpectedly synthesized target materialization")
    if control["materialization_equal"]:
        raise AssertionError("v0.11 growth control did not reproduce rebuild-parity failure")
    if not control["all_derived_fresh"]:
        raise AssertionError("control failure must be missing topology, not stale lifecycle")

    if not fixed["canonical_moved"]:
        raise AssertionError("v0.12 canonical topology move failed")
    if not fixed["target_context_present"] or fixed["target_derived_count"] != 4:
        raise AssertionError("v0.12 did not synthesize complete target materialization")
    if not fixed["old_subject_retired"]:
        raise AssertionError("v0.12 failed to retire old subject materialization")
    if not fixed["materialization_equal"] or not fixed["all_derived_fresh"]:
        raise AssertionError("v0.12 did not converge to clean reconstruction")

    work = {row["recovery_work"] for row in locality_rows}
    if len(work) != 1:
        raise AssertionError(
            f"fixed topology-growth work grew with unrelated cardinality: {work}"
        )
    for row in locality_rows:
        if not (
            row["canonical_moved"]
            and row["target_context_present"]
            and row["target_derived_count"] == 4
            and row["old_subject_retired"]
            and row["materialization_equal"]
            and row["all_derived_fresh"]
        ):
            raise AssertionError(f"v0.12 growth safety failure at N={row['entity_count']}")

    out = {
        "experiment": "v0.12_local_topology_growth_materialization",
        "control": control,
        "created": fixed,
        "locality_cardinalities": [100, 1_000, 10_000, 50_000],
        "locality_rows": locality_rows,
        "invariant": (
            "canonical topology growth must create the missing derived outputs required by the new "
            "canonical key rather than restricting repair to already-materialized nodes"
        ),
        "mechanism": (
            "changed-key assertion upserts derive a bounded set of missing profile/state/support/context "
            "obligations, persist them as invalid placeholders inside local invalidation, then reuse the "
            "existing topological repair path"
        ),
        "scope": (
            "single SQLite database; one assertion move to a brand-new subject; deadline predicate; "
            "synthetic oracle assertions; no arbitrary new-predicate or distributed claim"
        ),
    }
    RESULTS_PATH.write_text(json.dumps(out, indent=2))
    print("GROWTH_RESULTS_JSON")
    print(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    run()
