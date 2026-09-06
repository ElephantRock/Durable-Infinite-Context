from __future__ import annotations

import json
from pathlib import Path

from simulator.predicate_schema import (
    run_predicate_locality_case,
    run_v012_predicate_control,
    run_v013_predicate_addition,
    run_v013_predicate_replacement,
)


ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "predicate_schema_results.json"


def run() -> dict:
    control = run_v012_predicate_control(entity_count=64, index=40)
    print(
        "PREDICATE_V012_CONTROL",
        {
            "canonical_changed": control["canonical_changed"],
            "new_context_present": control["new_context_present"],
            "profile_present": control["profile_present"],
            "subject_derived_count": control["subject_derived_count"],
            "materialization_equal": control["materialization_equal"],
        },
    )

    replacement = run_v013_predicate_replacement(entity_count=64, index=40)
    print(
        "PREDICATE_V013_REPLACEMENT",
        {
            "canonical_changed": replacement["canonical_changed"],
            "new_context_present": replacement["new_context_present"],
            "profile_predicates": replacement["profile_predicates"],
            "subject_derived_count": replacement["subject_derived_count"],
            "materialization_equal": replacement["materialization_equal"],
            "recovery_work": replacement["recovery_work"],
        },
    )

    addition = run_v013_predicate_addition(entity_count=64, index=40)
    print(
        "PREDICATE_V013_ADDITION",
        {
            "profile_predicates": addition["profile_predicates"],
            "subject_derived_count": addition["subject_derived_count"],
            "materialization_equal": addition["materialization_equal"],
            "queue_final": addition["queue_final"],
        },
    )

    locality_rows = []
    for entity_count in (100, 1_000, 10_000, 50_000):
        row = run_predicate_locality_case(entity_count)
        locality_rows.append(row)
        print(
            "PREDICATE_SCALE",
            entity_count,
            {
                "recovery_work": row["recovery_work"],
                "full_rebuild": row["full_rebuild_work"],
                "profile_predicates": row["profile_predicates"],
            },
        )

    if not control["canonical_changed"] or not control["new_context_present"]:
        raise AssertionError("v0.12 predicate control did not apply canonical/new-predicate state")
    if control["profile_present"] or control["subject_derived_count"] != 3:
        raise AssertionError("v0.12 control did not reproduce subject-profile loss")
    if control["materialization_equal"]:
        raise AssertionError("v0.12 control unexpectedly matched clean predicate rebuild")
    if not control["all_derived_fresh"]:
        raise AssertionError("control failure must be semantic completeness, not stale lifecycle")

    if not replacement["canonical_changed"]:
        raise AssertionError("v0.13 predicate replacement did not change canonical predicate")
    if not replacement["new_context_present"] or not replacement["old_context_retired"]:
        raise AssertionError("v0.13 predicate replacement context lifecycle is incorrect")
    if replacement["profile_predicates"] != ["launch_date"]:
        raise AssertionError("v0.13 replacement profile did not follow actual predicate set")
    if replacement["subject_derived_count"] != 4:
        raise AssertionError("v0.13 replacement did not converge to one profile plus one predicate triplet")
    if not replacement["materialization_equal"] or not replacement["all_derived_fresh"]:
        raise AssertionError("v0.13 replacement did not converge to clean reconstruction")
    if not replacement["profile_lookup_uses_index"]:
        raise AssertionError("subject-wide profile reconstruction did not use the subject/predicate index")

    if addition["profile_predicates"] != ["deadline", "launch_date"]:
        raise AssertionError("v0.13 profile did not aggregate simultaneously live predicates")
    if addition["subject_derived_count"] != 7:
        raise AssertionError("v0.13 addition did not produce one profile plus two predicate triplets")
    if not addition["deadline_context_present"] or not addition["new_context_present"]:
        raise AssertionError("v0.13 addition lost a predicate-specific context")
    if addition["queue_final"]["done"] != 2 or addition["queue_final"]["conflict"] != 0:
        raise AssertionError("v0.13 ordered evidence/assertion addition did not drain cleanly")
    if not addition["materialization_equal"] or not addition["all_derived_fresh"]:
        raise AssertionError("v0.13 addition did not converge to clean reconstruction")

    work = {row["recovery_work"] for row in locality_rows}
    if len(work) != 1:
        raise AssertionError(
            f"predicate replacement work grew with unrelated cardinality: {work}"
        )
    for row in locality_rows:
        if not (
            row["canonical_changed"]
            and row["new_context_present"]
            and row["old_context_retired"]
            and row["profile_predicates"] == ["launch_date"]
            and row["subject_derived_count"] == 4
            and row["materialization_equal"]
            and row["all_derived_fresh"]
            and row["profile_lookup_uses_index"]
        ):
            raise AssertionError(f"v0.13 predicate safety failure at N={row['entity_count']}")
        if row["recovery_work"] >= row["full_rebuild_work"]:
            raise AssertionError(
                f"v0.13 local predicate repair not cheaper than rebuild at N={row['entity_count']}"
            )

    out = {
        "experiment": "v0.13_subject_wide_predicate_schema",
        "control": control,
        "replacement": replacement,
        "addition": addition,
        "locality_cardinalities": [100, 1_000, 10_000, 50_000],
        "locality_rows": locality_rows,
        "invariant": (
            "a derived node whose identity omits predicate must have predicate-independent subject-wide "
            "semantics; freshness and node existence are insufficient if its value silently hard-codes one predicate"
        ),
        "mechanism": (
            "subject profiles aggregate the latest assertion for every predicate reachable by indexed subject lookup; "
            "state/support/context remain predicate-specific, and topology growth still creates only missing predicate outputs"
        ),
        "scope": (
            "single SQLite database; controlled deadline->launch_date replacement plus one two-predicate addition; "
            "subject-local indexed reconstruction; synthetic oracle assertions; no arbitrary schema registry, ontology migration, or distributed claim"
        ),
    }
    RESULTS_PATH.write_text(json.dumps(out, indent=2))
    print("PREDICATE_SCHEMA_RESULTS_JSON")
    print(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    run()
