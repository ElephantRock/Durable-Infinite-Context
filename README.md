# Durable Infinite Context — Minimum Falsifiable Prototype v0.3

This repository implements the first oracle-controlled experiment for the Durable Infinite Context architecture.

## What is implemented

- Canonical evidence records.
- Derived assertions with source lineage.
- Explicit `corrects` and `supersedes` relations.
- Deterministic state reconciliation.
- Current, historical-valid-time, and historical-knowledge-time queries.
- Contested-state preservation.
- Rule-based context compilation.
- Synthetic correction, transition, and conflict timelines.
- Architecture-neutral evaluator and instrumentation-ready interfaces.
- A pluggable `AgenticRAGAdapter` integration seam.

## What is deliberately **not** claimed

The included `evidence_recency_control` is a smoke-control heuristic; it is **not** the strong agentic hybrid-RAG baseline specified by the research design. The repository deliberately does not fake an LLM agent. Plug a real agent into `rag.baselines.AgenticRAGAdapter` for that comparison.

## First experiment

The default run creates:

- 250 correction timelines,
- 250 transition timelines,
- 250 unresolved-conflict timelines,
- 2,500 deterministic query cases.

It compares:

1. `evidence_recency_control` — evidence/recency heuristic without reconciliation.
2. `assertions_on_demand` — reconcile assertions at query time.
3. `persistent_state` — materialize current state on write and descend to assertions for historical queries.

This first run tests semantic correctness only. It is not yet a retrieval benchmark.

## Run

From this directory:

```bash
python -m unittest discover -s tests -v
python run_experiment.py
```

## Interpretation

The most important first ablation is `assertions_on_demand` versus `persistent_state`.

If they have equal semantic accuracy under oracle retrieval, persistent state has not yet demonstrated a correctness advantage; its prospective value would instead be read efficiency, context compression, and incremental maintenance. Those require the next experiment with cost instrumentation and real retrieval.

A strong agentic RAG comparison remains mandatory before accepting the architecture.

## v0.2 — State materialization scaling experiment

The repository now also contains:

- indexed per-key assertion access so on-demand reconciliation is not penalized by a full-store scan;
- logical read/context cost instrumentation (`benchmark/costs.py`);
- controlled long-history scenarios (`simulator/scaling.py`);
- an incremental current-state materializer with explicit fallback accounting (`state/incremental.py`);
- relevant-history scaling (`run_scaling_experiment.py`);
- read/write tradeoff analysis (`run_tradeoff_experiment.py`);
- total-memory cardinality scaling (`run_cardinality_experiment.py`);
- `RESULTS_V0.2.md` with interpretation and limitations.

Run all current experiments with:

```bash
python -m unittest discover -s tests -v
python run_experiment.py
python run_scaling_experiment.py
python run_tradeoff_experiment.py
python run_cardinality_experiment.py
```

The v0.2 result narrows the hypothesis: persistent current state is not necessary for semantic correctness or bounded context under oracle retrieval; it earns value as a selective materialization for read-heavy evolving state.


## v0.3 — Selective addressability and coverage control

v0.3 adds real candidate retrieval while deliberately retaining oracle extraction and an oracle-resolved entity/predicate/time query plan. This isolates retrieval/addressability before planner and extraction errors are introduced.

New components:

- `rag/retrieval.py`: lexical, deterministic concept-semantic, hybrid-text, identity/predicate/time constrained retrieval, plus adaptive coverage control;
- `simulator/retrieval.py`: semantic-saturation, identity-collision, and temporal-disambiguation workloads;
- `benchmark/retrieval_metrics.py`: Recall@Budget-style support metrics and candidate-region instrumentation;
- `run_retrieval_experiment.py`;
- `run_coverage_experiment.py`;
- `RESULTS_V0.3.md`.

Run:

```bash
python -m unittest discover -s tests -v
python run_retrieval_experiment.py
python run_coverage_experiment.py
```

Important limitation: the v0.3 planner receives oracle-resolved structured constraints. The benchmark therefore tests the value of multiple address dimensions once resolved; it does not yet claim natural-language entity resolution or planner reliability.
