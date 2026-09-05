from __future__ import annotations

import json
from pathlib import Path

from benchmark.evaluator import build_store
from rag.retrieval import RetrievalIndex, Retriever, coverage_satisfied
from simulator.world import build_suite

ROOT = Path(__file__).resolve().parent


def run():
    # Focus on cases where one item is not necessarily enough: relation classification
    # and contested-state queries.
    scenarios = build_suite(n_each=100)
    store = build_store(scenarios)
    retriever = Retriever(RetrievalIndex(store))

    rows = []
    fixed_success = 0
    adaptive_success = 0
    adaptive_rounds = 0
    adaptive_returned = 0
    cases = 0
    for s in scenarios:
        for q in s.queries:
            if q.question_type != "relation_classification" and q.expected_status.value != "contested":
                continue
            cases += 1
            fixed_ids, _ = retriever.search(q, mode="planned_multi_address", budget=1)
            fixed_ok = coverage_satisfied(store, q, fixed_ids)
            adaptive_ids, trace = retriever.adaptive_search(q, initial_budget=1, max_budget=8)
            adaptive_ok = bool(trace.coverage_satisfied)
            fixed_success += int(fixed_ok)
            adaptive_success += int(adaptive_ok)
            adaptive_rounds += trace.rounds
            adaptive_returned += len(adaptive_ids)
            if len(rows) < 20:
                rows.append({
                    "query_id": q.id,
                    "question_type": q.question_type,
                    "fixed_budget_1": fixed_ids,
                    "fixed_coverage": fixed_ok,
                    "adaptive_ids": adaptive_ids,
                    "adaptive_rounds": trace.rounds,
                    "adaptive_coverage": adaptive_ok,
                })

    out = {
        "experiment": "v0.3_coverage_control",
        "cases": cases,
        "fixed_budget_1_coverage_rate": fixed_success / max(cases, 1),
        "adaptive_coverage_rate": adaptive_success / max(cases, 1),
        "avg_adaptive_rounds": adaptive_rounds / max(cases, 1),
        "avg_adaptive_returned": adaptive_returned / max(cases, 1),
        "sample_traces": rows,
    }
    (ROOT / "coverage_results.json").write_text(json.dumps(out, indent=2))
    print(json.dumps({k: v for k, v in out.items() if k != "sample_traces"}, indent=2))
    return out


if __name__ == "__main__":
    run()
