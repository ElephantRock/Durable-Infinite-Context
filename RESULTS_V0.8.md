# v0.8 — Interrupted maintenance / crash-recovery results

## Research question

v0.7 established local multi-layer invalidation/rebuild/retirement in-process, subject to the affected-region discovery correction recorded in `RESULTS_V0.7_CORRECTION.md`.

v0.8 asks:

> Can a canonical mutation and its derived maintenance recover after interruption at arbitrary maintenance phases without serving stale derived context or requiring a global rebuild?

The prototype tests a **durable maintenance intent** written before canonical mutation. A restarted process must drain pending maintenance before derived reads are admitted.

## Mechanism under test

The single-flight state machine is:

\[
Prepared \rightarrow CanonicalApplied \rightarrow Invalidated \rightarrow Rebuilding \rightarrow Repaired \rightarrow Finalized
\]

The hardened recovery rule is redo-style:

\[
Prepared\;or\;CanonicalApplied
\Rightarrow
IdempotentCanonicalRedo
\rightarrow Invalidate
\rightarrow Rebuild/Retire
\]

The durable intent records the operation and, once invalidation occurs, the exact affected derived node IDs emitted by dependency traversal.

Safety rule:

\[
PendingMaintenance \Rightarrow DerivedReadsBlocked
\]

A crash at `REBUILDING` treats the partially written derived node as untrusted. Recovery re-invalidates only the intent's recorded region and reconstructs that region idempotently from canonical state.

## Falsification and revision

The first v0.8 state machine treated `CANONICAL_APPLIED` as proof that the canonical mutation itself had persisted. A stronger torn-boundary test deliberately persisted the phase marker while withholding the canonical mutation.

That test failed: **57/58 tests passed**, with the sole failure demonstrating that phase advancement and canonical durability cannot be assumed atomic in the prototype model.

The mechanism was revised rather than the acceptance criterion. Recovery now idempotently replays canonical mutation for both `PREPARED` and `CANONICAL_APPLIED` intents.

The inverse torn boundary was also tested: canonical mutation persisted while the phase marker remained `PREPARED`. Replay remained safe.

Redo safety is covered for:

- evidence upsert;
- assertion upsert;
- assertion deletion.

The hardened unit suite reached **60/60 passing** before the result ledger was regenerated.

## Falsification matrix

At N=100, three mutation classes were interrupted after five phases:

- replace evidence payload;
- replace assertion object;
- delete assertion and retire its derived branch.

Crash phases:

1. `prepared` — intent durable; canonical persistence may or may not already have occurred;
2. `canonical_applied` — phase marker durable; recovery does not trust that canonical persistence was atomic with it;
3. `invalidated` — affected derived nodes invalid;
4. `rebuilding` — one partial derived write durably left `REBUILDING`;
5. `repaired` — local repair complete, journal finalization pending.

Every row requires:

- stale derived reads blocked before recovery;
- exact state/profile/support/context/dependency-graph parity with a clean reconstruction;
- semantic correctness;
- all surviving derived nodes fresh;
- empty maintenance journal after recovery.

All 15 phase/operation combinations pass under the redo-safe implementation.

## Recovery work at N=100

| Operation | Prepared | Canonical applied | Invalidated | Rebuilding | Repaired |
|---|---:|---:|---:|---:|---:|
| Evidence payload | 67 | **66** | 53 | 36 | 3 |
| Assertion object | 28 | **27** | 16 | 26 | 3 |
| Delete assertion | 116 | **115** | 101 | 35 | 3 |

Both `prepared` and `canonical_applied` recovery now perform one canonical mutation during restart. This is the deliberate cost of redo safety across a torn phase/canonical boundary.

Compared with the pre-redo ledger, only the `canonical_applied` rows increase by one logical operation:

- evidence replacement: 65 → 66;
- assertion replacement: 26 → 27;
- assertion deletion: 114 → 115.

The `repaired` phase still requires only journal finalization in the current trace: 3 logical operations and zero derived rebuilds.

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

## v0.7 hidden-scan correction and canonicalization

A v0.8 audit found that the original v0.7 wrapper discovered newly invalid nodes by calling `graph.invalid_nodes()` before and after mutation. The traversal itself was local, but that wrapper added an unmeasured O(total-derived-nodes) scan.

The benchmark was not weakened. Instead:

1. a scan-free maintainer returned exact affected IDs directly from invalidation traversal;
2. the complete recorded v0.7 ledger reproduced through that path;
3. tests replaced `graph.invalid_nodes()` with a function that raises and still passed;
4. v0.8 promoted the scan-free implementation into the canonical `CascadeMaintainer` API;
5. normal cascade reconstruction now receives the exact affected IDs explicitly.

The old `ScanFreeCascadeMaintainer` name remains only as a compatibility subclass, not as a separate implementation.

## Validation status

The redo-safe branch has already demonstrated:

- **60/60 unit tests** passing;
- v0.4 planner regression passing;
- v0.5 scalable-resolution regression passing;
- v0.6 maintenance regression passing;
- scan-free reproduction of the recorded v0.7 ledger.

Before the redo ledger was updated, the recovery verifier correctly rejected the old measurement at the first changed row:

```text
('replace_evidence_payload', 'canonical_applied').recovery_work:
expected 65, observed 66
```

The observed run simultaneously showed exact parity and the updated redo-safe values for all three `canonical_applied` rows. Those observed measurements are now recorded in `recovery_results.json`.

The final verifier additionally requires:

- exact phase/locality row counts;
- unique row keys;
- exact observed-vs-recorded key sets;
- equality of every recorded measurement field;
- all required safety/correctness booleans true.

**Merge criterion:** the exact final PR head must pass the complete CI chain and reproduce both the v0.7 compact ledger through the canonical scan-free path and the v0.8 redo-safe ledger. No result is considered final before that exact-head gate is green.

## What the evidence supports

Within this prototype model, the surviving hypothesis is now:

\[
\boxed{
DurableIntent + IdempotentCanonicalRedo + RecordedAffectedRegion
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

The maintenance boundary is therefore:

\[
Intent
\rightarrow CanonicalRedo
\rightarrow Invalidate
\rightarrow Rebuild/Retire
\rightarrow CommitMaintenance
\]

Read admission must be aware of unresolved maintenance intent, not merely the lifecycle bit on a derived object.

## Important limitations

This is **not** yet evidence of storage-engine durability.

The current experiment uses:

- an in-memory object graph;
- `copy.deepcopy` to model a process-independent durable crash image;
- explicit torn phase/canonical boundary simulation;
- one in-flight maintenance intent;
- oracle assertions;
- clean full reconstruction only as a benchmark correctness oracle.

It does **not** establish:

- fsync or real transaction semantics;
- atomicity across real database tables/indexes/object stores;
- filesystem/page-level torn writes;
- multiple concurrent writers;
- conflicting recovery intents;
- distributed consensus or replica recovery;
- cold/archive dependency recovery;
- authorization-aware recovery;
- production latency or throughput.

## Next falsification target

The next necessary step is **v0.9 — persistent WAL / real process-crash recovery**.

The maintenance journal and authoritative state should move into a transactional persistent store, with a separate process killed at controlled boundaries. A fresh process must reopen the store, drain recovery, and reproduce clean reconstruction while preserving affected-region locality.

Only after actual storage/process durability survives should the project add overlapping writers or multi-intent concurrency.
