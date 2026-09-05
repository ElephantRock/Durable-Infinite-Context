from __future__ import annotations

import json
from pathlib import Path

from benchmark.costs import assertions_on_demand_costed, persistent_state_costed
from core.storage import MemoryStore
from simulator.scaling import build_scaling_suite
from state.incremental import MaintenanceCost, apply_incremental_current_state


HISTORY_LENGTHS = [2, 4, 8, 16, 32, 64, 128, 256, 512]
QUERY_COUNTS = [1, 2, 5, 10, 100]
MAINTENANCE_WEIGHTS = [1, 2, 5]


def build_incremental(scenarios):
    store = MemoryStore()
    cost = MaintenanceCost()
    for s in scenarios:
        rels_by_source = {}
        for r in s.relations:
            rels_by_source.setdefault(r.source_assertion_id, []).append(r)
        for e in s.evidence:
            store.add_evidence(e)
        for a in s.assertions:
            store.add_assertion(a)
            rs = rels_by_source.get(a.id, [])
            for r in rs:
                store.add_relation(r)
            cost.add(apply_incremental_current_state(store, a, rs))
    return store, cost


def main():
    rows = []
    for n in HISTORY_LENGTHS:
        scenarios = build_scaling_suite(n, entities=1)
        store, maintenance = build_incremental(scenarios)
        q = next(q for q in scenarios[0].queries if q.question_type == "current")
        ondemand = assertions_on_demand_costed(store, q).cost.logical_reads
        state_read = persistent_state_costed(store, q).cost.logical_reads
        for ww in MAINTENANCE_WEIGHTS:
            for qcount in QUERY_COUNTS:
                # Shared evidence/assertion/relation writes cancel in the comparison.
                # Only the extra state-maintenance path is charged to persistent state.
                state_total = ww * maintenance.logical_ops + qcount * state_read
                ondemand_total = qcount * ondemand
                rows.append({
                    "history_len": n,
                    "query_count": qcount,
                    "maintenance_weight": ww,
                    "ondemand_total": ondemand_total,
                    "persistent_total": state_total,
                    "persistent_wins": state_total < ondemand_total,
                    "maintenance_ops": maintenance.logical_ops,
                    "maintenance_fallbacks": maintenance.fallbacks,
                    "ondemand_read_ops": ondemand,
                    "persistent_read_ops": state_read,
                })
        print(f"n={n:3d} maintenance={maintenance.logical_ops:4d} fallbacks={maintenance.fallbacks} read A={ondemand:4d} S={state_read}")

    Path("tradeoff_results.json").write_text(json.dumps({"rows": rows}, indent=2))
    print("\nExact break-even query count (persistent total cost < on-demand total cost)")
    print("history | maintenance_weight=1 | maintenance_weight=2 | maintenance_weight=5")
    for n in HISTORY_LENGTHS:
        base = next(r for r in rows if r["history_len"] == n and r["maintenance_weight"] == 1 and r["query_count"] == 1)
        maint = base["maintenance_ops"]
        a_read = base["ondemand_read_ops"]
        s_read = base["persistent_read_ops"]
        cells = []
        for mw in MAINTENANCE_WEIGHTS:
            q = 1
            while mw * maint + q * s_read >= q * a_read:
                q += 1
            cells.append(str(q))
        print(f"{n:7d} | {cells[0]:20s} | {cells[1]:20s} | {cells[2]:20s}")



if __name__ == "__main__":
    main()
