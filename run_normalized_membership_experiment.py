from __future__ import annotations

import json
from pathlib import Path

from simulator.compositional_profile import run_v015_compositional_case
from simulator.normalized_membership import (
    run_cross_version_case,
    run_v015_manifest_topology_control,
    run_v016_normalized_case,
    run_v016_normalized_topology_case,
    run_v016_read_protection_case,
)


ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "normalized_membership_results.json"


def _require_safe(row: dict) -> None:
    if not row["membership_equal"]:
        raise AssertionError("normalized membership diverged from current-head topology")
    if not row["materialization_equal"] or not row["all_derived_fresh"]:
        raise AssertionError("v0.16 derived materialization diverged from clean rebuild")
    if not row["head_index_equal"]:
        raise AssertionError("v0.16 head index drifted")
    if not row["full_assembly_equal"] or not row["partial_assembly_equal"]:
        raise AssertionError("v0.16 logical profile diverged from canonical oracle")
    if not row["membership_lookup_uses_index"] or not row["membership_enumeration_uses_index"]:
        raise AssertionError("normalized membership path is not index-backed")
    if row["recovery"]["queue_final"]["conflict"] != 0:
        raise AssertionError("v0.16 measurement encountered unexpected conflict")


def run() -> dict:
    cross = run_cross_version_case(
        entity_count=128, predicate_count=32, history_depth=8, changed_count=1
    )
    if not cross["equal"]:
        raise AssertionError("v0.16 full logical profile changed v0.15 semantics")

    predicate_counts = [1, 2, 4, 8, 16, 32, 64]
    predicate_rows = []
    for p in predicate_counts:
        control = run_v015_compositional_case(
            entity_count=128, predicate_count=p, history_depth=8, changed_count=1
        )
        fixed = run_v016_normalized_case(
            entity_count=128, predicate_count=p, history_depth=8, changed_count=1
        )
        _require_safe(fixed)
        if fixed["full_profile"] != control["full_profile"]:
            raise AssertionError(f"v0.16 logical profile mismatch at P={p}")
        row = {
            "predicate_count": p,
            "v015_manifest_bytes": control["profile_storage_bytes"],
            "v016_descriptor_bytes": fixed["descriptor_storage_bytes"],
            "membership_storage_bytes": fixed["membership_storage_bytes"],
            "membership_btree_height": fixed["membership_btree_height"],
            "maintenance_work": fixed["recovery"]["total_work"],
            "partial_payload_bytes": fixed["partial_assembly_trace"]["payload_bytes"],
            "partial_descriptor_bytes": fixed["partial_assembly_trace"]["descriptor_bytes"],
            "partial_membership_rows": fixed["partial_assembly_trace"]["membership_rows"],
            "partial_membership_bytes": fixed["partial_assembly_trace"]["membership_bytes"],
            "partial_facet_bytes": fixed["partial_assembly_trace"]["facet_bytes"],
            "partial_vm_steps": fixed["partial_assembly_trace"]["vm_steps"],
            "partial_row_page_units": fixed["partial_assembly_trace"]["row_page_units"],
            "full_payload_bytes": fixed["full_assembly_trace"]["payload_bytes"],
            "full_membership_rows": fixed["full_assembly_trace"]["membership_rows"],
            "full_membership_bytes": fixed["full_assembly_trace"]["membership_bytes"],
            "full_facet_bytes": fixed["full_assembly_trace"]["facet_bytes"],
            "full_vm_steps": fixed["full_assembly_trace"]["vm_steps"],
            "full_row_page_units": fixed["full_assembly_trace"]["row_page_units"],
        }
        predicate_rows.append(row)
        print("NORMALIZED_P", row)

    if len({row["v016_descriptor_bytes"] for row in predicate_rows}) != 1:
        raise AssertionError("v0.16 profile descriptor still grows with P")
    if len({row["partial_descriptor_bytes"] for row in predicate_rows}) != 1:
        raise AssertionError("selective read descriptor bytes still grow with P")
    if len({row["partial_membership_rows"] for row in predicate_rows}) != 1:
        raise AssertionError("K=1 selective membership row count still grows with P")
    if len({row["partial_membership_bytes"] for row in predicate_rows}) != 1:
        raise AssertionError("K=1 selective membership bytes still grow with P")
    if len({row["partial_facet_bytes"] for row in predicate_rows}) != 1:
        raise AssertionError("K=1 selective facet bytes unexpectedly grow with P")
    if len({row["partial_payload_bytes"] for row in predicate_rows}) != 1:
        raise AssertionError("K=1 returned SQL payload bytes still grow with P")
    if any(row["full_membership_rows"] != row["predicate_count"] for row in predicate_rows):
        raise AssertionError("full profile did not enumerate exactly P membership rows")
    full_bytes = [row["full_payload_bytes"] for row in predicate_rows]
    if any(b <= a for a, b in zip(full_bytes, full_bytes[1:])):
        raise AssertionError(f"full physical payload did not grow with P: {full_bytes}")
    manifests = [row["v015_manifest_bytes"] for row in predicate_rows]
    if any(b <= a for a, b in zip(manifests, manifests[1:])):
        raise AssertionError(f"v0.15 control manifest did not grow with P: {manifests}")

    changed_counts = [1, 2, 4, 8, 16]
    changed_rows = []
    for k in changed_counts:
        fixed = run_v016_normalized_case(
            entity_count=128, predicate_count=32, history_depth=8, changed_count=k
        )
        _require_safe(fixed)
        row = {
            "changed_count": k,
            "maintenance_work": fixed["recovery"]["total_work"],
            "partial_membership_rows": fixed["partial_assembly_trace"]["membership_rows"],
            "partial_membership_bytes": fixed["partial_assembly_trace"]["membership_bytes"],
            "partial_facet_bytes": fixed["partial_assembly_trace"]["facet_bytes"],
            "partial_payload_bytes": fixed["partial_assembly_trace"]["payload_bytes"],
            "partial_vm_steps": fixed["partial_assembly_trace"]["vm_steps"],
            "full_payload_bytes": fixed["full_assembly_trace"]["payload_bytes"],
        }
        changed_rows.append(row)
        print("NORMALIZED_K", row)
    if [row["partial_membership_rows"] for row in changed_rows] != changed_counts:
        raise AssertionError("selective membership probes did not scale exactly with K")
    maintenance = [row["maintenance_work"] for row in changed_rows]
    if any(b <= a for a, b in zip(maintenance, maintenance[1:])):
        raise AssertionError(f"maintenance did not grow with changed K: {maintenance}")
    partial_bytes = [row["partial_payload_bytes"] for row in changed_rows]
    if any(b <= a for a, b in zip(partial_bytes, partial_bytes[1:])):
        raise AssertionError(f"selective physical payload did not grow with K: {partial_bytes}")

    topology_predicates = [2, 4, 8, 16, 32, 64]
    topology_rows = []
    for p in topology_predicates:
        control = run_v015_manifest_topology_control(
            entity_count=128, predicate_count=p, history_depth=8
        )
        fixed = run_v016_normalized_topology_case(
            entity_count=128, predicate_count=p, history_depth=8
        )
        if not fixed["membership_equal"] or not fixed["materialization_equal"]:
            raise AssertionError(f"v0.16 topology parity failed at P={p}")
        if not fixed["head_index_equal"] or not fixed["all_derived_fresh"]:
            raise AssertionError(f"v0.16 topology safety failed at P={p}")
        if fixed["membership_after_add"] != p + 1 or fixed["membership_after_delete"] != p:
            raise AssertionError(f"v0.16 membership lifecycle failed at P={p}")
        row = {
            "predicate_count": p,
            "v015_add_work": control["add"]["total_work"],
            "v016_add_work": fixed["add"]["total_work"],
            "v015_delete_work": control["delete"]["total_work"],
            "v016_delete_work": fixed["delete"]["total_work"],
            "v015_profile_bytes_after_add": control["after_add_profile_bytes"],
            "v016_profile_bytes_after_add": fixed["after_add_profile_bytes"],
            "v016_membership_rows_written_add": fixed["add"]["membership_rows_written"],
            "v016_membership_bytes_written_add": fixed["add"]["membership_bytes_written"],
            "v016_membership_rows_written_delete": fixed["delete"]["membership_rows_written"],
            "v016_membership_bytes_written_delete": fixed["delete"]["membership_bytes_written"],
        }
        topology_rows.append(row)
        print("NORMALIZED_TOPOLOGY_P", row)

    control_add = [row["v015_add_work"] for row in topology_rows]
    if any(b <= a for a, b in zip(control_add, control_add[1:])):
        raise AssertionError(f"v0.15 topology control did not grow with P: {control_add}")
    if len({row["v016_add_work"] for row in topology_rows}) != 1:
        raise AssertionError("v0.16 predicate-addition work still depends on total P")
    if len({row["v016_delete_work"] for row in topology_rows}) != 1:
        raise AssertionError("v0.16 predicate-deletion work still depends on total P")
    if len({row["v016_profile_bytes_after_add"] for row in topology_rows}) != 1:
        raise AssertionError("v0.16 topology mutation still rewrites a P-sized profile")
    if len({row["v016_membership_bytes_written_add"] for row in topology_rows}) != 1:
        raise AssertionError("v0.16 membership delta bytes still depend on total P")

    history_rows = []
    for h in [1, 8, 64]:
        fixed = run_v016_normalized_case(
            entity_count=128, predicate_count=16, history_depth=h, changed_count=1
        )
        _require_safe(fixed)
        row = {
            "history_depth": h,
            "maintenance_work": fixed["recovery"]["total_work"],
            "partial_payload_bytes": fixed["partial_assembly_trace"]["payload_bytes"],
            "partial_vm_steps": fixed["partial_assembly_trace"]["vm_steps"],
        }
        history_rows.append(row)
        print("NORMALIZED_H", row)
    if len({row["maintenance_work"] for row in history_rows}) != 1:
        raise AssertionError("v0.16 maintenance regained H-dependence")
    if len({row["partial_payload_bytes"] for row in history_rows}) != 1:
        raise AssertionError("v0.16 selective returned bytes depend on H")

    global_rows = []
    for n in [100, 1_000, 10_000, 50_000]:
        fixed = run_v016_normalized_case(
            entity_count=n, predicate_count=16, history_depth=8, changed_count=1
        )
        _require_safe(fixed)
        row = {
            "entity_count": n,
            "maintenance_work": fixed["recovery"]["total_work"],
            "partial_payload_bytes": fixed["partial_assembly_trace"]["payload_bytes"],
            "partial_vm_steps": fixed["partial_assembly_trace"]["vm_steps"],
            "membership_btree_height": fixed["membership_btree_height"],
            "full_rebuild_work": fixed["full_rebuild_work"],
        }
        global_rows.append(row)
        print("NORMALIZED_N", row)
    if len({row["maintenance_work"] for row in global_rows}) != 1:
        raise AssertionError("v0.16 maintenance grew with unrelated N")
    if len({row["partial_payload_bytes"] for row in global_rows}) != 1:
        raise AssertionError("v0.16 selective returned bytes grew with unrelated N")

    read_safety = run_v016_read_protection_case(entity_count=48, index=24)
    if not all(read_safety.values()):
        raise AssertionError(f"v0.16 stale-read protection failed: {read_safety}")

    out = {
        "experiment": "v0.16_normalized_predicate_membership",
        "cross_version_equivalence": cross["equal"],
        "predicate_counts": predicate_counts,
        "predicate_rows": predicate_rows,
        "changed_counts": changed_counts,
        "changed_rows": changed_rows,
        "topology_predicates": topology_predicates,
        "topology_rows": topology_rows,
        "history_rows": history_rows,
        "global_rows": global_rows,
        "read_safety": read_safety,
        "invariant": (
            "selective reads and topology deltas must not load or rewrite an object whose serialized size grows with total P; "
            "full profile enumeration must remain honestly proportional to P"
        ),
        "mechanism": (
            "replace the serialized predicate manifest with a constant-size subject descriptor plus indexed normalized "
            "(subject,predicate) membership rows; validate K requested predicates by indexed probes and enumerate all P "
            "memberships only for a full profile"
        ),
        "measurement_scope": (
            "logical recovery work, exact SQL-returned payload bytes, returned-row page-size units, SQLite VM instruction "
            "counts, query-plan index checks, and dbstat membership-index height when available; these measurements do not "
            "claim direct operating-system or storage-device page-read counts"
        ),
    }
    RESULTS_PATH.write_text(json.dumps(out, indent=2))
    print("NORMALIZED_MEMBERSHIP_RESULTS_JSON")
    print(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    run()
