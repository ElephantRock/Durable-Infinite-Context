# Milestone 1 Results

## Scope

This is an **oracle-controlled semantic experiment**, not yet the strong agentic-RAG comparison. It tests whether the evidence/assertion/reconciliation representation behaves correctly before retrieval and extraction noise are introduced.

The suite contains 750 independent timelines and 2,500 query cases covering:

- explicit correction,
- state transition/supersession,
- historical belief vs current belief about the past,
- unresolved contradiction,
- provenance.

## Result

See `results.json` and `experiment_output.txt` for the exact run.

The current result shows:

- `assertions_on_demand` reaches full value, epistemic-status, and correction/transition classification accuracy on the controlled suite.
- `persistent_state` matches it exactly on these semantic metrics.
- The simple evidence-recency control fails unresolved-conflict behavior and overclaims in 20% of all query cases.

## Interpretation

The experiment validates the deterministic reconciliation semantics implemented so far, but it **does not yet establish that persistent state is superior to on-demand assertion reconciliation**. Under oracle retrieval, both use the same underlying semantics and therefore tie on correctness.

That is an informative ablation result: the next test must measure whether materialized state earns its complexity through lower read/context cost, and then compare both against a real agentic hybrid-RAG baseline under identical evidence and budgets.

## Required next experiments

1. Add logical read/context-cost tracing for `assertions_on_demand` vs `persistent_state`.
2. Add distractors and real retrieval while keeping oracle assertions.
3. Plug a real LLM agent into `AgenticRAGAdapter`; do not substitute the included heuristic control.
4. Only after those tests, enable real assertion extraction.
