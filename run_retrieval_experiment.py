from __future__ import annotations

import json
from pathlib import Path

from benchmark.evaluator import build_store
from benchmark.retrieval_metrics import RetrievalScore
from rag.retrieval import RetrievalIndex, Retriever
from simulator.retrieval import build_retrieval_suite, build_semantic_suite, build_temporal_suite

ROOT = Path(__file__).resolve().parent


def score_suite(scenarios, modes, budget):
    store = build_store(scenarios)
    retriever = Retriever(RetrievalIndex(store))
    scores = {m: RetrievalScore(m) for m in modes}
    for s in scenarios:
        for q in s.queries:
            for m in modes:
                ids, trace = retriever.search(q, mode=m, budget=budget)
                scores[m].add(q, ids, trace.candidates_considered)
    return [scores[m].to_dict() for m in modes]


def run():
    modes = ["semantic_only", "lexical_only", "hybrid_text", "planned_multi_address"]

    semantic_saturation = []
    for d in [0, 10, 100, 500, 1000]:
        rows = score_suite(build_semantic_suite(d, entities=20), modes, budget=4)
        semantic_saturation.append({"distractors_per_target": d, "budget": 4, "scores": rows})
        print("SEM", d, {r["mode"]: round(r["complete_support_rate"], 3) for r in rows})

    identity_collision = []
    for d in [0, 10, 100, 500, 1000]:
        rows = score_suite(build_retrieval_suite(d, entities=20), modes, budget=4)
        identity_collision.append({"distractors_per_target": d, "budget": 4, "scores": rows})
        print("ID", d, {r["mode"]: round(r["complete_support_rate"], 3) for r in rows})

    temporal = []
    for h in [4, 16, 64, 256]:
        rows = score_suite(build_temporal_suite(h, entities=20), modes, budget=4)
        temporal.append({"history_len": h, "budget": 4, "scores": rows})
        print("TIME", h, {r["mode"]: round(r["complete_support_rate"], 3) for r in rows})

    out = {
        "experiment": "v0.3_selective_addressability",
        "planner_mode": "oracle_resolved_entity_predicate_time",
        "extraction_mode": "oracle_assertions",
        "budget": 4,
        "semantic_saturation": semantic_saturation,
        "identity_collision": identity_collision,
        "temporal": temporal,
    }
    (ROOT / "retrieval_results.json").write_text(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    run()
