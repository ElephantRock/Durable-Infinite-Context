# v0.7 correction — affected-region discovery

During v0.8 crash-recovery work, a manual audit found that the v0.7 dependency traversal itself was local, but the original `CascadeMaintainer` wrapper identified newly invalid nodes by comparing `graph.invalid_nodes()` before and after a mutation. The no-argument rebuild path also used `invalid_nodes()` to discover work. Both operations scan all derived lifecycle entries, and that O(total-derived-nodes) discovery work was not included in the v0.7 logical-work trace.

This does **not** falsify the v0.7 dependency topology, semantic parity, selective rebuild, or retirement measurements once the affected region is known. It does weaken the original end-to-end locality interpretation of the wrapper that produced them.

v0.8 therefore revised the mechanism rather than the benchmark:

1. `DependencyTrace` now carries the exact node IDs newly invalidated during reverse-dependency traversal.
2. The scan-free maintainer returns those IDs directly instead of scanning global lifecycle state.
3. Selective rebuild receives those IDs explicitly instead of discovering invalid nodes with a global scan.
4. Regression tests monkeypatch `invalid_nodes()` to raise if the operational scan-free mutation/recovery path attempts to use it.
5. The complete recorded v0.7 ledger was rerun through the scan-free path without changing the workload or acceptance metrics.

Authoritative correction run: GitHub Actions `34003891203`.

The scan-free reproduction passed the existing compact v0.7 ledger contract exactly. At N=50,000 the reproduced values remained:

| Mutation | Invalidated | Rebuilt | Incremental work |
|---|---:|---:|---:|
| Replace evidence payload | 3 | 3 | 60 |
| Replace assertion object | 3 | 3 | 21 |
| Insert correction | 4 | 4 | 88 |
| Replace shared evidence | 12 | 12 | 270 |
| Delete assertion | 4 | 4 | 111 |

The topology control also reproduced exactly, including `InvalidationWork = 2 + 3FD` for the controlled graph family.

The corrected maintenance invariant is therefore stricter:

\[
MaintenanceCost(\Delta M) = Cost(DiscoverAffected) + Cost(Invalidate) + Cost(Rebuild) + Cost(Retire)
\]

and **each operational term** must scale with the true affected dependency region rather than unrelated total memory.

Global scans remain permissible in benchmark/oracle validation code when they are explicitly outside the measured operational path; they are not permissible as hidden affected-region discovery in the mechanism being claimed as local.
