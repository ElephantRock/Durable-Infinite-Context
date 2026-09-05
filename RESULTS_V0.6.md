# v0.6 — Incremental Addressability Maintenance Results

## Research question

v0.5 established a bounded indexed read path for controlled query resolution, but its addressability indexes were rebuilt from the full memory state.

v0.6 tests the corresponding maintenance-plane hypothesis:

\[
MaintenanceCost(\Delta M) \approx f(AffectedRegion)
\]

rather than:

\[
MaintenanceCost(\Delta M,N) \approx O(N)
\]

for fixed local mutation complexity.

The falsification target is stronger than "the update code runs": after every mutation, the incrementally maintained materialization must be exactly equivalent to a clean full rebuild.

## Mechanism under test

The v0.6 mechanism adds locality metadata to canonical storage and makes the v0.5 subject-profile materialization incrementally patchable.

### Canonical-side locality indexes

`MemoryStore` now maintains:

- assertion IDs by state key;
- assertion IDs by subject;
- direct evidence → subject dependency reference counts.

These indexes prevent the maintenance path from hiding a global canonical-store scan upstream of the derived index update.

### Incremental subject-profile materialization

`SubjectProfileIndex` now uses mutable unique-subject postings for:

- exact tokens;
- separator fragments;
- character n-grams;
- predicate-specific versions of all three.

A subject refresh reconstructs only that subject from its canonical assertions/evidence and patches the changed posting memberships.

IDF is computed lazily from current posting cardinality. This avoids rewriting the full token vocabulary when total subject cardinality changes.

### Dependency-aware maintainer

`AddressabilityMaintainer` resolves the affected region before refreshing the derived index:

- evidence replacement/deletion → subjects that directly depend on that evidence;
- assertion insertion/replacement/deletion → old/new assertion subjects.

A fresh complete `SubjectProfileIndex` is then built only as the experimental oracle and compared with the incremental materialization.

## Benchmark

Entity cardinalities:

- 100
- 1,000
- 10,000
- 50,000

Mutation classes:

1. insert a new subject/evidence/assertion;
2. replace one evidence payload / exact alias;
3. change one assertion predicate;
4. rebind one assertion to different evidence;
5. replace evidence shared by four subjects;
6. delete one assertion;
7. delete one evidence record.

Every recorded operation must satisfy both:

- exact materialization equality with a clean rebuild;
- a workload-specific semantic check.

Logical maintenance work counts subject refreshes, subject-local assertions/evidence/tokens examined, and base/predicate posting membership mutations.

## Initial failure and revision

The first CI execution stopped at a unit test before the v0.6 benchmark ran. The test expected an old surface alias to become completely unresolvable after replacing `Atlas-...` with `Nova-...`.

That expectation was incorrect under the already-validated v0.5 retrieval semantics: the stable numeric separator fragment intentionally remains a fuzzy address signal. The maintenance index itself matched a clean rebuild.

The test was corrected to assert the actual maintenance invariant:

- the old exact-token posting is removed;
- the new exact-token posting is added;
- the new alias resolves correctly;
- the incrementally maintained index equals the rebuild oracle.

No benchmark threshold or maintenance mechanism was relaxed.

## Observed result

The corrected CI run passed:

- **35/35 unit tests**;
- the complete v0.4 planner regression;
- the complete v0.5 scalable-resolution regression;
- the v0.6 maintenance sweep.

Every v0.6 measurement had:

- `materialization_equal = true`;
- `semantic_check = true`.

### N = 50,000

| Mutation | Affected subjects | Incremental work | Full rebuild work | Incremental / rebuild |
|---|---:|---:|---:|---:|
| Insert subject | 1 | 88 | 4,399,842 | 0.00002000 |
| Replace evidence payload | 1 | 36 | 4,399,840 | 0.00000818 |
| Change predicate | 1 | 88 | 4,399,840 | 0.00002000 |
| Rebind assertion evidence | 1 | 40 | 4,399,842 | 0.00000909 |
| Replace shared evidence | 4 | 172 | 4,400,038 | 0.00003909 |
| Delete assertion | 1 | 79 | 4,399,950 | 0.00001795 |
| Delete evidence | 1 | 81 | 4,399,865 | 0.00001841 |

### Scaling trend

For the fixed-local mutations, incremental logical work remained bounded across the cardinality sweep:

- insert: 88 → 88 → 88 → 88;
- evidence payload replacement: 36 → 36 → 36 → 36;
- predicate change: 86 → 88 → 88 → 88;
- evidence rebind: 40 → 40 → 40 → 40;
- assertion deletion: 77 → 79 → 79 → 79;
- evidence deletion: 79 → 81 → 81 → 81.

The small 1–2 operation differences come from local lexical-feature differences in particular synthetic identifiers, not global cardinality growth.

For shared evidence with fixed fan-out of four subjects:

\[
Work_{shared}=172
\]

at every tested cardinality.

Meanwhile full rebuild work grows approximately linearly with memory size, reaching about 4.4 million logical operations at 50,000 entities.

Therefore, for the controlled fixed-complexity mutations:

\[
\frac{Cost_{incremental}(\Delta,N)}{Cost_{rebuild}(N)} \rightarrow 0
\]

and for shared evidence:

\[
Cost_{update} \approx f(Fanout, LocalProfileComplexity)
\]

rather than total entity cardinality.

## Interpretation

v0.6 supports a narrower maintenance-plane claim:

> Rebuildable retrieval materializations do not inherently require global reconstruction after every canonical change. With explicit dependency metadata, addressability maintenance can be bounded by the true affected subject region while remaining exactly reconstructible from authoritative state.

This strengthens the architecture from:

\[
CanonicalState \rightarrow RebuildableIndex
\]

to:

\[
CanonicalChange \rightarrow AffectedRegion \rightarrow IncrementalRepair
\]

with a full rebuild retained as an oracle/recovery path rather than the normal mutation path.

## Important limitations

This result does **not** establish:

- production database/index update latency;
- distributed consistency or concurrent mutation semantics;
- crash recovery;
- relation-graph lifecycle cleanup;
- high-fan-out update boundedness independent of fan-out;
- storage/write-amplification efficiency;
- archive/deletion propagation through all derived layers;
- model-extraction maintenance;
- a production entity linker.

The implementation uses in-memory sets and controlled oracle assertions. A full rebuild is intentionally performed after each benchmark mutation only to serve as the correctness oracle; it is not part of the proposed runtime update path.

High fan-out is also not a defect to hide: the intended invariant is affected-region proportionality, not constant cost for every possible mutation.

## Revised architectural statement

The maintenance requirement can now be stated more precisely:

\[
\boxed{MaintenanceCost(\Delta M) \propto Size(TrueAffectedSubgraph(\Delta M))}
\]

subject to the complexity of the affected records/features.

This is stronger and more useful than demanding universal O(1) writes.

## Next falsification target

v0.6 covers direct evidence/assertion → subject addressability dependencies. It does not yet test multi-layer derived-state cascades.

The next experiment should therefore ask:

> Can dependency-aware invalidation and reconstruction remain proportional to the true affected subgraph when a canonical correction/deletion propagates through multiple layers of derived state?

That is the natural v0.7 dependency-cascade / invalidation-propagation test before claiming lifecycle maintenance locality across the broader architecture.
