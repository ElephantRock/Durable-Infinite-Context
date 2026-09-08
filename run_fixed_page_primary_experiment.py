from __future__ import annotations

import json
from pathlib import Path

from simulator.fixed_page_primary import (
    FAILPOINTS,
    SCENARIOS,
    run_active_migration_lookup_envelope,
    run_collision_rejection,
    run_common_envelope,
    run_crash_case,
)
from simulator.normalized_membership import run_v016_normalized_case

ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "fixed_page_primary_results.json"


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
        raise AssertionError("v0.16 semantic guard failed before v0.24 experiment")


def run() -> dict:
    semantic_guard = run_v016_normalized_case(
        entity_count=128,
        predicate_count=16,
        history_depth=8,
        changed_count=1,
    )
    _require_semantic_guard(semantic_guard)

    crash_rows: list[dict] = []
    for scenario in SCENARIOS:
        for failpoint in FAILPOINTS:
            row = run_crash_case(scenario, failpoint).to_dict()
            crash_rows.append(row)
            print(
                "FIXED_PAGE_CRASH",
                scenario,
                failpoint,
                {
                    "committed": row["expected_committed"],
                    "visible": row["target_visible_after_crash"],
                    "epoch": row["crash_epoch"],
                    "tail_bytes": row["physical_tail_bytes"],
                    "recovery_work": row["recovery_one"]["logical_work"],
                },
            )

    if len(crash_rows) != len(SCENARIOS) * len(FAILPOINTS):
        raise AssertionError("fixed-page crash matrix cardinality drifted")
    if not all(row["exact_snapshot_match"] for row in crash_rows):
        raise AssertionError("fixed-page crash exposed a torn logical snapshot")
    if not all(row["audit_valid"] and row["existing_keys_found"] for row in crash_rows):
        raise AssertionError("fixed-page crash lost or duplicated membership")
    if not all(row["recovery_idempotent"] for row in crash_rows):
        raise AssertionError("fixed-page recovery was not idempotent")
    if any(int(row["recovery_one"]["logical_work"]) != 0 for row in crash_rows):
        raise AssertionError("fixed-page store required application redo")
    for row in crash_rows:
        expected_epoch = row["post_epoch"] if row["expected_committed"] else row["pre_epoch"]
        if int(row["crash_epoch"]) != int(expected_epoch):
            raise AssertionError(f"commit epoch mismatch: {row}")

    common = run_common_envelope()
    if common["global_max_source_slots_scanned"] > 8:
        raise AssertionError("fixed-page source migration exceeded budget")
    if common["global_max_rows_moved"] > 8:
        raise AssertionError("fixed-page migration moved more than source budget")
    if common["address_formula"]["primary_index_structure"] != "none":
        raise AssertionError("primary path unexpectedly gained an index")
    if any(row["lookup_total_pread_max"] > 8 for row in common["rows"]):
        raise AssertionError("single-generation direct lookup exceeded fixed pread envelope")
    if any(row["missing_total_preads"] != 8 for row in common["rows"]):
        raise AssertionError("single-generation missing lookup envelope drifted")

    migration = run_active_migration_lookup_envelope()
    if migration["successful_lookup_total_pread_max"] > 14:
        raise AssertionError("two-generation successful lookup exceeded fixed envelope")
    if migration["missing_total_preads"] != 14:
        raise AssertionError("two-generation missing lookup envelope drifted")
    if not migration["audit_valid"]:
        raise AssertionError("active-migration audit failed")

    collision = run_collision_rejection()
    if not (
        collision["rejected"]
        and collision["snapshot_unchanged"]
        and collision["audit_valid"]
        and collision["first_rejected_ordinal"] == 17
    ):
        raise AssertionError("bounded collision rejection control failed")

    out = {
        "experiment": "v0.24_fixed_page_primary",
        "semantic_guard": {
            "membership_equal": semantic_guard["membership_equal"],
            "materialization_equal": semantic_guard["materialization_equal"],
            "head_index_equal": semantic_guard["head_index_equal"],
            "all_derived_fresh": semantic_guard["all_derived_fresh"],
            "full_assembly_equal": semantic_guard["full_assembly_equal"],
            "partial_assembly_equal": semantic_guard["partial_assembly_equal"],
        },
        "crash_rows": crash_rows,
        "common_envelope": common,
        "active_migration_lookup_envelope": migration,
        "collision_rejection": collision,
        "hypothesis_under_test": (
            "a two-copy fixed-page primary with arithmetic bucket addressing and a fixed-size "
            "commit superblock can remove growing comparison-index traversal from the primary "
            "lookup path while preserving the v0.23-style process-crash atomicity contract"
        ),
        "prediction": (
            "real SIGKILL after data-page writes or their fsync must expose the exact pre-insert "
            "logical snapshot, while SIGKILL after the committed superblock fsync must expose the "
            "exact post-insert snapshot; recovery must require zero logical redo. In a single "
            "generation, lookup must issue exactly two superblock preads plus at most three "
            "dual-copy primary-page reads (<=8 os.pread calls total), independent of tested N. "
            "During two-generation migration the corresponding miss bound is 14 pread calls."
        ),
        "result": (
            "survives only if the fixed crash matrix, direct-address lookup envelope, bounded "
            "migration, and collision rejection control all pass. Even then this does not prove "
            "OS/device page I/O independence: os.pread invocation counts may be satisfied from "
            "cache, and exceptional overflow is not integrated into this fixed-page store."
        ),
        "revision": (
            "if the primary survives, the next falsification target is cross-store hybridization: "
            "make the fixed-page primary and exact overflow share one durable commit protocol, "
            "then measure WAL/fsync/page-write amplification rather than SQL row writes."
        ),
        "measurement_scope": (
            "exact user-space os.pread/os.pwrite invocation counts, arithmetic byte offsets, "
            "fixed 4096-byte record images with CRC, bounded migration work, sparse-file bytes, "
            "and exact logical state after real process SIGKILL. No device-read count, hardware "
            "power-loss, multi-writer, distributed, or production-latency claim."
        ),
    }
    RESULTS_PATH.write_text(json.dumps(out, indent=2))
    print("FIXED_PAGE_PRIMARY_RESULTS_JSON")
    print(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    run()
