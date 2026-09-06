# v0.9 — Persistent WAL / real process-crash recovery

## Research question

v0.8 established local crash recovery over a deep-copied in-memory durable image, but it deliberately did not establish storage-engine atomicity or real process-crash durability.

v0.9 asks:

> Do the same no-stale-read and local-recovery invariants survive actual process death and restart when canonical records, derived materializations, dependency edges, and maintenance intent are persisted in a transactional store?

## Storage mechanism under test

SQLite is used as a falsification vehicle, not as a claim that SQLite is the final Durable Infinite Context storage architecture.

The controlled configuration is:

- SQLite `journal_mode=WAL`;
- `synchronous=FULL`;
- canonical evidence and assertions persisted in the database;
- derived nodes and lifecycle persisted in the database;
- dependency edges persisted with an index on `source_node`;
- maintenance intent persisted in the same database;
- one in-flight writer;
- worker process terminated with actual `SIGKILL`;
- recovery performed by a fresh Python process reopening the same database.

Pending journal intent blocks derived reads until recovery completes.

## Transactional refinement of v0.8

v0.8's simulated durability allowed the canonical mutation and the `CANONICAL_APPLIED` phase marker to tear independently. It therefore required defensive idempotent redo when either `PREPARED` or `CANONICAL_APPLIED` was recovered.

v0.9 puts the canonical mutation and phase advance in the **same SQLite transaction**. The falsifiable prediction is:

\[
\boxed{
Atomic(CanonicalMutation,PhaseAdvance)
\Rightarrow
CANONICAL\_APPLIED\ can\ be\ trusted
}
\]

Therefore:

- a crash before the canonical transaction commits must reopen at `PREPARED` with old canonical truth and recovery must apply the mutation exactly once;
- a crash after that transaction commits must reopen at `CANONICAL_APPLIED` with new canonical truth and recovery must perform zero redundant canonical mutations.

The experiment supports this prediction under the tested SQLite configuration.

## Falsification and audit history

The first real-process implementation passed its initial phase matrix, but it was not accepted as sufficient evidence. Audit found four experimental-validity weaknesses and one coverage gap:

1. **Open SQLite transaction lifetime.** The worker originally did not retain a strong reference to the connection returned for an uncommitted failpoint. That could permit ordinary connection cleanup/rollback before process death. The worker now retains the live connection and asserts `in_transaction` immediately before `SIGKILL`.
2. **Crash identity.** The harness originally accepted any non-zero worker exit. An ordinary Python exception could therefore masquerade as a crash. The hardened harness requires exactly `-SIGKILL`.
3. **Durability setting enforcement.** `synchronous=FULL` was recorded but not initially an acceptance assertion. It is now mandatory.
4. **Affected-region index guarantee.** The recursive dependency walk had an index available but did not explicitly guarantee the planner would use it. The operational query now uses `INDEXED BY idx_dependency_source`, and CI checks the recursive query plan with `EXPLAIN QUERY PLAN`.
5. **Incomplete transaction-boundary matrix.** The first matrix covered uncommitted canonical and invalidation writes but not uncommitted partial rebuild, repair, or finalization. Those failpoints were added before the result was frozen.

The benchmark criteria were tightened rather than relaxed.

## Final crash matrix

Three mutation classes are tested at `N=100`:

- evidence payload replacement;
- assertion object replacement;
- assertion deletion with derived-branch retirement.

Each mutation is killed at 11 boundaries:

1. `prepared_committed`
2. `canonical_uncommitted`
3. `canonical_committed`
4. `invalidation_uncommitted`
5. `invalidated_committed`
6. `partial_rebuild_uncommitted`
7. `partial_rebuild_committed`
8. `repair_uncommitted`
9. `repaired_committed`
10. `finalize_uncommitted`
11. `finalized_committed`

Thus the fixed matrix contains **33 actual SIGKILL cases**.

Every case requires:

- the expected durable phase after restart;
- the expected canonical value visibility;
- exact rollback of uncommitted lifecycle/materialization changes;
- read blocking whenever a journal intent remains;
- no read blocking after finalization has committed;
- exact materialization and dependency-edge parity with a clean reconstruction;
- semantic correctness;
- all surviving derived nodes fresh;
- empty journal after recovery;
- WAL mode and `synchronous=FULL`;
- index-backed recursive affected-region traversal.

All 33 hardened cases passed in GitHub Actions run `34019100562` on head `deb5ceac50f55ecef4d9d0cd766ead353b61e3ed`.

