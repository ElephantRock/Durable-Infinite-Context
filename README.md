# Durable Infinite Context — Minimum Falsifiable Prototype v0.11

This repository is a falsification-first research prototype for **Durable Infinite Context**: a system that can accumulate durable history without requiring lifetime history to fit in the model context window.

The working target is:

\[
C_t = F(q_t, M_t), \qquad |C_t| \le B
\]

while durable memory can continue to grow. The project is not attempting to prove that an actually infinite context window exists. It is testing whether bounded, task-relevant context can be reconstructed from indefinitely growing durable state with correct revision semantics and tractable operational cost.

## Governing research discipline

The project follows:

**Observe → Diagnose → Derive → Hypothesize → Predict → Test → Revise → Engineer**

Architecture is treated as a surviving hypothesis, not as the goal. If a simpler mechanism matches the evidence, complexity should be removed. If an experiment contradicts the mechanism, the mechanism is revised while the falsification target remains fixed.

## Current surviving architecture

The current prototype contains:

- canonical evidence records;
- derived assertions with source lineage;
- explicit correction/supersession relations;
- deterministic reconciliation with contested-state preservation;
- current and historical valid/knowledge-time queries;
- bounded rule-based context compilation;
- selective state materialization;
- multi-dimensional retrieval/addressability;
- non-oracle deterministic query planning over oracle assertions;
- scalable indexed candidate generation;
- incremental addressability maintenance;
- dependency-aware invalidation, selective reconstruction, and local retirement;
- explicit derived lifecycle states (`fresh`, `invalid`, `rebuilding`);
- crash-safe maintenance intent handling;
- SQLite WAL / `synchronous=FULL` persistent recovery with actual `SIGKILL` failpoints;
- indexed recursive dependency traversal;
- durable ordered multi-intent admission with optimistic canonical versions;
- snapshot-consistent derived reads;
- promotion-time revalidation of topology-dependent intent impact metadata;
- machine-readable result ledgers with executable replay verifiers for the hardened maintenance/recovery milestones;
- a pluggable `AgenticRAGAdapter` seam for the still-required strong baseline comparison.

A useful current decomposition is:

\[
\boxed{
Evidence
+ Assertions
+ SelectiveState
+ Addressability
+ Coverage
+ BoundedCompilation
+ LocalMaintenance
+ DurableRecovery
+ OrderedIntentAdmission
+ TopologyRevalidation
}
\]

This decomposition is provisional and remains subject to falsification.

## What is deliberately **not** claimed

The included `evidence_recency_control` is a smoke-control heuristic. It is **not** the strong agentic hybrid-RAG baseline required by the research design. The repository deliberately does not fake an LLM agent.

The current evidence also does **not** establish:

- a production entity linker or production semantic embedding model;
- real model extraction accuracy (assertions remain oracle-provided in the current planner/maintenance experiments);
- distributed or multi-database consistency;
- replica recovery;
- hardware power-loss guarantees beyond the tested SQLite/storage stack;
- arbitrary multi-writer database execution (SQLite remains the physical single writer);
- production latency/cost performance;
- cold/archive recovery;
- arbitrary topology growth into previously nonexistent derived materializations;
- a strong agentic-RAG superiority result.

A genuine strong agentic retrieval baseline and real extraction remain mandatory before accepting the architecture as broadly superior.

## Milestone ledger

| Version | Falsification target | Main surviving result |
|---|---|---|
| v0.1 | Is persistent state semantically necessary? | No. Assertions-on-demand and persistent state tied under oracle retrieval; state only earned a prospective efficiency role. |
| v0.2 | Does materialized state earn write complexity? | Selective state materialization can reduce current-state reconstruction from history-depth-dependent work to one state read. |
| v0.3 | Are semantic similarity and one retrieval channel sufficient? | No. Identity and time are independent address dimensions; adaptive coverage prevents controlled premature closure. |
| v0.4 | Does addressability survive removal of the oracle query plan? | In controlled language, 200/200 resolvable cases matched the oracle plan and 60/60 irreducibly ambiguous cases abstained. |
| v0.5 | Can query resolution avoid O(N) subject scans? | Yes in the controlled benchmark after a noisy-alias failure forced a separator-fragment address channel; indexed resolution remained accurate through 50k entities. |
| v0.6 | Can address indexes be maintained locally? | Fixed-local mutations stayed roughly constant while full rebuild work grew with total memory; shared evidence scaled with true fan-out. |
| v0.7 | Can multi-layer invalidation/rebuild remain local? | Yes for the tested dependency DAGs; later audit exposed and removed hidden whole-graph invalid-node discovery. |
| v0.8 | Can interrupted maintenance recover without stale reads? | Initial phase-only mechanism failed a torn-boundary test; idempotent canonical redo repaired the protocol. |
| v0.9 | Do the crash invariants survive real persistence/process death? | SQLite WAL + `synchronous=FULL` passed 33 real-`SIGKILL` cases; fixed-region recovery stayed local through 50k entities. |
| v0.10 | Do multiple durable logical intents preserve conflict/recovery semantics? | Concurrent admission, explicit same-key conflict, local read protection, and fixed three-intent recovery passed; a snapshot race was found and fixed before merge. |
| v0.11 | Can admission-time impact metadata become stale after earlier topology changes? | Yes—the v0.10 control leaked a stale read. Promotion-time topology revalidation closes that leak while preserving local work in the controlled sweep. |

