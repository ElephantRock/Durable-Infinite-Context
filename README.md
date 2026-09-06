# Durable Infinite Context — Minimum Falsifiable Prototype v0.9

This repository implements the falsification-first research prototype for the Durable Infinite Context architecture.

## What is implemented

- Canonical evidence records.
- Derived assertions with source lineage.
- Explicit `corrects` and `supersedes` relations.
- Deterministic state reconciliation.
- Current, historical-valid-time, and historical-knowledge-time queries.
- Contested-state preservation.
- Rule-based context compilation.
- Synthetic correction, transition, conflict, scaling, retrieval, planner, query-resolution, maintenance-locality, dependency-cascade, crash-recovery, and process-crash workloads.
- Explicit derived lifecycle states: `fresh`, `invalid`, and `rebuilding`.
- Dependency-aware selective reconstruction and local retirement of unreachable derived metadata.
- Scan-free affected-region discovery as the canonical cascade-maintenance path.
- A single-flight durable maintenance-intent prototype with idempotent canonical redo and stale-read blocking during recovery.
- SQLite WAL / `synchronous=FULL` persistence with actual `SIGKILL` failpoints and fresh-process recovery.
- Indexed recursive dependency traversal for persistent affected-region discovery.
- Architecture-neutral evaluator and instrumentation-ready interfaces.
- A pluggable `AgenticRAGAdapter` integration seam.

## What is deliberately **not** claimed

The included `evidence_recency_control` is a smoke-control heuristic; it is **not** the strong agentic hybrid-RAG baseline specified by the research design. The repository deliberately does not fake an LLM agent. Plug a real agent into `rag.baselines.AgenticRAGAdapter` for that comparison.

v0.9 establishes a controlled single-writer process-crash result over one SQLite database in WAL mode with `synchronous=FULL`. It does **not** establish hardware power-loss guarantees beyond the storage stack, concurrent-writer correctness, distributed consistency, replica recovery, cold/archive recovery, production storage latency, or real model-extraction maintenance.

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

Important limitations: assertions are still oracle-provided; the language is controlled and synthetic; the resolver is not a production entity linker; and a genuine agentic-RAG baseline plus real extraction remain mandatory later comparisons.

## v0.6 — Incremental addressability maintenance

v0.6 tests whether the v0.5 query-resolution materialization can remain correct under canonical mutations without a full-memory rebuild on every write.

New components and changes:

- `MemoryStore` subject-local assertion indexes and direct evidence→subject dependency reference counts;
- incrementally maintainable token/fragment/n-gram and predicate-specific postings in `SubjectProfileIndex`;
- lazy IDF computation to avoid global vocabulary maintenance after cardinality changes;
- `rag/maintenance.py`: affected-region discovery and incremental subject refresh;
- `simulator/maintenance.py`: controlled mutation worlds;
- `benchmark/maintenance_metrics.py`: incremental-vs-rebuild logical work and parity instrumentation;
- `run_maintenance_experiment.py`;
- `RESULTS_V0.6.md` and `maintenance_results.json`.

The benchmark covers insert, evidence payload/alias replacement, predicate change, assertion→evidence rebind, shared-evidence replacement, assertion deletion, and evidence deletion at 100, 1,000, 10,000, and 50,000 entities.

After every mutation, a fresh complete index is built as a correctness oracle. Every recorded v0.6 case achieved exact materialization equality and passed its semantic check.

At 50,000 entities:

- insert subject: 88 incremental operations versus 4,399,842 for rebuild;
- replace one evidence payload: 36 versus 4,399,840;
- change one predicate: 88 versus 4,399,840;
- rebind one assertion to new evidence: 40 versus 4,399,842;
- replace evidence shared by four subjects: 172 versus 4,400,038;
- delete one assertion: 79 versus 4,399,950;
- delete one evidence record: 81 versus 4,399,865.

The fixed-local operations remain essentially flat as total entity cardinality grows, while the shared-evidence update remains proportional to its fixed fan-out of four. The result supports:

\[
MaintenanceCost(\Delta M) \propto Size(TrueAffectedSubgraph(\Delta M))
\]

rather than a universal O(1) write claim.

