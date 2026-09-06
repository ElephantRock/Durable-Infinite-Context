# Durable Infinite Context — Minimum Falsifiable Prototype v0.13

This repository is a falsification-first research prototype for **Durable Infinite Context**: a system that can accumulate durable history without requiring lifetime history to fit in the model context window.

The working target is:

\[
C_t = F(q_t, M_t), \qquad |C_t| \le B
\]

while durable memory can continue to grow. The project is not attempting to prove that an actually infinite context window exists. It is testing whether bounded, task-relevant context can be reconstructed from indefinitely growing durable state with correct revision semantics and tractable operational cost.

## Governing research discipline

**Observe → Diagnose → Derive → Hypothesize → Predict → Test → Revise → Engineer**

Architecture is a surviving hypothesis, not the goal. Simpler mechanisms should replace unnecessary machinery; failed predictions revise the mechanism while the falsification target stays fixed.

## Current surviving architecture

The current prototype contains:

- canonical evidence records;
- revisable assertions with source lineage and correction/supersession relations;
- deterministic reconciliation with contested-state preservation;
- current/historical valid-time and knowledge-time queries;
- bounded rule-based context compilation;
- selective state materialization;
- multi-dimensional retrieval/addressability;
- non-oracle deterministic query planning over oracle assertions;
- scalable indexed candidate generation;
- incremental addressability maintenance;
- dependency-aware invalidation, selective reconstruction, and local retirement;
- explicit derived lifecycle states (`fresh`, `invalid`, `rebuilding`);
- SQLite WAL / `synchronous=FULL` recovery with real `SIGKILL` failpoints;
- indexed recursive dependency traversal;
- durable ordered multi-intent admission with optimistic canonical versions;
- snapshot-consistent derived reads;
- promotion-time revalidation of topology-dependent intent metadata;
- local creation of missing required outputs when canonical topology grows;
- subject-wide profile reconstruction when profile identity is subject-only, while state/support/context remain predicate-specific;
- machine-readable result ledgers with executable replay verifiers for hardened maintenance/recovery milestones;
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
+ MaterializationCompleteness
+ SemanticIdentityConsistency
}
\]

This decomposition remains provisional.

## What is deliberately **not** claimed

The included `evidence_recency_control` is a smoke-control heuristic. It is **not** the strong agentic hybrid-RAG baseline required by the research design. The repository deliberately does not fake an LLM agent.

The current evidence also does **not** establish:

- a production entity linker or semantic embedding model;
- real extraction accuracy (assertions remain oracle-provided in current planner/maintenance experiments);
- distributed, replicated, or multi-database consistency;
- hardware power-loss guarantees beyond the tested SQLite/storage stack;
- arbitrary multi-writer physical database execution (SQLite remains the physical single writer);
- production latency or dollar cost;
- cold/archive recovery;
- arbitrary ontology/schema migrations or predicate-type compatibility;
- bounded maintenance as one subject accumulates arbitrarily many predicates or deep per-predicate history;
- strong agentic-RAG superiority.

A genuine strong agentic retrieval baseline and real extraction remain mandatory before accepting the architecture as broadly superior.

## Milestone ledger

| Version | Falsification target | Main surviving result |
|---|---|---|
| v0.1 | Is persistent state semantically necessary? | No. Assertions-on-demand and persistent state tied under oracle retrieval; state only earned a prospective efficiency role. |
| v0.2 | Does materialized state earn write complexity? | Selective state materialization reduces current-state reconstruction when repeated reads justify maintenance cost. |
| v0.3 | Are similarity and one retrieval channel sufficient? | No. Identity and time are independent address dimensions; adaptive coverage prevents controlled premature closure. |
| v0.4 | Does addressability survive removal of the oracle query plan? | In controlled language, 200/200 resolvable cases matched the oracle plan and 60/60 irreducibly ambiguous cases abstained. |
| v0.5 | Can query resolution avoid O(N) subject scans? | Yes in the controlled benchmark after a noisy-alias failure forced a fragment address channel; indexed resolution stayed accurate through 50k entities. |
| v0.6 | Can address indexes be maintained locally? | Fixed-local mutations stayed roughly constant while rebuild work grew with total memory; shared evidence scaled with true fan-out. |
| v0.7 | Can multi-layer invalidation/rebuild remain local? | Yes for tested DAGs; later audit exposed and removed hidden whole-graph invalid-node discovery. |
| v0.8 | Can interrupted maintenance recover without stale reads? | Initial phase-only handling failed a torn-boundary test; idempotent canonical redo repaired the protocol. |
| v0.9 | Do crash invariants survive real persistence/process death? | SQLite WAL + `synchronous=FULL` passed 33 real-`SIGKILL` cases; fixed-region recovery stayed local through 50k entities. |
| v0.10 | Do multiple durable logical intents preserve conflict/recovery semantics? | Concurrent admission, explicit same-key conflict, local read protection, and fixed three-intent recovery passed; a snapshot race was found and fixed before merge. |
| v0.11 | Can admission-time impact metadata become stale after earlier topology changes? | Yes. Promotion-time topology revalidation closes the demonstrated stale-read leak while preserving local work. |
| v0.12 | Can canonical growth create outputs that do not yet exist? | Yes. Explicit missing-output obligations restore exact completeness when canonical truth moves to a previously unmaterialized subject. |
| v0.13 | Can a subject-only profile remain correct when predicates change/coexist? | v0.12 loses the profile after `deadline→launch_date`. Subject-wide indexed profile reconstruction restores exact parity, supports two live predicates and predicate removal, and stays independent of unrelated memory through 50k entities. |

