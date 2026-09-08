from __future__ import annotations

import json
from pathlib import Path

from simulator.cross_store_hybrid import (
    SCENARIO_FAILPOINTS,
    run_common_envelope,
    run_crash_case,
    run_overflow_envelope,
    run_reclamation_scaling,
)
from simulator.normalized_membership import run_v016_normalized_case

ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "cross_store_hybrid_results.json"


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
        raise AssertionError("v0.16 semantic guard failed before v0.25 experiment")


def run() -> dict:
    semantic_guard = run_v016_normalized_case(
        entity_count=128,
        predicate_count=16,
        history_depth=8,
        changed_count=1,
    )
    _require_semantic_guard(semantic_guard)

    crash_rows: list[dict] = []
    for scenario, failpoints in SCENARIO_FAILPOINTS.items():
        for failpoint in failpoints:
            row = run_crash_case(scenario, failpoint).to_dict()
            crash_rows.append(row)
            print(
                "CROSS_STORE_CRASH",
                scenario,
                failpoint,
                {
                    "committed": row["expected_committed"],
                    "future_overflow": row["pre_recovery_future_overflow_rows"],
                    "tail_bytes": row["pre_recovery_tail_bytes"],
                    "deleted_future": row["recovery_one"]["deleted_future_overflow_rows"],
                    "reclaimed_tail": row["recovery_one"]["reclaimed_tail_bytes"],
                },
            )

    if len(crash_rows) != sum(len(v) for v in SCENARIO_FAILPOINTS.values()):
        raise AssertionError("v0.25 crash matrix cardinality drifted")
    if not all(row["exact_snapshot_match_before_recovery"] for row in crash_rows):
        raise AssertionError("cross-store crash exposed a torn logical snapshot")
    if not all(row["exact_snapshot_match_after_recovery"] for row in crash_rows):
        raise AssertionError("recovery changed committed logical state")
    if not all(row["existing_keys_found"] and row["audit_valid_before_recovery"] for row in crash_rows):
        raise AssertionError("cross-store crash lost or duplicated committed membership")
    if not all(row["recovery_idempotent"] for row in crash_rows):
        raise AssertionError("cross-store recovery was not idempotent")

    overflow_committed = next(
        row for row in crash_rows
        if row["scenario"] == "overflow_admission" and row["failpoint"] == "overflow_committed"
    )
    if int(overflow_committed["pre_recovery_future_overflow_rows"]) != 1:
        raise AssertionError("future overflow control did not leave one hidden durable row")
    if int(overflow_committed["recovery_one"]["deleted_future_overflow_rows"]) != 1:
        raise AssertionError("recovery did not remove hidden future overflow row")

    for failpoint in ("primary_pages_written", "primary_data_synced"):
        row = next(
            item for item in crash_rows
            if item["scenario"] == "migration_start" and item["failpoint"] == failpoint
        )
        if int(row["pre_recovery_tail_bytes"]) <= 0:
            raise AssertionError("migration-start crash did not expose stale physical tail")
        if int(row["recovery_one"]["reclaimed_tail_bytes"]) != int(row["pre_recovery_tail_bytes"]):
            raise AssertionError("migration-start stale tail was not fully reclaimed")

    common = run_common_envelope()
    if int(common["global_max_source_slots_scanned"]) > 8:
        raise AssertionError("v0.25 migration source scan exceeded eight-slot budget")
    if int(common["global_max_rows_moved"]) > 8:
        raise AssertionError("v0.25 migration moved more than eight source rows")
    if int(common["migration_starts"]) <= 0 or int(common["migration_starts"]) != int(common["migration_completions"]):
        raise AssertionError("ordinary envelope failed to complete every migration")
    for row in common["rows"]:
        if int(row["successful_lookup_overflow_checks"]) != 0:
            raise AssertionError("ordinary successful lookup queried exceptional overflow")
        if int(row["successful_lookup_pread_max"]) > 8:
            raise AssertionError("ordinary fixed-page lookup exceeded v0.24 userspace pread envelope")
        if int(row["visible_overflow_rows"]) != 0 or not row["audit_valid"]:
            raise AssertionError("ordinary cross-store envelope contaminated overflow")

    overflow = run_overflow_envelope()
    heights = [int(row["overflow_btree_height"]) for row in overflow["rows"]]
    if heights != sorted(heights) or max(heights) <= min(heights):
        raise AssertionError("persistent exact overflow failed to expose growing B-tree height")
    for row in overflow["rows"]:
        if row["primary_hit_overflow_checked"]:
            raise AssertionError("bounded common-path hit queried overflow")
        if int(row["coordinator_superblock_pwrites_per_admission"]) != 1:
            raise AssertionError("overflow admission coordinator superblock write count drifted")
        if int(row["coordinator_explicit_fsyncs_per_admission"]) != 1:
            raise AssertionError("overflow admission explicit fixed-file fsync count drifted")
        if int(row["sqlite_commits_per_admission"]) != 1:
            raise AssertionError("overflow admission SQLite commit count drifted")
        if not row["audit_valid"]:
            raise AssertionError("persistent overflow envelope audit failed")

    reclamation = run_reclamation_scaling()
    reclaimed = [int(row["reclaimed_tail_bytes"]) for row in reclamation["rows"]]
    if reclaimed != sorted(reclaimed) or max(reclaimed) <= min(reclaimed):
        raise AssertionError("reclamation control failed to expose growing stale-tail bytes")
    for row in reclamation["rows"]:
        if int(row["tail_bytes_after_recovery"]) != 0:
            raise AssertionError("reclamation left stale tail")
        if int(row["truncate_calls"]) != 1 or int(row["fixed_file_fsyncs"]) != 1:
            raise AssertionError("reclamation syscall envelope drifted")
        if int(row["logical_redo"]) != 0 or not row["audit_valid"]:
            raise AssertionError("reclamation required logical redo or broke audit")

    out = {
        "experiment": "v0.25_cross_store_hybrid_reclamation",
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
        "overflow_envelope": overflow,
        "reclamation_scaling": reclamation,
        "hypothesis_under_test": (
            "the fixed-page primary committed epoch can coordinate direct exact SQLite overflow "
            "admission so process crash exposes only the exact pre/post logical image, while "
            "future overflow rows and uncommitted fixed-page tail are safely reclaimable without "
            "contaminating ordinary primary-hit lookup"
        ),
        "prediction": (
            "overflow rows committed before the primary coordinator epoch advances must remain "
            "invisible and be deleted by recovery; pre-commit migration allocation must be "
            "truncatable to the committed next_page_id frontier; ordinary primary hits must not "
            "query overflow. If stale-tail bytes grow with generation capacity, strict bounded "
            "physical-reclamation volume is falsified even if reclamation uses one truncate call."
        ),
        "result": (
            "survives only if the crash oracle, common-path isolation, exact overflow geometry, "
            "and safe idempotent reclamation all pass. Growing reclaimed byte volume is retained "
            "as a negative result rather than inferred away from constant syscall counts."
        ),
        "measurement_scope": (
            "exact logical snapshots across a fixed-page file plus persistent SQLite overflow, "
            "real process SIGKILL, userspace fixed-file pread/pwrite/fsync calls, SQLite logical "
            "commit count, SQLite dbstat B-tree height, file sizes, and reclaimed bytes. SQLite "
            "internal fsync/WAL-frame counts, filesystem/device I/O, cache misses, power-loss "
            "torn-write behavior, multi-writer semantics, and production latency are not measured."
        ),
    }
    RESULTS_PATH.write_text(json.dumps(out, indent=2))
    print("CROSS_STORE_HYBRID_RESULTS_JSON")
    print(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    run()
