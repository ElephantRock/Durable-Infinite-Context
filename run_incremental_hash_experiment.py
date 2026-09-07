from __future__ import annotations

import json
from pathlib import Path

from simulator.normalized_membership import run_v016_normalized_case
from storage.hash_resize import run_hash_resize_envelope
from storage.incremental_hash import run_incremental_hash_envelope


ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "incremental_hash_results.json"


def _require_semantic_guard(row: dict) -> None:
    if not row["membership_equal"]:
        raise AssertionError("v0.16 membership drifted before incremental-hash experiment")
    if not row["materialization_equal"] or not row["all_derived_fresh"]:
        raise AssertionError("v0.16 semantic guard failed clean-rebuild parity")
    if not row["head_index_equal"]:
        raise AssertionError("v0.16 semantic guard lost current-head parity")
    if not row["full_assembly_equal"] or not row["partial_assembly_equal"]:
        raise AssertionError("v0.16 semantic guard changed logical profile semantics")


def run() -> dict:
    semantic_guard = run_v016_normalized_case(
        entity_count=128,
        predicate_count=16,
        history_depth=8,
        changed_count=1,
    )
    _require_semantic_guard(semantic_guard)

    checkpoints = (1_000, 4_000, 16_000, 64_000, 256_000)
    control = run_hash_resize_envelope(
        checkpoints=checkpoints,
        initial_capacity=128,
        slots_per_page=64,
        max_load=0.50,
        sample_count=512,
    )
    candidate = run_incremental_hash_envelope(
        checkpoints=checkpoints,
        initial_capacity=128,
        slots_per_page=64,
        max_load=0.50,
        migration_slot_budget=8,
        sample_count=512,
    )

    control_largest = [row["largest_single_resize_rows"] for row in control["rows"]]
    if control_largest != sorted(control_largest):
        raise AssertionError(f"stop-the-world resize maximum decreased: {control_largest}")
    if control_largest[-1] <= control_largest[0]:
        raise AssertionError("control did not expose N-growing resize work")

    budget = candidate["migration_slot_budget"]
    if candidate["global_max_source_slots_scanned_per_insert"] > budget:
        raise AssertionError("incremental source scan exceeded fixed mutation budget")
    if candidate["global_max_rows_copied_per_insert"] > budget:
        raise AssertionError("incremental copied rows exceeded source scan budget")
    if candidate["global_max_capacity_amplification_vs_current"] > 1.5 + 1e-12:
        raise AssertionError("two-generation capacity amplification exceeded 1.5x")
    if not candidate["migration_events"] or not candidate["migration_snapshots"]:
        raise AssertionError("incremental experiment did not exercise live migration")
    if not all(
        event["completion_live_size"] is not None
        for event in candidate["migration_events"]
    ):
        raise AssertionError("an incremental migration failed to complete")
    if not all(
        snapshot["lookup_generations_max"] <= 2
        for snapshot in candidate["migration_snapshots"]
    ):
        raise AssertionError("incremental lookup touched more than two generations")

    control_total_rehash = control["rows"][-1]["cumulative_rehashed_rows"]
    if candidate["cumulative_rows_copied"] != control_total_rehash:
        raise AssertionError(
            "incremental migration changed total resize-row work instead of scheduling it"
        )
    if candidate["global_max_rows_copied_per_insert"] >= control_largest[-1]:
        raise AssertionError("incremental migration failed to reduce the single-mutation spike")

    mutation_slot_maxima = [
        row["interval_max_mutation_slot_work"] for row in candidate["rows"]
    ]

    for row in candidate["rows"]:
        print("INCREMENTAL_HASH_N", row)
    for snapshot in candidate["migration_snapshots"]:
        print("INCREMENTAL_HASH_MIGRATION_SNAPSHOT", snapshot)

    out = {
        "experiment": "v0.19_incremental_hash_migration",
        "semantic_guard": {
            "membership_equal": semantic_guard["membership_equal"],
            "materialization_equal": semantic_guard["materialization_equal"],
            "head_index_equal": semantic_guard["head_index_equal"],
            "all_derived_fresh": semantic_guard["all_derived_fresh"],
            "full_assembly_equal": semantic_guard["full_assembly_equal"],
            "partial_assembly_equal": semantic_guard["partial_assembly_equal"],
        },
        "stop_the_world_control": control,
        "incremental_candidate": candidate,
        "observed_mutation_slot_work_maxima": mutation_slot_maxima,
        "hypothesis_under_test": (
            "a two-generation open-addressed hash index with a strict fixed source-slot "
            "migration budget can remove the stop-the-world Theta(N) resize charge from "
            "one logical mutation while preserving bounded generation fan-out and completing "
            "migration under sustained growth"
        ),
        "prediction": (
            "source slots scanned and rows copied per insertion must never exceed the fixed "
            "budget; every migration must complete before another resize is required; live "
            "lookup must touch at most two generations; and temporary allocated slot capacity "
            "must stay at or below 1.5x the target generation"
        ),
        "result": (
            "the migration-scheduling hypothesis survives the tested algorithmic envelope: "
            "source scanning and copied rows are bounded per insertion, migration completes, "
            "and lookup generation fan-out is at most two. Total rehash work is distributed "
            "rather than removed. The stronger claim that total mutation work is globally "
            "bounded does not survive this evidence because destination linear probing is "
            "unbounded and the observed interval mutation-slot maxima rise with the sweep."
        ),
        "observed_caveat": (
            "fixed source migration budget does not bound destination placement probes: "
            f"interval maximum mutation slot work is {mutation_slot_maxima}"
        ),
        "revision": (
            "incremental migration is a stronger resize scheduler than stop-the-world rehash, "
            "but it is not yet earned as the production address index. Before persistence, "
            "test a placement mechanism with a defensible bounded collision/relocation "
            "envelope under growing and adversarial collision fixtures; crash-safe migration "
            "remains a later mandatory gate."
        ),
        "measurement_scope": candidate["measurement_scope"],
    }
    RESULTS_PATH.write_text(json.dumps(out, indent=2))
    print("INCREMENTAL_HASH_RESULTS_JSON")
    print(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    run()
