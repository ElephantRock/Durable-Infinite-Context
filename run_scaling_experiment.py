from __future__ import annotations

import json
from pathlib import Path
from statistics import mean

from benchmark.costs import assertions_on_demand_costed, persistent_state_costed
from benchmark.evaluator import build_store
from simulator.scaling import build_scaling_suite


HISTORY_LENGTHS = [1, 2, 4, 8, 16, 32, 64, 128, 256, 512]
ENTITIES = 100


def run_one(history_len: int) -> list[dict]:
    scenarios = build_scaling_suite(history_len, ENTITIES)
    store = build_store(scenarios)
    architectures = {
        "assertions_on_demand": assertions_on_demand_costed,
        "persistent_state": persistent_state_costed,
    }
    rows = []
    for qtype in ["current", "provenance", "historical"]:
        queries = [q for s in scenarios for q in s.queries if q.question_type == qtype]
        for name, fn in architectures.items():
            results = [fn(store, q) for q in queries]
            accuracy = mean(int(r.answer.value == q.expected_value and r.answer.status == q.expected_status) for r, q in zip(results, queries))
            rows.append({
                "history_len": history_len,
                "entities": ENTITIES,
                "query_type": qtype,
                "architecture": name,
                "accuracy": accuracy,
                "mean_logical_reads": mean(r.cost.logical_reads for r in results),
                "mean_state_reads": mean(r.cost.state_cells_read for r in results),
                "mean_assertion_reads": mean(r.cost.assertions_read for r in results),
                "mean_relation_reads": mean(r.cost.relation_edges_read for r in results),
                "mean_evidence_reads": mean(r.cost.evidence_records_read for r in results),
                "mean_context_items": mean(r.cost.context_items for r in results),
                "mean_context_chars": mean(r.cost.context_chars for r in results),
            })
    return rows


def main() -> None:
    all_rows: list[dict] = []
    for n in HISTORY_LENGTHS:
        all_rows.extend(run_one(n))
        print(f"completed history_len={n}")

    out = {
        "history_lengths": HISTORY_LENGTHS,
        "entities_per_scale": ENTITIES,
        "rows": all_rows,
    }
    Path("scaling_results.json").write_text(json.dumps(out, indent=2))

    print("\nCurrent-state logical reads")
    print("history | assertions_on_demand | persistent_state")
    for n in HISTORY_LENGTHS:
        subset = [r for r in all_rows if r["history_len"] == n and r["query_type"] == "current"]
        vals = {r["architecture"]: r["mean_logical_reads"] for r in subset}
        print(f"{n:7d} | {vals['assertions_on_demand']:20.2f} | {vals['persistent_state']:16.2f}")

    print("\nHistorical logical reads (expected tie in v0.1 state model)")
    print("history | assertions_on_demand | persistent_state")
    for n in HISTORY_LENGTHS:
        subset = [r for r in all_rows if r["history_len"] == n and r["query_type"] == "historical"]
        vals = {r["architecture"]: r["mean_logical_reads"] for r in subset}
        print(f"{n:7d} | {vals['assertions_on_demand']:20.2f} | {vals['persistent_state']:16.2f}")


if __name__ == "__main__":
    main()
