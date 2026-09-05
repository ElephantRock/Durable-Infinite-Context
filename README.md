# Durable Infinite Context — Minimum Falsifiable Prototype v0.5

This repository implements the falsification-first research prototype for the Durable Infinite Context architecture.

## What is implemented

- Canonical evidence records.
- Derived assertions with source lineage.
- Explicit `corrects` and `supersedes` relations.
- Deterministic state reconciliation.
- Current, historical-valid-time, and historical-knowledge-time queries.
- Contested-state preservation.
- Rule-based context compilation.
- Synthetic correction, transition, conflict, scaling, retrieval, planner, and query-resolution workloads.
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

The repository also contains:

- indexed per-key assertion access so on-demand reconciliation is not penalized by a full-store scan;
- logical read/context cost instrumentation (`benchmark/costs.py`);
- controlled long-history scenarios (`simulator/scaling.py`);
- an incremental current-state materializer with explicit fallback accounting (`state/incremental.py`);
- relevant-history scaling (`run_scaling_experiment.py`);
- read/write tradeoff analysis (`run_tradeoff_experiment.py`);
- total-memory cardinality scaling (`run_cardinality_experiment.py`);
- `RESULTS_V0.2.md` with interpretation and limitations.

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

Important limitation: the v0.3 planner receives oracle-resolved structured constraints. The benchmark therefore tests the value of multiple address dimensions once resolved; it does not claim natural-language entity resolution or planner reliability.

## v0.4 — Non-oracle query planning

v0.4 removes the oracle query plan while retaining oracle assertions. The deterministic planner receives only user-visible question text plus subject profiles derived from memory.

New components:

- `rag/planner.py`: identity, predicate, intent, and temporal plan inference with explicit ambiguity preservation;
- `rag/planned.py`: multi-address retrieval from inferred plans without reading hidden `QueryCase` identity/predicate/time fields;
- `simulator/planner.py`: unique-identity, contextual-collision, irreducible-ambiguity, and temporal workloads;
- `benchmark/planner_metrics.py`;
- `run_planner_experiment.py`;
- `RESULTS_V0.4.md` and `planner_results.json`.

The controlled benchmark contains 260 cases. All 200 resolvable cases matched the oracle plan and complete-support retrieval, while all 60 intentionally underdetermined identity cases abstained with zero over-resolution.

Important limitation: v0.4 scores every subject profile at query time. It validates the semantics of conditional hard-constraint formation, not scalable identity resolution.

## v0.5 — Scalable query resolution

v0.5 replaces the v0.4 full subject-profile scan with a rebuildable candidate-generation materialization:

- `rag/scalable_planner.py`: exact token, separator-fragment, and four-character n-gram postings with bounded profile scoring;
- `simulator/scalable_planner.py`: cardinality, typo/noise, contextual-disambiguation, and irreducible-ambiguity workloads;
- `benchmark/scalable_planner_metrics.py`: candidate recall and logical query-work instrumentation;
- `run_scalable_planner_experiment.py`;
- `RESULTS_V0.5.md` and `scalable_planner_results.json`.

The first v0.5 mechanism was partially falsified: noisy aliases/descriptors reached only 90% exact-plan accuracy and about 95% candidate recall at scale. The resolver was revised by adding a separator-fragment address channel while leaving the benchmark unchanged.

After revision, all resolvable workloads reached 100% exact-plan accuracy and 100% candidate recall through 50,000 entities. Irreducible ambiguity retained 100% abstention and 0% over-resolution.

At 50,000 entities:

- exact unique identity: 1 profile scored, 11 logical query operations on average;
- noisy unique identity: 30.45 profiles scored, 184.3 logical operations;
- exact contextual identity: 1 profile scored, 12 logical operations;
- noisy contextual identity: 30.45 profiles scored, 187.3 logical operations;
- irreducible ambiguity: 0 profiles scored, 9 logical operations.

The corresponding logical-work fractions relative to an N-profile scan are approximately 0.00022, 0.003686, 0.00024, 0.003746, and 0.00018.

A negative result is preserved: at only 100 entities, the indexed/fuzzy machinery can cost more than a direct scan. The evidence therefore supports **adaptive resolution** rather than universal indexing: small candidate universes may use direct scanning, while large universes use indexed candidate generation.

Run the current planner milestones with:

```bash
python -m unittest discover -s tests -v
python run_planner_experiment.py
python run_scalable_planner_experiment.py
```

Important limitations: assertions are still oracle-provided; the language is controlled and synthetic; index build/update cost is not yet tested; the resolver is not a production entity linker; and a genuine agentic-RAG baseline plus real extraction remain mandatory later comparisons.

The next falsification target is **incremental addressability maintenance**: determine whether these rebuildable query-resolution indexes can be kept correct under local evidence/assertion changes without update cost proportional to total memory.
