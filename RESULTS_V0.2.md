# MFP v0.2 Results — State Materialization Cost and Scaling

## Scope

This stage tests the ablation that v0.1 left unresolved: `assertions_on_demand` and `persistent_state` had identical semantic correctness under oracle retrieval, so persistent state had not yet earned its complexity.

v0.2 therefore asks whether materialized current state provides a measurable operational advantage while preserving the same semantics.

These are **logical-operation experiments**, not production latency measurements. They deliberately keep oracle extraction and exact state-key addressability so that retrieval/entity-resolution noise does not confound the state-materialization question.

## Regression check

The original 750-timeline / 2,500-query semantic suite still passes unchanged:

- `assertions_on_demand`: 100% value, status, relation, provenance, and exact accuracy; 0% overclaim.
- `persistent_state`: identical semantic results.
- the simple evidence-recency smoke control remains at 80% value/status accuracy and 20% overclaim.

Persistent state therefore still has **no independent semantic-correctness advantage** in the oracle suite.

## Experiment A — Relevant-history depth

For each entity, the benchmark creates a linear sequence of explicit deadline transitions. It then queries current state, provenance, and historical state while increasing relevant history depth from 1 to 512 assertions.

### Current-state reads

| History length | Assertions on demand | Persistent state |
|---:|---:|---:|
| 1 | 1 | 1 |
| 2 | 3 | 1 |
| 4 | 7 | 1 |
| 8 | 15 | 1 |
| 16 | 31 | 1 |
| 32 | 63 | 1 |
| 64 | 127 | 1 |
| 128 | 255 | 1 |
| 256 | 511 | 1 |
| 512 | 1,023 | 1 |

The on-demand path reads `n` assertions and `n-1` assertion relations in this workload, hence `2n-1` logical reads. The persistent-current-state path reads one materialized state cell.

Both remain 100% accurate.

### Historical reads

Historical queries are intentionally **not materialized** in v0.2. Persistent state falls back to the assertion layer, so both architectures have identical read cost. At history length 512, both require 1,023 logical reads.

This is an important limitation rather than a failure: current-state materialization only helps the query classes it actually materializes.

### Context size

Both variants compile the answer before model inference. Therefore the working context remains bounded despite divergent reconstruction cost. At history length 512, a simple current-state answer is still one logical context item in both variants.

This falsifies a stronger claim that persistent state is required for bounded context. **On-demand deterministic reconciliation can also produce bounded context; persistent state primarily changes pre-inference reconstruction cost in this experiment.**

## Experiment B — Incremental maintenance

A new incremental current-state materializer was implemented and unit-tested on correction, transition, and unresolved-conflict scenarios.

For the linear transition workload, it uses the O(1) fast path on every update; no full-history reconciliation fallbacks occurred.

Logical maintenance operations grow linearly with writes:

| History length | Extra state-maintenance ops | Fallbacks |
|---:|---:|---:|
| 2 | 4 | 0 |
| 4 | 10 | 0 |
| 8 | 22 | 0 |
| 16 | 46 | 0 |
| 32 | 94 | 0 |
| 64 | 190 | 0 |
| 128 | 382 | 0 |
| 256 | 766 | 0 |
| 512 | 1,534 | 0 |

The prototype therefore does **not** shift an O(n) reconstruction scan onto every ordinary transition write in this controlled workload.

## Experiment C — Read/write tradeoff

Shared evidence/assertion/relation writes cancel in the comparison. The table below asks how many current-state queries are required before persistent state has lower total logical cost, under different relative weights assigned to state-maintenance operations.

| History length | Maintenance weight 1x | Maintenance weight 2x | Maintenance weight 5x |
|---:|---:|---:|---:|
| 2 | 3 | 5 | 11 |
| 4 | 2 | 4 | 9 |
| 8 | 2 | 4 | 8 |
| 16 | 2 | 4 | 8 |
| 32 | 2 | 4 | 8 |
| 64 | 2 | 4 | 8 |
| 128 | 2 | 4 | 8 |
| 256 | 2 | 4 | 8 |
| 512 | 2 | 4 | 8 |

This is a **logical cost model**, not a latency or dollar-cost claim. It demonstrates that materialization is workload-dependent: read-heavy current-state workloads can amortize state maintenance quickly, while write-heavy or rarely queried state may not justify persistent materialization.

## Experiment D — Total-memory cardinality

To separate relevant-history depth from total lifetime memory, the benchmark fixes each queried entity at a 16-event history while increasing the total store size by adding unrelated entities.

| Entities | Total assertions | Assertions-on-demand reads | Persistent-state reads |
|---:|---:|---:|---:|
| 10 | 160 | 31 | 1 |
| 100 | 1,600 | 31 | 1 |
| 1,000 | 16,000 | 31 | 1 |
| 5,000 | 80,000 | 31 | 1 |

Under exact key addressability, logical read cost is independent of unrelated lifetime cardinality. This supports the scaling invariant that total retained history need not leak into the hot path once the correct state key is known.

It does **not** yet validate semantic retrieval, identity resolution, or query planning. Those remain separate hypotheses.

## Revision to the architecture hypothesis

The v0.2 evidence supports a narrower claim than the original architecture:

> Persistent current-state materialization is not required for semantic correctness or bounded model context when assertions can be reconciled on demand. Its demonstrated value is amortized reconstruction efficiency for repeatedly queried evolving state.

Therefore persistent state should not be universal by default. A better current hypothesis is **selective materialization**:

- keep canonical evidence and assertions as the general durable substrate;
- materialize state for predicates/entities with sufficient read frequency, history depth, reconciliation cost, or latency sensitivity;
- compute rarely used state on demand;
- preserve assertion/evidence descent for historical and provenance-sensitive questions.

## What remains unproven

1. A real agentic hybrid-RAG baseline has not yet been run. The existing `AgenticRAGAdapter` remains an integration seam, not a simulated result.
2. Retrieval is still oracle-keyed. Semantic retrieval, entity resolution, distractor saturation, and coverage control remain untested.
3. Logical operation counts are not production latency measurements.
4. The incremental state fast path has only been tested on the controlled v0.2 semantic families, not arbitrary complex contested-state evolution.
5. Real assertion extraction remains disabled in these experiments.

## Next discriminating experiment

The next test should hold oracle assertions fixed but remove oracle retrieval:

1. add natural-language query surfaces;
2. introduce lexical/semantic/temporal candidate generation and distractor saturation;
3. measure `Recall@Budget`, candidate burden, and premature closure;
4. then plug a genuine LLM-driven agent into `AgenticRAGAdapter` under the same evidence and budgets.

Only after that comparison should selective persistent state be accepted as part of the production architecture.
