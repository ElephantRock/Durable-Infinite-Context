# v0.27 — Persistence fault model

## Observe

v0.26 established restart convergence when recovery itself is terminated with real process `SIGKILL`. That evidence is still a process-crash result. In particular, v0.26 observed that a completed `ftruncate` remained visible after process death even when the process was killed before the explicit fixed-file `fsync`.

That observation does **not** establish that the file-size change would survive hardware power loss. Process-visible state and durable-media state are different boundaries.

## Diagnose

The remaining protocol question is durability ordering. A cleanup implementation can appear correct under process death while still being wrong if it records that cleanup is complete before the underlying storage operation has become durable.

The dangerous pattern is:

1. truncate stale file tail;
2. durably record `tail_clean = true` elsewhere;
3. crash before the file truncate is durable;
4. restart and trust the marker.

If the marker survives but the truncate does not, stale tail is stranded permanently.

## Derive

v0.27 therefore separates volatile and durable file-size state explicitly:

\[
\begin{aligned}
ftruncate &: V \leftarrow new\\
fsync &: D \leftarrow V\\
PowerLoss &: V \leftarrow D
\end{aligned}
\]

where `V` is the process-visible file-size frontier and `D` is the durable frontier.

SQLite cleanup commits are represented as an explicit atomic durable transaction boundary. This is deliberately an abstract ordering model, not an implementation model of SQLite WAL internals.

A recovery protocol that re-derives residue from durable state on every restart should therefore tolerate loss of an unsynced truncation: after modeled power loss the old durable file length reappears, the next recovery observes the residue again, truncates it again, and syncs it.

## Hypothesis

The v0.25/v0.26 **derivation-based** cleanup protocol is restart-convergent under the explicit volatile-versus-durable persistence model, while a cleanup-complete marker durably committed before the fixed-file `fsync` is unsafe.

## Prediction

The fixed predictions were:

- an uncommitted future-row DELETE is lost after modeled power loss;
- a committed future-row DELETE survives;
- an unsynced `ftruncate` is lost and the durable stale tail reappears;
- a synced `ftruncate` survives;
- re-deriving residue on restart converges with zero application logical redo;
- a durable `tail_clean` marker written before fixed-file `fsync` can survive while the truncate is lost, stranding stale tail;
- stale-tail retry reclamation remains `4096(C+2)=Theta(C)` for the current eager generation allocation scheme.

## Test

The deterministic experiment keeps the v0.16 semantic guard and executes nine persistence-fault cases over three residue classes:

- future overflow;
- fixed stale tail;
- combined future-row + stale-tail residue.

The exercised persistence boundaries are:

- future-row DELETE before durable transaction commit;
- future-row DELETE after durable transaction commit;
- after future-row cleanup but before tail cleanup;
- tail truncated but not file-synced;
- tail file-synced.

The negative control uses the same stale-tail fixture but durably records a cleanup-complete marker after truncate and before file `fsync`.

The reclamation sweep uses initial capacities `C={32,128,512,2048}`.

## Validated evidence

Initial successful CI evidence is anchored to run **#255** on head:

`012265f69399b99370a1f9229e1c86687bd1f02e`

Artifact:

- ID `10064080341`
- name `v0.27-persistence-fault-results`
- SHA-256 `a33559cdf1b37a5a3ac2d9a4bb3467ad376d8ddfd63ca6fb1cfedb37b8e93202`
- contained `persistence_fault_results.json` SHA-256 `4d55252a4d2358d1c815a74b35cfcec4fd862f39be01c7d38cd5a7b66ab6c234`

All six semantic guard predicates remained true.

### Nine-case matrix

All **9/9** cases preserved the exact committed logical snapshot after modeled power loss, converged under restart, required zero application logical redo, and had a no-op second completed recovery.

Key durability observations:

| Boundary | Durable future rows after power loss | Durable stale tail after power loss |
|---|---:|---:|
| future DELETE uncommitted | 1 | fixture-dependent |
| future DELETE committed | 0 | fixture-dependent |
| tail truncated, not synced | 0 | 139,264 bytes |
| tail synced | 0 | 0 bytes |

For the unsynced truncation cases, the model restores the old durable file-size frontier after power loss. Recovery then re-derives the residue and performs another truncate + file `fsync`.

### Premature durable-marker counterexample

The negative control starts with a 139,264-byte durable stale tail. It performs:

1. `ftruncate` to the reachable file length;
2. durable commit of `tail_clean = true`;
3. modeled power loss before fixed-file `fsync`.

After power loss:

- the durable marker is `true`;
- the durable file tail is again **139,264 bytes**;
- marker-based retry performs no cleanup;
- the marker protocol therefore does **not** converge.

Re-running the derivation-based protocol ignores the stale marker, observes durable file length directly, truncates + syncs it, and restores zero tail.

Thus:

\[
\boxed{DurableCleanupMarker \not\Rightarrow DurableCleanup}
\]

when the marker is persisted before the storage operation it certifies.

### Reclamation scaling

The current eager-generation stale suffix remains:

\[
\boxed{StaleTailBytes(C)=4096(C+2)=\Theta(C)}
\]

Observed durable residue after unsynced truncate + modeled power loss, and retry reclamation volume:

| Initial capacity `C` | Bytes |
|---:|---:|
| 32 | 139,264 |
| 128 | 532,480 |
| 512 | 2,105,344 |
| 2,048 | 8,396,800 |

Each retry converged to zero durable tail, but the amount of file-length range reclaimed grows linearly with generation capacity.

## Revise

v0.26's process-crash result survives, but its file-size observation must remain scoped to process visibility. v0.27 earns the stronger protocol-level statement that **re-deriving cleanup from durable state is safe in the tested persistence-order model even when an unsynced truncate is lost**.

The durable-marker negative control is rejected. Any future cleanup-completion metadata must either:

- be ordered strictly after the storage operation is durably complete; or
- be advisory only, with recovery still deriving residue from durable state.

## Engineer

The surviving experimental hybrid remains:

- arithmetic-addressed fixed-page bounded primary;
- bounded incremental migration;
- persistent exact overflow;
- fixed-page committed epoch as cross-store visibility coordinator;
- indexed cleanup of abandoned future overflow rows;
- stale-tail cleanup derived from committed fixed-page metadata/file length;
- restart-safe recovery under the tested process-crash and abstract persistence-ordering models.

The marker protocol is **not** adopted.

## Claim boundary

v0.27 is a deterministic abstract persistence-ordering experiment. It does **not** establish:

- hardware power-loss safety;
- torn-sector or partial-page behavior;
- drive write-cache ordering;
- filesystem journaling semantics;
- SQLite WAL-frame/fsync implementation behavior;
- filesystem allocated-block reclamation;
- device-level write amplification;
- multi-writer correctness;
- production latency, throughput, or cost.

`ftruncate`, `fsync`, and atomic SQLite transaction commit are modeled durability boundaries. The result is about protocol ordering and restart convergence under that model.

## Next falsification target — bounded crash residue

v0.27 leaves the main remaining locality defect explicit: eager generation allocation can leave a `Theta(C)` stale file-length range after an interrupted migration start.

The next question is:

\[
\boxed{
Can generation space be allocated incrementally so crash residue and reclamation volume are bounded per mutation,
without destroying arithmetic lookup locality, bounded migration work, or the crash/persistence contracts already earned?
}
\]

A v0.28 experiment should compare eager generation extension against incremental/lazy page allocation, measure the largest durable/volatile unreachable suffix per mutation, preserve direct page addressing, and retain the negative persistence-ordering controls from v0.27.
