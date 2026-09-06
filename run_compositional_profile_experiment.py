from __future__ import annotations

import json
from pathlib import Path

from simulator.compositional_profile import (
    run_composed_read_protection_case,
    run_manifest_topology_case,
    run_v014_monolithic_control,
    run_v015_compositional_case,
)


ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "compositional_profile_results.json"


def _require_fixed_safe(row: dict) -> None:
    if not row["materialization_equal"] or not row["all_derived_fresh"]:
        raise AssertionError("v0.15 materialization diverged from clean reconstruction")
    if not row["head_index_equal"]:
        raise AssertionError("v0.15 current-head index drifted")
    if not row["full_assembly_equal"] or not row["partial_assembly_equal"]:
        raise AssertionError("v0.15 composed profile differs from canonical oracle")
    if row["profile_has_embedded_evidence"]:
        raise AssertionError("v0.15 persisted manifest still embeds evidence payloads")
    if row["full_assembly_trace"]["facet_reads"] != row["predicate_count"]:
        raise AssertionError("full profile did not read exactly P facets")
    if row["partial_assembly_trace"]["facet_reads"] != row["changed_count"]:
        raise AssertionError("partial profile did not read exactly K facets")
    if row["recovery"]["queue_final"]["conflict"] != 0:
        raise AssertionError("v0.15 measurement encountered unexpected conflict")


