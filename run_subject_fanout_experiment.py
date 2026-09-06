from __future__ import annotations

import json
from pathlib import Path

from simulator.subject_fanout import (
    run_head_fallback_case,
    run_v013_history_control,
    run_v014_head_index_case,
)


ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "subject_fanout_results.json"


def _require_safe(row: dict) -> None:
    if not row["materialization_equal"] or not row["all_derived_fresh"]:
        raise AssertionError(
            f"materialization failure for {row['store']} "
            f"N={row['entity_count']} P={row['predicate_count']} H={row['history_depth']}"
        )
    if row["profile_predicate_count"] != row["predicate_count"]:
        raise AssertionError("profile predicate cardinality does not match canonical live set")
    if row["queue_final"]["done"] < 1 or row["queue_final"]["conflict"] != 0:
        raise AssertionError("fanout measurement queue did not drain cleanly")
    if row["store"] == "HeadIndexedPredicateStore":
        if not row["head_index_equal"]:
            raise AssertionError("subject-predicate head index differs from canonical oracle")
        if not row["head_lookup_uses_index"] or not row["head_refresh_uses_index"]:
            raise AssertionError("v0.14 head access is not index-backed")
        if row["head_trace"]["head_rows_read"] != row["predicate_count"]:
            raise AssertionError("v0.14 did not read exactly one head per live predicate")
        if row["head_trace"]["head_refresh_queries"] != 0:
            raise AssertionError("evidence-only measurement unexpectedly refreshed assertion heads")


