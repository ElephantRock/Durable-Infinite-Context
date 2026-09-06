# v0.15 Results — Compositional Profile Facets

## Problem

v0.14 removed unnecessary dependence on historical depth `H` by maintaining a current assertion head per `(subject,predicate)`. It still persisted one evidence-bearing subject profile:

\[
Profile(subject)=Facet_1+\cdots+Facet_P
\]

so changing one predicate could force reconstruction of all `P` live predicate contributions even when only `K=1` predicate changed.

The v0.15 falsification target was therefore:

\[
\boxed{
Maintenance=O(K),\qquad PartialAssembly=O(K),\qquad FullAssembly=O(P)
}
\]

without changing the logical subject-profile semantics established by v0.14.

## Mechanism under test

v0.15 persists `profile:<subject>` as a predicate manifest only:

\[
ProfileManifest(subject)=\{p_1,\ldots,p_P\}
\]

Predicate-specific support materializations remain evidence-bearing facets. A logical profile is assembled from one SQLite WAL snapshot as:

\[
Profile_{logical}(subject,Q)
=
Manifest(subject)+\sum_{p\in Q}Facet(subject,p)
\]

where `Q=P` for a full profile and `|Q|=K` for a selective profile.

Evidence/value changes do not invalidate the manifest. Predicate-presence topology changes do.

## First CI falsification and revision

The first CI execution did **not** pass. The discriminating cross-version check found that the v0.15 assembled profile used Python tuples for `evidence_payloads`, while the persisted v0.14 JSON profile decoded to lists. Payloads, predicate membership, and order were otherwise identical, but exact API representation was not.

This was treated as a semantic-interface failure rather than weakening the comparison. v0.15 was revised to return the same JSON-native list representation used by v0.14, and a permanent cross-version regression test was added.

The corrected implementation then passed the fixed experiment and the complete historical CI replay chain.

Validated implementation head for the first successful v0.15 experiment:

`b007d1159b573a7faf216fdf2879362040b3d76a`

GitHub Actions run:

`34057706190` (`CI` run 158)

The uploaded `v0.15-compositional-profile-results` artifact has GitHub artifact digest:

`sha256:e57488b5b50f070c1e2c1d7777aea783554396b8935b3ea9297689b857f61397`

The contained `compositional_profile_results.json` payload has SHA-256:

`ff5720513574676d1286c1062d4876a3f61dda6baefa23daa275fd759663f3dd`

## Discriminating control

At `P=32`, `K=1`, `H=8`, `N=128`:

| Mechanism | Maintenance work | Persisted profile bytes |
|---|---:|---:|
| v0.14 monolithic profile | 287 | 2,863 |
| v0.15 compositional facets | **27** | **438** |

The v0.15 assembled full logical profile exactly matches the corresponding v0.14 persisted profile and the independent canonical oracle.

## Predicate fan-out `P` sweep

Fixed `K=1`, `H=8`, `N=128`:

| P | v0.14 maintenance | v0.15 maintenance | Partial logical assembly | Full logical assembly | Monolithic profile bytes | Manifest bytes |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 39 | **27** | **3** | 3 | 197 | 66 |
| 2 | 47 | **27** | **3** | 4 | 283 | 78 |
| 4 | 63 | **27** | **3** | 6 | 455 | 102 |
| 8 | 95 | **27** | **3** | 10 | 799 | 150 |
| 16 | 159 | **27** | **3** | 18 | 1,487 | 246 |
| 32 | 287 | **27** | **3** | 34 | 2,863 | 438 |
| 64 | 543 | **27** | **3** | 66 | 5,615 | 822 |

Under the experiment's logical-operation accounting, one changed facet remains 27 maintenance operations as total live subject fan-out grows from 1 to 64. A selective one-facet read remains three logical operations: one journal read, one manifest-row read, and one facet read.

Full assembly grows exactly with the requested full result: two fixed reads plus `P` facet reads.

## Changed/requested subset `K` sweep

Fixed `P=32`, `H=8`, `N=128`:

