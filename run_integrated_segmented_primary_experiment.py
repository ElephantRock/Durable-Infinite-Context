from __future__ import annotations

import json
from pathlib import Path

from simulator.segmented_fixed_page_primary import (
    CAPACITIES,
    FAILPOINTS,
    run_capacity_sweep,
    run_crash_matrix,
)
from simulator.normalized_membership import run_v016_normalized_case

ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "integrated_segmented_primary_results.json"


def _require_semantic_guard(row: dict) -> None:
    required = (
        "membership_equal",
        "materialization_equal",
        "head_index_equal",
        "all_derived_fresh",
        "full_assembly_equal",
        "partial_assembly_equal",
    )
    if not all(bool(row[name]) for name in required):
        raise AssertionError("v0.16 semantic guard failed before v0.30 experiment")


def run() -> dict:
    semantic_guard = run_v016_normalized_case(
        entity_count=128,
        predicate_count=16,
        history_depth=8,
        changed_count=1,
    )
    _require_semantic_guard(semantic_guard)

    capacity = run_capacity_sweep()
    crash = run_crash_matrix()

    if capacity["capacities"] != list(CAPACITIES):
        raise AssertionError("v0.30 capacity sweep drifted")
    if crash["failpoints"] != list(FAILPOINTS):
        raise AssertionError("v0.30 crash failpoint set drifted")
    if crash["case_count"] != len(CAPACITIES) * len(FAILPOINTS):
        raise AssertionError("v0.30 crash matrix cardinality drifted")
    if not crash["all_exact_committed_state_match"]:
        raise AssertionError("v0.30 SIGKILL exposed a torn committed state")
    if not crash["all_existing_seed_visible"]:
        raise AssertionError("v0.30 SIGKILL lost pre-existing membership")
    if not crash["all_recovery_state_unchanged"]:
        raise AssertionError("v0.30 recovery changed committed logical state")
    if int(crash["max_uncommitted_tail_after_recovery_bytes"]) != 0:
        raise AssertionError("v0.30 recovery left file-length residue")
    if int(capacity["max_observed_physical_bytes_appended"]) > int(
        capacity["derived_transaction_tail_cap_bytes"]
    ):
        raise AssertionError("v0.30 observed append exceeded derived constant transaction cap")

    for row in capacity["rows"]:
        print(
            "INTEGRATED_SEGMENT_CAPACITY",
            row["old_capacity"],
            {
                "logical_segment": row["target_logical_segment_id_max"],
                "segments_allocated": row["new_segments_allocated"],
                "append_bytes": row["physical_bytes_appended"],
                "lookup_preads": row["lookup_total_preads"],
                "missing_preads": row["missing_total_preads"],
            },
        )
    for row in crash["rows"]:
        print(
            "INTEGRATED_SEGMENT_CRASH",
            row["old_capacity"],
            row["failpoint"],
            {
                "committed": row["expected_committed"],
                "tail_before": row["uncommitted_tail_before_recovery_bytes"],
                "tail_after": row["uncommitted_tail_after_recovery_bytes"],
                "epoch": row["crash_epoch"],
            },
        )

    out = {
        "experiment": "v0.30_integrated_segmented_primary",
        "semantic_guard": {
            "membership_equal": semantic_guard["membership_equal"],
            "materialization_equal": semantic_guard["materialization_equal"],
            "head_index_equal": semantic_guard["head_index_equal"],
            "all_derived_fresh": semantic_guard["all_derived_fresh"],
            "full_assembly_equal": semantic_guard["full_assembly_equal"],
            "partial_assembly_equal": semantic_guard["partial_assembly_equal"],
        },
        "capacity_sweep": capacity,
        "crash_matrix": crash,
        "hypothesis_under_test": (
            "integrating append-local 16-page physical segments and an eight-level dual-copy radix "
            "extent map into the v0.24 logical primary can preserve bounded lookup/migration semantics "
            "under real file operations and process death while making uncommitted file-length residue "
            "independent of the numeric generation capacity"
        ),
        "prediction": (
            "when the second row forces C->2C migration at C in {32,2048,131072,4194304}, the new "
            "generation logical page/segment ids must grow with C but each fresh physical segment must "
            "append at most 46 pages, per-transaction fresh segments must remain bounded by the fixed "
            "placement/migration budgets, and single/two-generation lookup preads must stay within the "
            "fixed-depth radix envelopes. Real SIGKILL after allocation, dependency writes, dependency "
            "fsync, or superblock commit must expose exactly the pre/post committed fixture state; "
            "restart must truncate only from the committed physical frontier with zero generation scan "
            "and zero logical redo."
        ),
        "result": (
            "survives only if all capacity and crash rows satisfy those bounds. A passing result remains "
            "a single-writer process-crash experiment: it does not prove hardware power-loss, torn-write, "
            "device-I/O, multi-writer, distributed, or production-performance properties."
        ),
        "revision": (
            "if the integrated candidate survives, the next falsification target should remove the "
            "experiment-only assumption that radix-node JSON payloads stay sparse enough for one 4096-byte "
            "record and test node split/overflow behavior under dense materialized segment prefixes."
        ),
        "measurement_scope": (
            "real os.pread/os.pwrite/ftruncate/fsync calls, 4096-byte CRC records, process-visible file "
            "length, fixed-depth radix traversal, bounded placement/migration work, exact known-fixture "
            "committed state across real SIGKILL, and derivation-based physical-tail cleanup."
        ),
    }
    RESULTS_PATH.write_text(json.dumps(out, indent=2))
    print("INTEGRATED_SEGMENTED_PRIMARY_RESULTS_JSON")
    print(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    run()