def run() -> dict:
    discriminating_control = run_v013_history_control(
        entity_count=128,
        predicate_count=8,
        history_depth=16,
    )
    discriminating_fixed = run_v014_head_index_case(
        entity_count=128,
        predicate_count=8,
        history_depth=16,
    )
    _require_safe(discriminating_control)
    _require_safe(discriminating_fixed)
    if discriminating_fixed["total_recovery_work"] >= discriminating_control["total_recovery_work"]:
        raise AssertionError("v0.14 did not eliminate enough history work to beat the deep-history control")

    history_depths = [1, 2, 4, 8, 16, 32, 64]
    history_rows = []
    for history_depth in history_depths:
        control = run_v013_history_control(
            entity_count=128,
            predicate_count=8,
            history_depth=history_depth,
        )
        fixed = run_v014_head_index_case(
            entity_count=128,
            predicate_count=8,
            history_depth=history_depth,
        )
        _require_safe(control)
        _require_safe(fixed)
        history_rows.append(
            {
                "history_depth": history_depth,
                "control_work": control["total_recovery_work"],
                "control_canonical_rows_read": control["base_recovery_trace"]["canonical_rows_read"],
                "fixed_work": fixed["total_recovery_work"],
                "fixed_base_work": fixed["base_recovery_work"],
                "fixed_head_work": fixed["head_trace"]["logical_work"],
                "fixed_canonical_rows_read": fixed["base_recovery_trace"]["canonical_rows_read"],
                "full_rebuild_work": fixed["full_rebuild_work"],
            }
        )
        print("FANOUT_HISTORY", history_rows[-1])

    control_work = [row["control_work"] for row in history_rows]
    if any(b <= a for a, b in zip(control_work, control_work[1:])):
        raise AssertionError(f"v0.13 history control did not grow monotonically: {control_work}")
    fixed_work = {row["fixed_work"] for row in history_rows}
    fixed_reads = {row["fixed_canonical_rows_read"] for row in history_rows}
    if len(fixed_work) != 1 or len(fixed_reads) != 1:
        raise AssertionError(
            f"v0.14 current-profile work still depends on history depth: work={fixed_work} reads={fixed_reads}"
        )

    predicate_counts = [1, 2, 4, 8, 16, 32]
    predicate_rows = []
    for predicate_count in predicate_counts:
        row = run_v014_head_index_case(
            entity_count=128,
            predicate_count=predicate_count,
            history_depth=8,
        )
        _require_safe(row)
        predicate_rows.append(
            {
                "predicate_count": predicate_count,
                "total_work": row["total_recovery_work"],
                "base_work": row["base_recovery_work"],
                "head_work": row["head_trace"]["logical_work"],
                "head_rows_read": row["head_trace"]["head_rows_read"],
                "canonical_rows_read": row["base_recovery_trace"]["canonical_rows_read"],
                "full_rebuild_work": row["full_rebuild_work"],
            }
        )
        print("FANOUT_PREDICATES", predicate_rows[-1])

    p_work = [row["total_work"] for row in predicate_rows]
    if any(b <= a for a, b in zip(p_work, p_work[1:])):
        raise AssertionError(f"true live-predicate work did not grow with profile size: {p_work}")

    global_cardinalities = [100, 1_000, 10_000, 50_000]
    global_rows = []
    for entity_count in global_cardinalities:
        row = run_v014_head_index_case(
            entity_count=entity_count,
            predicate_count=8,
            history_depth=8,
        )
        _require_safe(row)
        global_rows.append(
            {
                "entity_count": entity_count,
                "total_work": row["total_recovery_work"],
                "base_work": row["base_recovery_work"],
                "head_work": row["head_trace"]["logical_work"],
                "full_rebuild_work": row["full_rebuild_work"],
            }
        )
        print("FANOUT_GLOBAL", global_rows[-1])

    if len({row["total_work"] for row in global_rows}) != 1:
        raise AssertionError("fixed local P/H work grew with unrelated global cardinality")

    fallback = run_head_fallback_case(entity_count=64, index=30, history_depth=4)
    if fallback["after_move"].get("deadline") != fallback["fallback_assertion_id"]:
        raise AssertionError("predicate move did not fall back to historical deadline head")
    if "renamed_deadline" not in fallback["after_move"]:
        raise AssertionError("predicate move did not create the new head")
    if "renamed_deadline" in fallback["after_delete"]:
        raise AssertionError("deleting the moved assertion left a stale renamed head")
    if not fallback["move_materialization_equal"] or not fallback["delete_materialization_equal"]:
        raise AssertionError("head move/delete lifecycle diverged from clean reconstruction")
    if not fallback["move_head_index_equal"] or not fallback["delete_head_index_equal"]:
        raise AssertionError("head move/delete lifecycle diverged from canonical head oracle")
    if not fallback["head_lookup_uses_index"] or not fallback["head_refresh_uses_index"]:
        raise AssertionError("head lifecycle access is not index-backed")

    out = {
        "experiment": "v0.14_subject_local_fanout_and_history",
        "discriminating_control": discriminating_control,
        "discriminating_fixed": discriminating_fixed,
        "history_depths": history_depths,
        "history_rows": history_rows,
        "predicate_counts": predicate_counts,
        "predicate_rows": predicate_rows,
        "global_cardinalities": global_cardinalities,
        "global_rows": global_rows,
        "head_fallback": fallback,
        "invariant": (
            "subject-wide profile reconstruction may scale with the live predicate set represented in the output, "
            "but current-state repair should not rescan historical assertion versions that cannot affect the current profile"
        ),
        "mechanism": (
            "maintain a transactional subject_predicate_heads index from each subject/predicate to its current assertion; "
            "profile rebuild reads one indexed head/current assertion per live predicate while canonical assertion mutation "
            "refreshes only the old/new predicate heads"
        ),
        "scope": (
            "single SQLite database; synthetic oracle assertions; evidence-update profile rebuild for scaling; controlled "
            "predicate move/delete fallback; head-index bootstrap/rebuild excluded from measured recovery; no claim of O(1) "
            "work in live predicate count P because profile output itself is subject-wide"
        ),
    }
    RESULTS_PATH.write_text(json.dumps(out, indent=2))
    print("SUBJECT_FANOUT_RESULTS_JSON")
    print(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    run()
