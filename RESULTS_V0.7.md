# v0.7 — Dependency-Cascade Invalidation Results

## Research question

v0.6 established that direct addressability materializations can be maintained locally after evidence/assertion mutations. It did not establish that corrections and deletions can propagate through **multiple layers of derived state** without maintenance work growing with lifetime memory.

v0.7 therefore tests:

\[
\boxed{
Cost_{invalidate+rebuild+retire}(\Delta)
\approx
f(Size(TrueAffectedSubgraph(\Delta)))
}
\]

rather than:

\[
Cost(\Delta,N) \approx O(N)
\]

for fixed local mutation complexity.

The result is accepted only if the incrementally repaired system is exactly equivalent to a clean reconstruction from authoritative canonical state.

## Mechanism under test

The integrated derived path is:

\[
Evidence/Assertion
\rightarrow
State/Profile
\rightarrow
Support
\rightarrow
Context
\]

Canonical evidence, assertions, and relations remain authoritative. Derived layers carry explicit lifecycle state:

- `fresh`
- `invalid`
- `rebuilding`

A canonical change performs immediate invalidation. Reconstruction is demand-driven/topological, and derived nodes whose canonical basis disappears are retired locally.

The dependency graph stores both directions:

- dependency → dependents, for invalidation;
- dependent → dependencies, for ordered reconstruction.

Dependency metadata is itself rebuildable and is included in exact parity checks.

## Falsification gates

Each integrated mutation must satisfy all of the following:

1. semantic output is correct;
2. no invalid derived node remains after the requested repair pass;
3. state, addressability profile, support snapshot, context, and dependency graph exactly equal a clean full reconstruction;
4. incremental work does not grow with unrelated total-memory cardinality for a fixed affected region.

The topology control separately requires:

\[
InvalidatedNodes = Fanout \times Depth
\]

with unrelated branches remaining fresh.

## Benchmark

Integrated cardinalities:

- 100
- 1,000
- 10,000
- 50,000 entities

Integrated mutation classes:

1. replace one evidence payload;
2. replace one assertion object while identity/predicate/evidence references remain unchanged;
3. insert an explicit correction and `CORRECTS` relation;
4. replace evidence shared by four subjects;
5. delete one assertion and retire the unreachable derived branch.

Synthetic topology controls use independent chains to vary:

- global branch count;
- cascade depth;
- fan-out.

The full hardened CI gate also reruns v0.4, v0.5, and v0.6 before v0.7.

## Initial implementation and revision

The first v0.7 implementation passed its semantic and locality benchmark. Before recording that result as final, inspection exposed a lifecycle defect: deleting a canonical assertion removed the corresponding state/profile/support/context values, but the dependency graph retained the now-unreachable derived nodes as fresh metadata tombstones.

That would have moved unbounded growth from semantic materializations into lineage metadata.

The benchmark was not weakened. The mechanism was revised in three ways:

1. dependency-edge removals and node retirement were added to logical maintenance cost;
2. locally rebuilt nodes with no remaining canonical basis are retired leaf-to-root;
3. exact rebuild parity now includes the complete dependency graph, not only semantic values.

The retirement candidate set is restricted to the locally rebuilt region, so cleanup does not introduce a global scan.

## Hardened validation

Authoritative run: GitHub Actions `33999047206`.

The run passed:

- **45/45 unit tests**;
- complete v0.4 planner regression;
- complete v0.5 scalable-resolution regression;
- complete v0.6 incremental-addressability regression;
- complete v0.7 dependency-cascade experiment.

Every integrated row had:

- `materialization_equal = true`;
- dependency-graph parity included in that equality;
- `semantic_check = true`;
- `all_fresh_after_rebuild = true`.

## Integrated result at N = 50,000

| Mutation | Invalidated | Rebuilt | Incremental work | Full rebuild work | Fraction |
|---|---:|---:|---:|---:|---:|
| Replace evidence payload | 3 | 3 | 60 | 5,149,752 | 0.00001165 |
| Replace assertion object | 3 | 3 | 21 | 5,149,752 | 0.00000408 |
| Insert correction | 4 | 4 | 88 | 5,149,786 | 0.00001709 |
| Replace shared evidence | 12 | 12 | 270 | 5,149,994 | 0.00005243 |
| Delete assertion | 4 | 4 | 111 | 5,149,891 | 0.00002155 |

Deletion additionally retires the four now-unreachable nodes for that subject/key branch:

- profile;
- state;
- support;
- context.

The dependency graph therefore shrinks from `4N` derived nodes to `4N - 4` for that deleted branch and still exactly matches a clean reconstruction.

## Cardinality scaling

For fixed local mutation structure, work remains essentially flat as total memory grows from 100 to 50,000 entities:

