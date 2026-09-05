from __future__ import annotations

import json
from pathlib import Path
from statistics import mean

from benchmark.costs import assertions_on_demand_costed, persistent_state_costed
from benchmark.evaluator import build_store
from simulator.scaling import build_scaling_suite


ENTITY_COUNTS = [10, 100, 1000, 5000]
HISTORY_LEN = 16
SAMPLED_QUERIES = 10


def main():
    rows = []
    for entities in ENTITY_COUNTS:
        scenarios = build_scaling_suite(HISTORY_LEN, entities=entities)
        store = build_store(scenarios)
        queries = [next(q for q in s.queries if q.question_type == "current") for s in scenarios[:SAMPLED_QUERIES]]
        for name, fn in {
            "assertions_on_demand": assertions_on_demand_costed,
            "persistent_state": persistent_state_costed,
        }.items():
            results = [fn(store, q) for q in queries]
            rows.append({
                "entities": entities,
                "total_assertions": entities * HISTORY_LEN,
                "history_len": HISTORY_LEN,
                "architecture": name,
                "accuracy": mean(int(r.answer.value == q.expected_value) for r, q in zip(results, queries)),
                "mean_logical_reads": mean(r.cost.logical_reads for r in results),
                "mean_context_items": mean(r.cost.context_items for r in results),
            })
        print(f"entities={entities:5d} total_assertions={entities*HISTORY_LEN:7d}")

    Path("cardinality_results.json").write_text(json.dumps({"rows": rows}, indent=2))
    print("\nFixed 16-event relevant history while total memory grows")
    print("entities | assertions | on_demand_reads | persistent_reads")
    for entities in ENTITY_COUNTS:
        rr = [r for r in rows if r["entities"] == entities]
        vals = {r["architecture"]: r["mean_logical_reads"] for r in rr}
        print(f"{entities:8d} | {entities*HISTORY_LEN:10d} | {vals['assertions_on_demand']:15.2f} | {vals['persistent_state']:16.2f}")


if __name__ == "__main__":
    main()