Detailed narratives and machine-readable evidence live in `RESULTS_V0.*.md` and `*_results.json`.

## Selected validated measurements

### v0.5 scalable resolution

After revision, the controlled resolver reached 100% exact-plan accuracy and candidate recall on resolvable workloads through 50,000 entities, while irreducible ambiguity retained 100% abstention. At small universes indexed/fuzzy machinery can cost more than direct scanning, supporting **adaptive** rather than universal indexing.

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

under the benchmark logical-work definition. Fixed `depth=4`, `fanout=4` invalidation work remained 50 while total graph size grew from 400 to 40,000 derived nodes.

### v0.9 real process-crash recovery

The hardened matrix covers three mutation classes across eleven failpoints (**33 real SIGKILL cases**). In the strongest fixed-region case, recovery remained **28 logical operations** from 100 through 50,000 entities while full reconstruction grew from 1,387 to 699,987.

### v0.10 durable multi-intent concurrency

For the fixed three-intent crash/recovery workload:

| Entities | Intents | Recovery work | Full rebuild |
|---:|---:|---:|---:|
| 100 | 3 | **106** | 1,387 |
| 1,000 | 3 | **106** | 13,987 |
| 10,000 | 3 | **106** | 139,987 |
| 50,000 | 3 | **106** | 699,987 |

The experiment also verifies explicit same-key conflict/retry, concurrent process admission, real `SIGKILL` recovery with active + queued work, and snapshot-consistent local read protection.

### v0.11 topology-dependent intent revalidation

The v0.10 control queues a topology move followed by an evidence update that captures the old subject as its impact key. Executing the move first makes the admission-time key stale. v0.11 recomputes topology-derived read keys during promotion.

| Entities | Recovery work | Full rebuild |
|---:|---:|---:|
| 100 | **111** | 1,389 |
| 1,000 | **111** | 13,989 |
| 10,000 | **111** | 139,989 |
| 50,000 | **111** | 699,989 |

Supported field rule:

\[
AdmissionStable(x) \Leftarrow VersionGuardedBySameWriteKey(x)
\]

\[
PromotionRevalidated(x) \Leftarrow DependsOnMutableCrossRecordTopology(x)
\]

### v0.12 local topology-growth materialization

The v0.11 control can end with canonical truth moved to a new subject, every surviving node fresh, but zero target derived nodes. v0.12 explicitly derives missing output obligations and creates only absent outputs before repair.

| Entities | Recovery work | Full rebuild |
|---:|---:|---:|
| 100 | **76** | 1,400 |
| 1,000 | **76** | 14,000 |
| 10,000 | **76** | 140,000 |
| 50,000 | **76** | 700,000 |

The initial 72-operation artifact omitted four deterministic output-existence probes; the corrected 76 ledger includes them.

### v0.13 subject-wide predicate semantics

The v0.12 control demonstrates semantic-identity failure after `deadline → launch_date`: canonical and predicate-specific context are correct, all surviving nodes are fresh, but the subject profile is absent and clean-rebuild parity is false.

v0.13 reconstructs the subject-only profile from the actual live predicate set using indexed subject-local assertion access.

For predicate replacement:

| Entities | Recovery work | Full rebuild |
|---:|---:|---:|
| 100 | **70** | 1,400 |
| 1,000 | **70** | 14,000 |
| 10,000 | **70** | 140,000 |
| 50,000 | **70** | 700,000 |

At N=64:

- replacement yields profile `[launch_date]`, 4 derived rows, exact parity;
- addition yields profile `[deadline, launch_date]`, 7 derived rows, exact parity;
- removing `deadline` preserves profile `[launch_date]` and the remaining predicate context, returning to 4 derived rows.

The supported locality claim is independence from **unrelated entity cardinality for fixed subject-local predicate/history size**. It does not claim constant work as one subject's predicate/history fan-out grows.

See `RESULTS_V0.13.md`, `predicate_schema_results.json`, and `verify_predicate_schema_results.py`.

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
python verify_growth_results.py
python verify_predicate_schema_results.py
```

CI runs this chain on pull requests and uploads the v0.9–v0.13 evidence ledgers as artifacts.

## Current architectural hypothesis

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
  -> derive missing output obligations
  -> locally create required absent materializations
  -> rebuild nodes according to their semantic identity scope
       * subject profile: subject-wide
       * state/support/context: predicate-specific
  -> selective reconstruction / retirement
  -> crash-safe completion
```

Two distinctions remain central:

> Memory is durable state. Context is a bounded compiled artifact reconstructed for a task.

> Derived correctness requires freshness, completeness, and semantic consistency between a node's identity and its meaning.

Formally, the current surviving invariant is:

\[
\boxed{
DerivedCorrectness
= Freshness
+ MaterializationCompleteness
+ SemanticIdentityConsistency
}
\]

## Next falsification target — subject-local fan-out

v0.13 proves that subject-wide profile repair is independent of unrelated total-memory cardinality when the affected subject's predicate/history size is fixed. It does **not** establish bounded behavior as a single subject accumulates many live predicates or deep assertion history.

The next discriminating experiment should independently vary:

\[
P = \text{live predicate count on the affected subject}
\]

\[
H = \text{assertion history depth per predicate}
\]

and compare local maintenance with unrelated global cardinality held fixed and then varied independently.

The next question is:

\[
\boxed{
Can subject-local maintenance scale with the true changed local region without rescanning irrelevant subject history or global memory?
}
\]
