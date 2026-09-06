from __future__ import annotations

import json
from pathlib import Path

from simulator.topology import (
    run_topology_locality_case,
    run_v010_topology_control,
    run_v011_topology_revalidation_case,
)


ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "topology_results.json"


def run() -> dict:
    legacy = run_v010_topology_control(entity_count=64, moved_index=40, target_index=5)
    print(
        "TOPOLOGY_V010_CONTROL",
        {
            "admission": legacy["admission_read_keys"],
            "promotion": legacy["promotion_read_keys"],
            "stale_read_admitted": legacy["stale_read_admitted"],
            "stale_value_visible": legacy["stale_value_visible"],
        },
    )

    revalidated = run_v011_topology_revalidation_case(
        entity_count=64,
        moved_index=40,
        target_index=5,
    )
    print(
        "TOPOLOGY_V011_REVALIDATED",
        {
            "admission": revalidated["admission_read_keys"],
            "promotion": revalidated["promotion_read_keys"],
            "changed": revalidated["read_keys_changed"],
            "stale_read_blocked": revalidated["stale_read_blocked"],
            "index_lookup": revalidated["revalidation_lookup_uses_index"],
        },
    )

    locality_rows = []
    for entity_count in (100, 1_000, 10_000, 50_000):
        row = run_topology_locality_case(entity_count)
        locality_rows.append(row)
        print(
            "TOPOLOGY_SCALE",
            entity_count,
            {
                "recovery_work": row["total_recovery_work"],
                "full_rebuild": row["full_rebuild_work"],
                "stale_read_blocked": row["stale_read_blocked"],
            },
        )

    if not legacy["stale_read_admitted"] or not legacy["stale_value_visible"]:
        raise AssertionError("v0.10 control did not reproduce stale-read topology leak")
    if legacy["read_keys_changed"]:
        raise AssertionError("v0.10 control unexpectedly revalidated impact metadata")

    if not revalidated["read_keys_changed"]:
        raise AssertionError("v0.11 did not refresh topology-dependent read keys")
    if not revalidated["stale_read_blocked"] or revalidated["stale_read_admitted"]:
        raise AssertionError("v0.11 admitted a stale read after topology mutation")
    if not revalidated["unrelated_read_admitted"]:
        raise AssertionError("v0.11 over-blocked an unrelated read")
    if not revalidated["revalidation_lookup_uses_index"]:
        raise AssertionError("v0.11 topology revalidation did not use the evidence index")

    total_work = {row["total_recovery_work"] for row in locality_rows}
    if len(total_work) != 1:
        raise AssertionError(
            f"fixed two-intent topology recovery grew with unrelated cardinality: {total_work}"
        )

    required_true = (
        legacy["semantic_check"],
        legacy["materialization_equal"],
        revalidated["semantic_check"],
        revalidated["materialization_equal"],
        *[row["semantic_check"] for row in locality_rows],
        *[row["materialization_equal"] for row in locality_rows],
        *[row["stale_read_blocked"] for row in locality_rows],
        *[row["unrelated_read_admitted"] for row in locality_rows],
        *[row["revalidation_lookup_uses_index"] for row in locality_rows],
    )
    if not all(required_true):
        raise AssertionError("v0.11 safety/correctness condition failed")

    out = {
        "experiment": "v0.11_topology_dependent_intent_revalidation",
        "control": legacy,
        "revalidated": revalidated,
        "locality_cardinalities": [100, 1_000, 10_000, 50_000],
        "locality_rows": locality_rows,
        "invariant": (
            "canonical conflict preconditions may remain admission-time facts, but derived-impact "
            "metadata must be revalidated after earlier intents that can change dependency topology"
        ),
        "mechanism": (
            "promotion revalidates topology-dependent read keys inside the same BEGIN IMMEDIATE "
            "transaction that installs the active maintenance journal"
        ),
        "scope": (
            "single SQLite database; ordered logical intents; assertion-subject topology moves; "
            "evidence-to-assertion dependency lookup; synthetic oracle assertions; no distributed claim"
        ),
    }
    RESULTS_PATH.write_text(json.dumps(out, indent=2))
    print("TOPOLOGY_RESULTS_JSON")
    print(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    run()
