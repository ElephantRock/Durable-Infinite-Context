from __future__ import annotations

import json
from pathlib import Path

from benchmark.evaluator import build_store
from benchmark.scalable_planner_metrics import ScalablePlannerScore
from rag.scalable_planner import ScalableQueryPlanner, SubjectProfileIndex
from simulator.scalable_planner import (
    ScalablePlannerSuite,
    build_ambiguous_cardinality_suite,
    build_contextual_cardinality_suite,
    build_unique_cardinality_suite,
)

ROOT = Path(__file__).resolve().parent


def score_suite(suite: ScalablePlannerSuite) -> dict:
    store = build_store([suite.scenario])
    planner = ScalableQueryPlanner(SubjectProfileIndex(store))
    score = ScalablePlannerScore(suite.label, suite.entity_count)
    for case in suite.cases:
        q = case.query
        plan, trace = planner.plan_with_trace(q.question_text or "")
        score.add(q, plan, trace, should_abstain=case.should_abstain)
    return score.to_dict()


def run():
    cardinalities = [100, 1_000, 10_000, 50_000]
    out = {
        "experiment": "v0.5_scalable_query_resolution",
        "extraction_mode": "oracle_assertions",
        "candidate_limit": 32,
        "broad_posting_limit": 128,
        "cardinalities": cardinalities,
        "unique_exact": [],
        "unique_noisy_alias": [],
        "contextual_exact": [],
        "contextual_noisy_descriptor": [],
        "irreducible_ambiguity": [],
    }

    for n in cardinalities:
        rows = {
            "unique_exact": score_suite(build_unique_cardinality_suite(n, queries=20, noisy_alias=False)),
            "unique_noisy_alias": score_suite(build_unique_cardinality_suite(n, queries=20, noisy_alias=True)),
            "contextual_exact": score_suite(build_contextual_cardinality_suite(n, queries=20, noisy_descriptor=False)),
            "contextual_noisy_descriptor": score_suite(build_contextual_cardinality_suite(n, queries=20, noisy_descriptor=True)),
            "irreducible_ambiguity": score_suite(build_ambiguous_cardinality_suite(n, queries=20)),
        }
        for key, row in rows.items():
            out[key].append(row)
        print(
            "SCALE",
            n,
            {
                key: {
                    "plan": round(row["exact_plan_rate"], 3),
                    "cand_recall": None if row["candidate_recall_rate"] is None else round(row["candidate_recall_rate"], 3),
                    "abstain": None if row["correct_abstention_rate"] is None else round(row["correct_abstention_rate"], 3),
                    "profiles": round(row["avg_profiles_scored"], 2),
                    "work": round(row["avg_logical_work"], 2),
                    "work_fraction": round(row["logical_work_fraction_vs_full_scan"], 6),
                }
                for key, row in rows.items()
            },
        )

    (ROOT / "scalable_planner_results.json").write_text(json.dumps(out, indent=2))
    print("SCALABLE_PLANNER_RESULTS_JSON")
    print(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    run()