def run() -> dict:
    control = run_v014_monolithic_control(
        entity_count=128, predicate_count=32, history_depth=8, changed_count=1
    )
    fixed = run_v015_compositional_case(
        entity_count=128, predicate_count=32, history_depth=8, changed_count=1
    )
    _require_fixed_safe(fixed)
    if not control["materialization_equal"] or not control["all_derived_fresh"]:
        raise AssertionError("v0.14 control is not semantically valid")
    if fixed["full_profile"] != control["persisted_profile"]:
        raise AssertionError("v0.15 full composed profile changed v0.14 logical semantics")
    if fixed["recovery"]["total_work"] >= control["recovery"]["total_work"]:
        raise AssertionError("v0.15 did not beat the monolithic P=32,K=1 control")

    predicate_counts = [1, 2, 4, 8, 16, 32, 64]
    predicate_rows = []
    for p in predicate_counts:
        monolithic = run_v014_monolithic_control(
            entity_count=128, predicate_count=p, history_depth=8, changed_count=1
        )
        compositional = run_v015_compositional_case(
            entity_count=128, predicate_count=p, history_depth=8, changed_count=1
        )
        _require_fixed_safe(compositional)
        if compositional["full_profile"] != monolithic["persisted_profile"]:
            raise AssertionError(f"logical profile mismatch at P={p}")
        row = {
            "predicate_count": p,
            "monolithic_work": monolithic["recovery"]["total_work"],
            "compositional_work": compositional["recovery"]["total_work"],
            "monolithic_profile_storage_bytes": monolithic["profile_storage_bytes"],
            "manifest_storage_bytes": compositional["profile_storage_bytes"],
            "partial_assembly_work": compositional["partial_assembly_trace"]["logical_work"],
            "full_assembly_work": compositional["full_assembly_trace"]["logical_work"],
            "partial_profile_bytes": compositional["partial_profile_bytes"],
            "full_profile_bytes": compositional["full_profile_bytes"],
            "full_rebuild_work": compositional["full_rebuild_work"],
        }
        predicate_rows.append(row)
        print("COMPOSITIONAL_P", row)

    monolithic_p = [row["monolithic_work"] for row in predicate_rows]
    if any(b <= a for a, b in zip(monolithic_p, monolithic_p[1:])):
        raise AssertionError(f"monolithic control did not grow with P: {monolithic_p}")
    if len({row["compositional_work"] for row in predicate_rows}) != 1:
        raise AssertionError("K=1 compositional maintenance still depends on total P")
    if len({row["partial_assembly_work"] for row in predicate_rows}) != 1:
        raise AssertionError("K=1 partial assembly still depends on total P")
    full_assembly = [row["full_assembly_work"] for row in predicate_rows]
    if any(b <= a for a, b in zip(full_assembly, full_assembly[1:])):
        raise AssertionError(f"full assembly did not grow with P: {full_assembly}")
    for row in predicate_rows:
        if row["predicate_count"] > 1 and row["manifest_storage_bytes"] >= row["monolithic_profile_storage_bytes"]:
            raise AssertionError("manifest did not reduce persisted profile payload size")

    changed_counts = [1, 2, 4, 8, 16]
    changed_rows = []
    for k in changed_counts:
        monolithic = run_v014_monolithic_control(
            entity_count=128, predicate_count=32, history_depth=8, changed_count=k
        )
        compositional = run_v015_compositional_case(
            entity_count=128, predicate_count=32, history_depth=8, changed_count=k
        )
        _require_fixed_safe(compositional)
        if compositional["full_profile"] != monolithic["persisted_profile"]:
            raise AssertionError(f"logical profile mismatch at K={k}")
        row = {
            "changed_count": k,
            "monolithic_work": monolithic["recovery"]["total_work"],
            "compositional_work": compositional["recovery"]["total_work"],
            "partial_assembly_work": compositional["partial_assembly_trace"]["logical_work"],
            "full_assembly_work": compositional["full_assembly_trace"]["logical_work"],
            "partial_profile_bytes": compositional["partial_profile_bytes"],
            "full_profile_bytes": compositional["full_profile_bytes"],
        }
        changed_rows.append(row)
        print("COMPOSITIONAL_K", row)

    comp_k = [row["compositional_work"] for row in changed_rows]
    if any(b <= a for a, b in zip(comp_k, comp_k[1:])):
        raise AssertionError(f"compositional maintenance did not grow with K: {comp_k}")
    partial_k = [row["partial_assembly_work"] for row in changed_rows]
    if any(b <= a for a, b in zip(partial_k, partial_k[1:])):
        raise AssertionError(f"partial assembly did not grow with K: {partial_k}")
    if len({row["full_assembly_work"] for row in changed_rows}) != 1:
        raise AssertionError("full assembly at fixed P unexpectedly depends on K")

    history_depths = [1, 8, 64]
    history_rows = []
    for h in history_depths:
        row = run_v015_compositional_case(
            entity_count=128, predicate_count=16, history_depth=h, changed_count=1
        )
        _require_fixed_safe(row)
        compact = {
            "history_depth": h,
            "maintenance_work": row["recovery"]["total_work"],
            "partial_assembly_work": row["partial_assembly_trace"]["logical_work"],
            "full_assembly_work": row["full_assembly_trace"]["logical_work"],
            "full_rebuild_work": row["full_rebuild_work"],
        }
        history_rows.append(compact)
        print("COMPOSITIONAL_H", compact)
    if len({row["maintenance_work"] for row in history_rows}) != 1:
        raise AssertionError("v0.15 maintenance regained H-dependence")
    if len({row["partial_assembly_work"] for row in history_rows}) != 1:
        raise AssertionError("v0.15 partial assembly depends on H")

    global_cardinalities = [100, 1_000, 10_000, 50_000]
    global_rows = []
    for n in global_cardinalities:
        row = run_v015_compositional_case(
            entity_count=n, predicate_count=16, history_depth=8, changed_count=1
        )
        _require_fixed_safe(row)
        compact = {
            "entity_count": n,
            "maintenance_work": row["recovery"]["total_work"],
            "partial_assembly_work": row["partial_assembly_trace"]["logical_work"],
            "full_assembly_work": row["full_assembly_trace"]["logical_work"],
            "full_rebuild_work": row["full_rebuild_work"],
        }
        global_rows.append(compact)
        print("COMPOSITIONAL_N", compact)
    if len({row["maintenance_work"] for row in global_rows}) != 1:
        raise AssertionError("v0.15 fixed local maintenance grew with unrelated N")
    if len({row["partial_assembly_work"] for row in global_rows}) != 1:
        raise AssertionError("v0.15 partial assembly grew with unrelated N")

    topology = run_manifest_topology_case(entity_count=64, index=30)
    if topology["before_predicates"] != ["deadline", "facet_001"]:
        raise AssertionError("manifest topology fixture is invalid")
    if topology["after_add_predicates"] != ["deadline", "facet_001", "facet_added"]:
        raise AssertionError("manifest did not add a new predicate")
    if topology["after_delete_predicates"] != ["deadline", "facet_001"]:
        raise AssertionError("manifest did not retire removed predicate")
    if not topology["add_materialization_equal"] or not topology["delete_materialization_equal"]:
        raise AssertionError("manifest topology lifecycle diverged from clean reconstruction")

    read_safety = run_composed_read_protection_case(entity_count=48, index=24)
    if not read_safety["unrelated_partial_present"]:
        raise AssertionError("unrelated profile facet was unnecessarily blocked")
    if not read_safety["affected_partial_blocked"] or not read_safety["full_profile_blocked"]:
        raise AssertionError("stale composed profile read was not blocked")
    if not read_safety["final_full_equal"] or not read_safety["materialization_equal"]:
        raise AssertionError("post-recovery composed profile diverged from oracle")

    out = {
        "experiment": "v0.15_compositional_profile_facets",
        "discriminating_control": control,
        "discriminating_fixed": fixed,
        "predicate_counts": predicate_counts,
        "predicate_rows": predicate_rows,
        "changed_counts": changed_counts,
        "changed_rows": changed_rows,
        "history_depths": history_depths,
        "history_rows": history_rows,
        "global_cardinalities": global_cardinalities,
        "global_rows": global_rows,
        "manifest_topology": topology,
        "read_safety": read_safety,
        "invariant": (
            "maintenance and task reconstruction should scale with the semantic subset actually changed or requested; "
            "a full P-facet profile still has an unavoidable O(P) assembly lower bound"
        ),
        "mechanism": (
            "persist profile(subject) as a predicate manifest; reuse predicate-specific support/context materializations as "
            "evidence-bearing facets; evidence/value changes repair only affected facets; assemble K requested facets or "
            "all P facets from one coherent SQLite snapshot"
        ),
        "scope": (
            "single SQLite database; synthetic oracle assertions; controlled evidence-update K sweep; manifest add/remove; "
            "full logical profile semantics matched against v0.14 and a canonical oracle; no claim that full P-sized output "
            "can be assembled in O(K) when K<P"
        ),
    }
    RESULTS_PATH.write_text(json.dumps(out, indent=2))
    print("COMPOSITIONAL_PROFILE_RESULTS_JSON")
    print(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    run()