| K | v0.14 maintenance | v0.15 maintenance | Partial logical assembly | Full logical assembly |
|---:|---:|---:|---:|---:|
| 1 | 287 | **27** | 3 | 34 |
| 2 | 574 | **54** | 4 | 34 |
| 4 | 1,148 | **108** | 6 | 34 |
| 8 | 2,296 | **216** | 10 | 34 |
| 16 | 4,592 | **432** | 18 | 34 |

The observed maintenance relation is exactly 27 logical operations per changed evidence facet in this controlled workload. Selective assembly is `K+2` logical reads. Full assembly remains fixed at `P+2=34` because `P` is fixed.

## Historical-depth `H` sweep

Fixed `P=16`, `K=1`, `N=128`:

| H | v0.15 maintenance | Partial assembly | Full assembly | Full rebuild work |
|---:|---:|---:|---:|---:|
| 1 | **27** | **3** | 18 | 1,844 |
| 8 | **27** | **3** | 18 | 2,180 |
| 64 | **27** | **3** | 18 | 4,868 |

The local path remains independent of irrelevant lifetime history while full reconstruction still exposes the added history.

## Unrelated global cardinality `N` sweep

Fixed `P=16`, `K=1`, `H=8`:

| Entities | v0.15 maintenance | Partial assembly | Full assembly | Full rebuild work |
|---:|---:|---:|---:|---:|
| 100 | **27** | **3** | 18 | 1,816 |
| 1,000 | **27** | **3** | 18 | 13,516 |
| 10,000 | **27** | **3** | 18 | 130,516 |
| 50,000 | **27** | **3** | 18 | 650,516 |

The fixed local path remains independent of unrelated global memory under this SQLite prototype.

## Topology and read-safety checks

The experiment also verified:

- adding `facet_added` updates the subject manifest and clean-rebuild parity;
- deleting that predicate removes it and preserves parity;
- the current-head index remains canonical;
- during an active update to one facet, an unrelated facet remains readable;
- the affected facet is blocked;
- a full profile read is blocked while it could be stale;
- after recovery, the full composed profile exactly matches the independent canonical oracle.

## Surviving result

The evidence supports the following **logical-operation** statement for the tested evidence/value mutation path:

\[
\boxed{
Maintenance=O(K),\qquad
SelectiveFacetReads=O(K),\qquad
FullFacetReads=O(P)
}
\]

with the tested fixed-local path independent of `H` and unrelated global `N`.

The result also strengthens the architectural distinction between physical representation and logical semantics: v0.15 changes the persisted profile from an evidence-bearing monolith to a manifest plus facets while preserving the exact v0.14 full logical profile.

## Important non-claim discovered by the same evidence

The current manifest is itself `O(P)` in serialized size:

`66, 78, 102, 150, 246, 438, 822` bytes for `P=1,2,4,8,16,32,64`.

A selective v0.15 read currently loads and deserializes that entire manifest row before reading `K` facets. The logical trace counts this as one manifest read, so the experiment **does not establish physical `O(K)` bytes, CPU time, or page I/O for selective assembly**.

Likewise, predicate addition/removal correctness was tested, but topology-mutation scaling across `P` was not. Rewriting an `O(P)` manifest may retain subject-wide work for schema/topology changes.

Therefore the stronger statement

\[
PartialAssembly=O(K)\quad\text{in actual bytes/time independent of }P
\]

is **not yet evidence-backed**.

## Revision / next falsification target

The next counterexample is now explicit: composition removed evidence fan-out from maintenance, but the subject manifest can still reintroduce `P` through representation size and topology rewrite.

A v0.16 experiment should test a normalized predicate-membership representation, for example indexed `(subject,predicate)` membership rows, so that:

- selective membership validation can probe only the requested `K` predicates;
- evidence/value maintenance remains local to changed facets;
- predicate addition/removal updates only the changed membership rows;
- full profile enumeration still legitimately costs `O(P)`;
- byte-level or row/page-level instrumentation exposes physical work rather than counting an arbitrarily large manifest row as one operation.

The falsifiable target is not “everything is constant.” It is:

\[
\boxed{
SelectivePhysicalWork\approx f(K),\qquad
TopologyDeltaWork\approx f(K),\qquad
FullEnumeration\approx f(P)
}
\]

subject to exact v0.15/v0.14 logical semantics and the existing crash/read-safety invariants.