Detailed narratives and machine-readable results live in the versioned `RESULTS_V0.*.md` and `*_results.json` files.

## Selected validated measurements

### v0.5 scalable resolution

After revision, the controlled resolver reached 100% exact-plan accuracy and candidate recall on resolvable workloads through 50,000 entities, while irreducible ambiguity retained 100% abstention. A negative result is preserved: at small entity universes the indexed/fuzzy machinery can cost more than direct scanning, supporting **adaptive** rather than universal indexing.

### v0.6 local index maintenance

At 50,000 entities, representative incremental logical work versus rebuild was:

- evidence payload replacement: 36 vs ~4.40M;
- predicate change: 88 vs ~4.40M;
- evidence shared by four subjects: 172 vs ~4.40M.

The supported claim is locality to the true affected region, not universal O(1) writes.

### v0.7 dependency cascade

For the synthetic topology control:

\[
AffectedNodes = FD
\]

\[
InvalidationWork = 2 + 3FD
\]

under the benchmark's logical-work definition. Fixed `depth=4`, `fanout=4` invalidation work remained 50 while the total graph grew from 400 to 40,000 derived nodes.

### v0.9 real process-crash recovery

The hardened matrix covers three mutation classes across eleven failpoints (**33 real SIGKILL cases**). In the strongest fixed-region locality case, recovery remained **28 logical operations** from 100 through 50,000 entities, while the full reconstruction control grew from 1,387 to 699,987.

### v0.10 durable multi-intent concurrency

For the fixed three-intent crash/recovery workload:

| Entities | Intents | Total logical recovery work | Full rebuild |
|---:|---:|---:|---:|
| 100 | 3 | **106** | 1,387 |
| 1,000 | 3 | **106** | 13,987 |
| 10,000 | 3 | **106** | 139,987 |
| 50,000 | 3 | **106** | 699,987 |

The experiment also verifies explicit same-key conflict/retry rather than silent lost update, real concurrent process admission, real `SIGKILL` recovery with active + queued work, and snapshot-consistent local read protection.

### v0.11 topology-dependent intent revalidation

The v0.10 control deliberately reproduces this failure:

1. queue an assertion subject move;
2. queue an evidence update that captures the old subject as its read-impact key;
3. execute the move first;
4. promote the evidence update;
5. the old admission-time key fails to protect the new subject while derived state is stale.

v0.11 recomputes topology-derived read keys during promotion, after all earlier intents and inside the transaction that installs the active maintenance journal.

The corrected fixed two-intent workload produced:

| Entities | Total recovery work | Full rebuild |
|---:|---:|---:|
| 100 | **111** | 1,389 |
| 1,000 | **111** | 13,989 |
| 10,000 | **111** | 139,989 |
| 50,000 | **111** | 699,989 |

The field-classification rule supported by the current operation set is:

\[
AdmissionStable(x) \Leftarrow VersionGuardedBySameWriteKey(x)
\]

\[
PromotionRevalidated(x) \Leftarrow DependsOnMutableCrossRecordTopology(x)
\]

Accordingly, same-write-key `previous_json` snapshots are protected by optimistic version conflict, while topology-derived `read_keys` are recomputed at promotion.

See `RESULTS_V0.11.md`, `topology_results.json`, and `verify_topology_results.py`.

## Reproducing the hardened path

```bash
python -m unittest discover -s tests -v
python run_planner_experiment.py
python run_scalable_planner_experiment.py
python run_maintenance_experiment.py
python verify_scanfree_cascade_results.py
python verify_recovery_results.py
python verify_process_recovery_results.py
python verify_multi_intent_results.py
python verify_topology_results.py
```

CI runs this chain on pull requests and uploads the v0.9, v0.10, and v0.11 evidence ledgers as artifacts.

## Current architectural hypothesis

The project currently favors:

```text
Canonical evidence
  -> revisable assertions
  -> selectively materialized state / derived views

Question
  -> ambiguity-aware resolution
  -> indexed candidate generation
  -> hard constraints only when justified
  -> coverage-controlled retrieval
  -> bounded context compilation

Canonical mutation
  -> durable ordered intent
  -> write-key conflict validation
  -> promotion-time topology-impact revalidation
  -> local dependency invalidation
  -> selective reconstruction / retirement
  -> crash-safe completion
```

The important distinction remains:

> Memory is durable state. Context is a bounded compiled artifact reconstructed for a task.

## Next falsification target — topology growth / missing derived-node creation

v0.11 repairs stale impact metadata when existing topology changes. It does not yet establish that canonical topology can grow into regions for which no derived materialization exists.

The next high-value counterexample is therefore:

\[
\boxed{
CanonicalTopologyGrowth \Rightarrow DerivedNodeCreation\ ?
}
\]

A move or insertion into a genuinely new subject/predicate may produce seed node IDs that do not yet exist in `derived_nodes`. The next experiment should determine whether the current recovery path silently leaves canonical truth without a corresponding state/profile/support/context materialization, and if so whether missing nodes can be synthesized locally without a full reconstruction.
