# v0.22 — Explicit Rare Overflow

## Problem

v0.21 established a finite bounded escalation contract, but every fixed number of bounded placement domains still has finite concentrated-collision capacity. Allowing the number of domains to grow without bound would erase the locality contract by making mutation work, missing-key lookup fan-out, and reserved space grow with the escalation count.

v0.22 tests a different policy: preserve one fixed bounded primary placement path, then route only exhausted placements to an explicit directly indexed comparison-tree overflow.

## Hypothesis

A bounded common path can remain isolated from exceptional overflow while guaranteeing admission in the tested envelope. The exceptional path is allowed to be logarithmic in overflow cardinality, but that cost must be explicit rather than hidden inside unbounded escalation.

## Fixed mechanism

- primary: one v0.20 two-choice bucketized cuckoo domain
- 4 slots per bucket
- at most 32 relocations
- stash capacity 8
- primary mutation cap: 200 modeled slot operations
- overflow: SQLite `WITHOUT ROWID` exact-key B-tree
- ordinary checkpoints: `N={1k,4k,16k,64k,256k}`
- overflow stress: `O={1,16,64,256,1024,4096,16384}`
- concentrated stress first fills the primary's 16-key local capacity, then sends every additional key to overflow
- v0.21 D=8 stress retained as a control
- v0.16 semantic guard retained

## Measurement discipline

The primary-path page count is the v0.20 logical-page model. Overflow depth is SQLite `dbstat` B-tree root-to-leaf height for small non-overflow keys. The combined modeled lookup pages add those quantities.

This milestone does **not** measure or claim crash durability, page-split write cost, pager bookkeeping, cache effects, filesystem/device I/O, or production latency.

## Initial CI evidence

Implementation head:

`ef8505cabab976531c5e3622b0d0c185f193377e`

CI run **#214** passed the new experiment and the complete historical chain.

Artifact digest:

`sha256:4d1263d7114e55ba8c5a1155413cb39800f14171d0f7b917099f413731b70b1d`

Contained JSON SHA-256:

`496b717c24fac8e1e6096955ff087e315a289471be6ca37ebf33031012a3e9a1`

The branch now commits that parsed evidence as `rare_overflow_results.json` and replays it with `verify_rare_overflow_results.py`.

## Ordinary common-path result

At every ordinary checkpoint, overflow incidence is exactly zero and successful primary hits never query overflow.

| Membership rows | Primary max mutation work | Primary lookup page max | Overflow insertions | Primary-hit overflow checks |
|---:|---:|---:|---:|---:|
| 1,000 | 17 | 2 | 0 | 0 |
| 4,000 | 17 | 2 | 0 | 0 |
| 16,000 | 22 | 2 | 0 | 0 |
| 64,000 | 27 | 2 | 0 | 0 |
| 256,000 | 30 | 2 | 0 | 0 |

The primary's explicit theoretical placement cap remains 200 modeled slot operations throughout.

Thus, in this fixture, overflow growth is completely absent from the common successful-hit path.

## Exceptional-path result

After the concentrated primary path is saturated at 16 keys, every later key is admitted to the directly indexed overflow. All inserted keys remain retrievable.

| Overflow rows `O` | B-tree height | Overflow-hit modeled pages | Missing-key modeled pages | B-tree total pages |
|---:|---:|---:|---:|---:|
| 1 | 1 | 4 | 4 | 1 |
| 16 | 1 | 4 | 4 | 1 |
| 64 | 1 | 4 | 4 | 1 |
| 256 | 2 | 5 | 5 | 4 |
| 1,024 | 2 | 5 | 5 | 10 |
| 4,096 | 2 | 5 | 5 | 39 |
| 16,384 | 3 | 6 | 6 | 153 |

The saturated primary miss contributes 3 modeled pages. The remaining cost is the overflow B-tree root-to-leaf depth.

Therefore the observed exceptional lookup envelope is:

\[
\boxed{
ExceptionalLookupPages(O)=3+Height_{BTree}(O)
}
\]

and the measured height sequence is:

`1,1,1,2,2,2,3`.

## v0.21 control

The retained D=8 finite-escalation control attempts 256 concentrated keys and reproduces the hardened v0.21 result:

- successes: 128
- failures: 128
- maximum mutation work: 1,600
- missing-key lookup pages: 24
- reserved capacity slots: 8,192

The hybrid therefore changes the availability policy rather than pretending the finite-domain control had unlimited capacity.

## Semantic guard

All retained v0.16 correctness checks pass:

- membership equality
- materialization equality
- current-head equality
- all derived state fresh
- exact full profile assembly
- exact partial profile assembly

## Surviving result

The hypothesis survives as a **common/exceptional-path separation**, not as universal constant lookup.

\[
\boxed{
\begin{aligned}
PrimaryHit &\le 2\ \text{modeled pages in the tested ordinary fixture},\\
PrimaryPlacementWork &\le 200\ \text{modeled slot operations},\\
OverflowHit &= 3+\Theta(\log_B O),\\
MissingKey &= 3+\Theta(\log_B O).
\end{aligned}
}
\]

The important distinction is:

> Guaranteed admission can coexist with a bounded common path if exceptional cost is explicit. It does not make exceptional lookup constant.

## Deliberate non-claims

v0.22 does not establish:

- universal constant lookup
- constant overflow insertion/write cost
- physical OS/device I/O bounds
- crash-safe persistent overflow
- atomic integration with v0.19 incremental migration
- production latency or throughput
- adversarial hash security

The production membership index is therefore **not replaced yet**.

## Revision / next target

The next experiment should test whether this hybrid admission policy remains correct when made durable and integrated with incremental migration. The critical questions are now transactional rather than purely geometric:

1. Can a key move between bounded primary generations and overflow without duplicate/lost membership?
2. Can a crash occur at every migration/overflow boundary and recover idempotently?
3. Can reads avoid stale or missing membership while migration and overflow admission overlap?
4. Can the common path remain isolated after persistence metadata is added?
5. What write amplification and recovery work does overflow introduce?

That is the appropriate v0.23 target if the final exact-head replay gate also passes.