- evidence payload replacement: **60 → 60 → 60 → 60**;
- assertion-object replacement: **21 → 21 → 21 → 21**;
- correction insertion: **88 → 88 → 88 → 88**;
- shared evidence with fan-out four: **270 → 270 → 270 → 270**;
- assertion deletion including retirement: **109 → 111 → 111 → 111**.

The two-operation deletion difference at the smallest scale comes from local feature/dependency details in the synthetic fixture, not from global memory growth.

Meanwhile full reconstruction grows from roughly ten thousand logical operations at 100 entities to roughly **5.15 million** at 50,000 entities.

Therefore, for the tested fixed-complexity integrated mutations:

\[
\frac{Cost_{incremental}(\Delta,N)}{Cost_{full}(N)} \rightarrow 0
\]

as unrelated memory grows.

## Topology cardinality control

With fixed depth `D=4` and fan-out `F=4`:

| Total branches | Total derived nodes | Affected nodes | Invalidation work |
|---:|---:|---:|---:|
| 100 | 400 | 16 | 50 |
| 1,000 | 4,000 | 16 | 50 |
| 10,000 | 40,000 | 16 | 50 |

Unrelated probes remained fresh in every case.

This directly rejects a global-graph traversal interpretation for the implemented mechanism.

## Depth × fan-out control

At a fixed 1,024 global branches, the observed values are:

| Depth | Fan-out | Affected nodes | Invalidation work |
|---:|---:|---:|---:|
| 1 | 1 | 1 | 5 |
| 1 | 4 | 4 | 14 |
| 1 | 16 | 16 | 50 |
| 1 | 64 | 64 | 194 |
| 2 | 1 | 2 | 8 |
| 2 | 4 | 8 | 26 |
| 2 | 16 | 32 | 98 |
| 2 | 64 | 128 | 386 |
| 4 | 1 | 4 | 14 |
| 4 | 4 | 16 | 50 |
| 4 | 16 | 64 | 194 |
| 4 | 64 | 256 | 770 |
| 8 | 1 | 8 | 26 |
| 8 | 4 | 32 | 98 |
| 8 | 16 | 128 | 386 |
| 8 | 64 | 512 | 1,538 |

For this controlled graph family:

\[
\boxed{AffectedNodes = F D}
\]

and the current trace definition gives exactly:

\[
\boxed{InvalidationWork = 2 + 3FD}
\]

Thus:

\[
InvalidationCost = \Theta(Size(ReachableAffectedSubgraph))
\]

for the tested topology.

## Interpretation

v0.7 supports a stronger maintenance-plane statement than v0.6:

> A canonical correction or deletion can invalidate, selectively reconstruct, and retire a multi-layer derived dependency region without work proportional to unrelated lifetime memory, provided reverse dependency metadata is maintained and derived lineage remains reconstructible from authoritative state.

The operational pattern is therefore:

\[
CanonicalChange
\rightarrow
AffectedRoots
\rightarrow
ImmediateInvalidation
\rightarrow
SelectiveTopologicalRebuild
\rightarrow
LocalRetirement
\]

not global semantic recomputation.

This also sharpens the earlier lifecycle principle:

\[
\boxed{
Correctness\ requires\ immediate\ invalidation;\ reconstruction\ may\ be\ selective
}
\]

provided stale/invalid derived materializations cannot be served as fresh results.

## Important limitations

v0.7 does **not** establish:

- durable persistence of lifecycle metadata across process crashes;
- atomicity between canonical writes and invalidation records;
- concurrent writer semantics;
- distributed dependency-graph consistency;
- crash recovery during `REBUILDING`;
- archive/cold-tier dependency traversal;
- production wall-clock latency;
- bounded behavior for genuinely massive affected subgraphs;
- model-extraction correction cascades;
- production authorization/deletion enforcement.

The implementation is in-memory and uses oracle assertions. Full reconstruction is deliberately performed by the benchmark as the correctness oracle; it is not the proposed routine maintenance path.

## Revised architectural statement

The maintenance invariant can now be stated as:

\[
\boxed{
MaintenanceCost(\Delta M)
\propto
Size(TrueAffectedDependencySubgraph(\Delta M))
}
\]

including invalidation, reconstruction, dependency-edge updates, and retirement of unreachable derived state.

## Next falsification target

The next unresolved failure mode is **interrupted maintenance**.

A natural v0.8 question is:

> Can canonical state, invalidation intent, derived lifecycle, and selective reconstruction recover correctly after crashes at arbitrary points in the write/invalidate/rebuild/retire sequence, without requiring a full-memory rebuild?

That should be tested before making claims about durable lifecycle maintenance rather than merely in-process locality.
