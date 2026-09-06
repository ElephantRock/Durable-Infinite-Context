# v0.8 — Interrupted maintenance / crash-recovery results

## Research question

v0.7 established local multi-layer invalidation/rebuild/retirement in-process, subject to the affected-region discovery correction recorded in `RESULTS_V0.7_CORRECTION.md`.

v0.8 asks:

> Can a canonical mutation and its derived maintenance recover after interruption at arbitrary maintenance phases without serving stale derived context or requiring a global rebuild?

The prototype tests a **durable maintenance intent** written before canonical mutation. A process restart must drain pending maintenance before derived reads are admitted.

## Mechanism under test

The single-flight state machine is:

\[
Prepared \rightarrow CanonicalApplied \rightarrow Invalidated \rightarrow Rebuilding \rightarrow Repaired \rightarrow Finalized
\]

The durable intent records the operation and, once invalidation occurs, the exact affected derived node IDs emitted by dependency traversal.

Safety rule:

\[
PendingMaintenance \Rightarrow DerivedReadsBlocked
\]

A crash at `REBUILDING` treats the partially written derived node as untrusted. Recovery re-invalidates only the intent's recorded region and reconstructs that region idempotently from canonical state.

## Falsification matrix

At N=100, three mutation classes were interrupted after five phases:

- replace evidence payload;
- replace assertion object;
- delete assertion and retire its derived branch.

Crash phases:

1. `prepared` — intent durable, canonical mutation not yet applied;
2. `canonical_applied` — canonical truth changed, descendants not yet invalidated;
3. `invalidated` — affected derived nodes invalid;
4. `rebuilding` — one partial derived write durably left `REBUILDING`;
5. `repaired` — local repair complete, journal finalization pending.

Every row required:

- stale derived reads blocked before recovery;
- exact state/profile/support/context/dependency-graph parity with a clean reconstruction;
- semantic correctness;
- all surviving derived nodes fresh;
- empty maintenance journal after recovery.

All 15 phase/operation combinations passed.

## Recovery work at N=100

| Operation | Prepared | Canonical applied | Invalidated | Rebuilding | Repaired |
|---|---:|---:|---:|---:|---:|
| Evidence payload | 67 | 65 | 53 | 36 | 3 |
| Assertion object | 28 | 26 | 16 | 26 | 3 |
| Delete assertion | 116 | 114 | 101 | 35 | 3 |

`prepared` recovery includes one canonical mutation. `canonical_applied` and later phases correctly perform zero canonical mutations during restart.

The `repaired` phase requires only journal finalization in the current trace: 3 logical operations and zero derived rebuilds.

## Partial-REBUILDING recovery locality

The strongest tested locality case interrupts assertion deletion after one derived materialization has been durably written but remains `REBUILDING`.

| Entities | Affected nodes | Re-invalidation work | Rebuilt nodes | Rebuild work | Total recovery work | Full reconstruction |
|---:|---:|---:|---:|---:|---:|---:|
| 100 | 4 | 11 | 4 | 19 | 35 | 9,977 |
| 1,000 | 4 | 11 | 4 | 19 | 35 | 102,675 |
| 10,000 | 4 | 11 | 4 | 19 | 35 | 1,029,675 |
| 50,000 | 4 | 11 | 4 | 19 | 35 | 5,149,651 |

Thus, for this fixed affected region:

\[
RecoveryWork(N)=35
\]

through 50,000 entities while clean reconstruction grows by more than three orders of magnitude.

At N=50,000:

\[
\frac{35}{5,149,651}\approx 6.80\times10^{-6}
\]

## v0.7 correction reproduced first

Before v0.8 recovery was allowed to run, CI required the complete recorded v0.7 ledger to reproduce through a scan-free affected-region discovery path. That gate passed.

This matters because recovery locality is only meaningful if the normal maintenance mechanism is itself local. The corrected path carries affected IDs directly in `DependencyTrace`; it does not infer them by scanning all lifecycle state.

## Validation

Authoritative evidence run: GitHub Actions `34003891203`.

It passed:

- **55/55 unit tests**;
- v0.4 planner regression;
- v0.5 scalable-resolution regression;
- v0.6 maintenance regression;
- scan-free reproduction of the recorded v0.7 ledger;
- all 15 v0.8 crash-phase cases;
- v0.8 recovery locality sweep through 50,000 entities.

The unit suite includes regressions that deliberately replace `graph.invalid_nodes()` with a function that raises, verifying that the scan-free mutation and recovery paths do not depend on global invalid-node discovery.

## What the evidence supports

Within this prototype model:

\[
\boxed{
PendingIntent + RecordedAffectedRegion
\Rightarrow
NoStaleRead + LocalIdempotentRecovery
}
\]

and for fixed affected-region complexity:

\[
\boxed{
RecoveryCost(\Delta M) \propto Size(RecordedAffectedRegion(\Delta M))
}
\]

rather than unrelated total-memory cardinality.

The architecture therefore needs a maintenance transaction/journal boundary in addition to dependency invalidation:

\[
Intent \rightarrow CanonicalMutation \rightarrow Invalidate \rightarrow Rebuild/Retire \rightarrow CommitMaintenance
\]

Read admission must be aware of unresolved maintenance intent, not merely the lifecycle bit on a derived object.

## Important limitations

This is **not** yet evidence of storage-engine durability.

The current experiment uses:

- an in-memory object graph;
- `copy.deepcopy` to model a process-independent durable crash image;
- one in-flight maintenance intent;
- oracle assertions;
- clean full reconstruction only as a benchmark correctness oracle.

It does **not** establish:

- fsync/transaction semantics;
- atomicity across real database tables/indexes/object stores;
- torn-write handling;
- multiple concurrent writers;
- conflicting recovery intents;
- distributed consensus or replica recovery;
- cold/archive dependency recovery;
- authorization-aware recovery;
- production latency or throughput.

## Next falsification target

The next necessary step is **real persistence atomicity and multi-intent recovery**.

A v0.9 test should move the maintenance journal and canonical/derived records into a transactional persistent store, inject failures around transaction boundaries, and then test multiple queued or overlapping maintenance intents. The key question is whether the same no-stale-read/local-recovery invariant survives actual process termination and restart rather than a deep-copy crash model.