## Recovery work at N=100

| Operation | Prepared | Canonical uncommitted | Canonical committed | Invalidation uncommitted | Invalidated | Partial uncommitted | Partial committed | Repair uncommitted | Repaired | Finalize uncommitted | Finalized |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Evidence payload | 38 | 38 | 36 | 36 | 29 | 29 | 32 | 32 | 2 | 2 | 0 |
| Assertion object | 35 | 35 | 33 | 33 | 26 | 26 | 29 | 29 | 2 | 2 | 0 |
| Delete assertion | 35 | 35 | 33 | 33 | 24 | 24 | 28 | 28 | 2 | 2 | 0 |

The paired uncommitted failpoints are informative:

- `canonical_uncommitted` reopens exactly like `prepared_committed`;
- `invalidation_uncommitted` reopens exactly like `canonical_committed`;
- `partial_rebuild_uncommitted` reopens exactly like `invalidated_committed`;
- `repair_uncommitted` reopens exactly like `partial_rebuild_committed`;
- `finalize_uncommitted` reopens exactly like `repaired_committed`.

This is the expected behavior if SQLite transaction commit is the durable boundary.

Recovery performs one canonical mutation only from a durable `PREPARED` state. Every state at or after `CANONICAL_APPLIED` performs zero canonical redo. A fully committed finalization requires zero recovery work.

## Fixed-region recovery locality

The locality sweep uses assertion deletion interrupted after a committed partial rebuild. The crash image therefore contains four affected derived nodes, including one `REBUILDING` node. Recovery re-invalidates the four-node region, reconstructs/retire it locally, and compares the entire persistent derived/dependency state to a clean rebuild.

| Entities | Recovery work | Re-invalidated | Retired | Full-rebuild control | Fraction |
|---:|---:|---:|---:|---:|---:|
| 100 | 28 | 4 | 4 | 1,387 | 2.019% |
| 1,000 | 28 | 4 | 4 | 13,987 | 0.2002% |
| 10,000 | 28 | 4 | 4 | 139,987 | 0.02000% |
| 50,000 | 28 | 4 | 4 | 699,987 | 0.004000% |

For the fixed affected region:

\[
\boxed{RecoveryWork(N)=28}
\]

through 50,000 entities in the benchmark's logical-work accounting, while the reconstruction control grows approximately linearly with total persistent materialization size.

The operational affected-region query is an indexed recursive dependency traversal; CI verifies the actual query plan rather than merely checking that an index exists.

## What the evidence supports

Within the tested single-database, single-writer process-crash model:

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

For fixed affected-region complexity, measured recovery work is independent of unrelated total-memory cardinality through the tested 50,000-entity scale.

The v0.8 generic redo rule is therefore refined, not discarded:

- when the storage substrate cannot guarantee atomic coupling of canonical data and phase metadata, defensive idempotent redo remains necessary;
- when the storage transaction atomically commits both, a durable `CANONICAL_APPLIED` marker can be treated as evidence that the canonical mutation committed too.

## Reproducibility

`process_recovery_results.json` is the compact committed evidence ledger generated from GitHub Actions run `34019100562`.

`verify_process_recovery_results.py` reruns the complete executable experiment, requires exact row counts and unique keys, compares every recorded phase/locality metric, requires every safety/index boolean, and restores the committed compact ledger after the raw experiment output is generated.

CI gates v0.9 on that verifier.

## Important limitations

This result is materially stronger than v0.8, but it is not a universal durability claim.

It establishes only the tested conditions:

- one SQLite database;
- WAL mode;
- `synchronous=FULL`;
- one in-flight writer;
- local filesystem process termination via `SIGKILL`;
- controlled synthetic canonical data and oracle assertions.

It does **not** establish:

- sudden hardware power-loss durability beyond the storage engine's documented guarantees and underlying filesystem/device behavior;
- multiple concurrent writers;
- multiple queued or overlapping maintenance intents;
- conflicting transaction serialization semantics;
- distributed consensus or replica recovery;
- network partitions;
- cold/archive dependency traversal;
- authorization-aware recovery;
- real extraction/model-update cascades;
- production throughput, tail latency, or storage amplification.

## Next falsification target

The next highest-value unresolved maintenance-plane risk is **multiple intents / concurrent writers**.

v0.10 should test whether independent and conflicting writes can preserve serializable canonical truth, exact dependency invalidation, bounded read blocking, deterministic recovery ordering, and affected-region-local recovery under contention and crashes.