Review hardening fixed hidden broad-posting materialization cost, deterministic same-sequence assertion ordering, and locality-index hydration for restored/pre-populated stores. The hardened CI run passed **38/38 unit tests** and reproduced the v0.4, v0.5, and v0.6 benchmark results. A requested second automated Codex review could not execute because the repository's code-review usage quota was exhausted; the available review findings were all addressed with regressions.

## v0.7 — Dependency-cascade invalidation

v0.7 extends the maintenance test from direct addressability updates to a multi-layer derived dependency path:

\[
Evidence/Assertion \rightarrow State/Profile \rightarrow Support \rightarrow Context
\]

New components:

- `state/dependencies.py`: reverse dependency traversal, explicit derived lifecycle, exact dependency-graph parity, and retirement accounting;
- `state/cascade.py`: integrated state/profile/support/context materialization with selective topological reconstruction;
- `simulator/cascade.py`: integrated cardinality worlds and synthetic depth × fan-out topology controls;
- `benchmark/cascade_metrics.py`;
- `run_cascade_experiment.py`;
- `RESULTS_V0.7.md` and `cascade_results.json`.

The first v0.7 mechanism passed answer-level correctness and locality, but inspection found a lifecycle defect: deletion left unreachable derived nodes behind as dependency-metadata tombstones. The benchmark was kept fixed while the mechanism was hardened to count dependency-edge removal, retire unreachable nodes locally, and include the entire dependency graph in rebuild-oracle equality.

A later v0.8 audit found a second issue in the v0.7 operational wrapper: affected-node discovery scanned the global invalid-node set before and after mutation even though traversal itself was local. The recorded v0.7 ledger reproduced through a scan-free replacement, and v0.8 promoted that replacement into the canonical `CascadeMaintainer` path.

At 50,000 entities:

- evidence payload replacement: 3 nodes invalidated/rebuilt, 60 incremental operations versus 5,149,752 for full reconstruction;
- object-only assertion replacement: 3 nodes, 21 versus 5,149,752;
- explicit correction: 4 nodes, 88 versus 5,149,786;
- shared evidence with fan-out four: 12 nodes, 270 versus 5,149,994;
- assertion deletion: 4 nodes rebuilt and then retired, 111 versus 5,149,891.

All integrated rows exactly matched a clean reconstruction, including dependency graph lineage/lifecycle metadata, passed semantic checks, and contained no remaining invalid nodes.

For fixed `depth=4`, `fanout=4`, synthetic invalidation work remained **50** while the total graph grew from 400 to 40,000 derived nodes. Across the controlled depth × fan-out sweep, the observed topology was exactly:

\[
AffectedNodes = F D
\]

\[
InvalidationWork = 2 + 3FD
\]

under the benchmark's logical-work definition.

## v0.8 — Interrupted maintenance / crash recovery

v0.8 adds a recovery coordinator and durable-intent state machine around canonical mutation and derived maintenance:

\[
Intent
\rightarrow CanonicalRedo
\rightarrow Invalidate
\rightarrow Rebuild/Retire
\rightarrow CommitMaintenance
\]

Key components:

- `state/recovery.py`: durable-intent phases, stale-read blocking, partial-`REBUILDING` recovery, and idempotent canonical redo;
- `simulator/recovery.py`: phase-boundary and cardinality recovery scenarios;
- `benchmark/recovery_metrics.py`;
- `run_recovery_experiment.py`;
- `verify_recovery_results.py`;
- `RESULTS_V0.8.md` and `recovery_results.json`.

The first recovery mechanism was falsified by an inverse torn-boundary test: the journal phase marker could say `CANONICAL_APPLIED` while the canonical write itself was absent. The mechanism was revised to redo canonical mutation idempotently for both `PREPARED` and `CANONICAL_APPLIED` recovery.

Redo safety is tested for evidence upsert, assertion upsert, and assertion deletion. The hardened unit surface reached **60/60 passing** before the recovery ledger was regenerated.

At N=100, redo-safe recovery work is:

| Operation | Prepared | Canonical applied | Invalidated | Rebuilding | Repaired |
|---|---:|---:|---:|---:|---:|
| Evidence payload | 67 | 66 | 53 | 36 | 3 |
| Assertion object | 28 | 27 | 16 | 26 | 3 |
| Delete assertion | 116 | 115 | 101 | 35 | 3 |

