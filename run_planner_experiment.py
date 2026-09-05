from __future__ import annotations

import json
from pathlib import Path

from benchmark.evaluator import build_store
from benchmark.planner_metrics import PlannerScore
from rag.planned import PlannedRetriever
from rag.planner import QueryPlanner
from rag.retrieval import RetrievalIndex, Retriever
from simulator.planner import (
    build_ambiguous_suite,
    build_contextual_suite,
    build_planner_temporal_suite,
    build_unique_suite,
)

ROOT = Path(__file__).resolve().parent
BUDGET = 4


def score_cases(cases, label):
    store = build_store([c.scenario for c in cases])
    index = RetrievalIndex(store)
    planner = QueryPlanner(index)
    inferred = PlannedRetriever(index)
    oracle = Retriever(index)
    score = PlannerScore(label)

    for case in cases:
        q = case.query
        question = q.question_text or ""
        plan = planner.plan(question)
        inferred_ids, _ = inferred.search(
            query_id=q.id,
            question=question,
            plan=plan,
            budget=BUDGET,
        )
        oracle_ids, _ = oracle.search(q, mode="planned_multi_address", budget=BUDGET)
        score.add(case, plan, inferred_ids, oracle_ids)

    return score.to_dict()


def run():
    unique = []
    for d in [0, 10, 100]:
        row = score_cases(build_unique_suite(d, entities=20), f"unique_d{d}")
        unique.append({"distractors_per_target": d, "score": row})
        print("UNIQUE", d, round(row["exact_plan_rate"], 3), round(row["inferred_complete_support_rate"], 3))

    contextual = []
    for d in [1, 10, 100]:
        row = score_cases(build_contextual_suite(d, entities=20), f"context_d{d}")
        contextual.append({"distractors_per_target": d, "score": row})
        print("CONTEXT", d, round(row["exact_plan_rate"], 3), round(row["inferred_complete_support_rate"], 3))

    ambiguous = []
    for d in [1, 10, 100]:
        row = score_cases(build_ambiguous_suite(d, entities=20), f"ambiguous_d{d}")
        ambiguous.append({"distractors_per_target": d, "score": row})
        print("AMBIG", d, round(row["correct_abstention_rate"], 3), round(row["over_resolution_rate"], 3))

    temporal = []
    for h in [4, 16, 64, 256]:
        row = score_cases(build_planner_temporal_suite(h, entities=20), f"temporal_h{h}")
        temporal.append({"history_len": h, "score": row})
        print("TIME", h, round(row["exact_plan_rate"], 3), round(row["inferred_complete_support_rate"], 3))

    out = {
        "experiment": "v0.4_non_oracle_planner",
        "extraction_mode": "oracle_assertions",
        "planner_mode": "question_text_plus_memory_profiles",
        "retrieval_budget": BUDGET,
        "unique_identity": unique,
        "contextual_identity_collision": contextual,
        "irreducible_identity_collision": ambiguous,
        "temporal_resolution": temporal,
    }
    (ROOT / "planner_results.json").write_text(json.dumps(out, indent=2))
    print("PLANNER_RESULTS_JSON")
    print(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    run()