The strongest locality case interrupts deletion with one partial derived write left `REBUILDING`. Recovery remains **35 logical operations** while total entity cardinality grows from 100 to 50,000; clean reconstruction grows from 9,977 to 5,149,651 logical operations.

The surviving prototype-level invariant is:

\[
\boxed{
DurableIntent + IdempotentCanonicalRedo + RecordedAffectedRegion
\Rightarrow
NoStaleRead + LocalIdempotentRecovery
}
\]

The final CI verifier requires exact row counts, unique keys, exact ledger key sets, equality of every recorded measurement field, and all safety/correctness booleans true. v0.8 remains a simulated durable-image result; v0.9 tests the corresponding storage/process claim.

## v0.9 — Persistent WAL / real process-crash recovery

v0.9 persists canonical records, derived nodes, dependency edges, and maintenance intent in SQLite and kills a separate worker process with actual `SIGKILL` at transaction boundaries. A fresh process reopens the database and drains recovery.

The controlled storage configuration is SQLite WAL with `synchronous=FULL`. The canonical mutation and `CANONICAL_APPLIED` phase advance share one transaction, allowing the stronger storage substrate to refine v0.8's generic redo rule:

\[
\boxed{
Atomic(CanonicalMutation,PhaseAdvance)
\Rightarrow
CANONICAL\_APPLIED\ can\ be\ trusted
}
\]

The hardened matrix covers three mutation classes across eleven failpoints, for **33 real-SIGKILL cases**. Audit tightened the experiment to require a live open transaction at uncommitted failpoints, exact `-SIGKILL` exit status, mandatory WAL + `synchronous=FULL`, transaction coverage through finalization, and an `INDEXED BY` recursive affected-region walk whose query plan is checked in CI.

All 33 hardened cases passed on GitHub Actions run `34019100562`. At N=100, recovery work is:

| Operation | Prepared | Canonical uncommitted | Canonical committed | Invalidated | Partial committed | Repaired | Finalized |
|---|---:|---:|---:|---:|---:|---:|---:|
| Evidence payload | 38 | 38 | 36 | 29 | 32 | 2 | 0 |
| Assertion object | 35 | 35 | 33 | 26 | 29 | 2 | 0 |
| Delete assertion | 35 | 35 | 33 | 24 | 28 | 2 | 0 |

Uncommitted invalidation, partial-rebuild, repair, and finalization transactions reopen exactly at their preceding committed phases. Recovery performs one canonical mutation only from durable `PREPARED`; every state at or after `CANONICAL_APPLIED` performs zero redundant canonical writes.

The strongest locality case is assertion deletion after a committed partial rebuild. Recovery remains **28 logical operations** at 100, 1,000, 10,000, and 50,000 entities, while the full-rebuild control grows from 1,387 to 699,987.

The compact machine-readable evidence is `process_recovery_results.json`; `verify_process_recovery_results.py` reruns the entire matrix/locality sweep and requires exact recorded metrics plus all safety/index booleans.

The surviving v0.9 prototype-level invariant is:

\[
\boxed{
TransactionalIntent
+ AtomicCanonicalPhaseCommit
+ IndexedDependencyTraversal
+ LocalRepair
\Rightarrow
NoStaleRead + ExactProcessCrashRecovery
}
\]

Run the current planner, maintenance, cascade, and recovery milestones with:

```bash
python -m unittest discover -s tests -v
python run_planner_experiment.py
python run_scalable_planner_experiment.py
python run_maintenance_experiment.py
python verify_scanfree_cascade_results.py
python verify_recovery_results.py
python verify_process_recovery_results.py
```

## Next falsification target — v0.10 multiple intents / concurrent writers

The next unresolved maintenance-plane question is whether overlapping independent and conflicting mutations can preserve serializable canonical truth, exact dependency invalidation, bounded read blocking, deterministic recovery ordering, and affected-region-local recovery under contention and crashes.

The v0.10 experiment should begin with queued/serialized durable intents as the simplest control, then test same-key conflicts, independent subjects, evidence-update versus assertion-delete races, correction relations racing target replacement, crash recovery with multiple outstanding intents, writer lock contention, and whether read admission can safely narrow from a global maintenance block to affected subgraphs.
