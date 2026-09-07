# v0.20 — Bounded placement locality

## Problem

v0.19 removed the demonstrated stop-the-world `Theta(N)` resize charge from one insertion by distributing source migration across future mutations. But total mutation work still retained a growing tail because destination placement used ordinary linear probing. The observed maximum mutation slot work rose from 26 to 50 across the v0.19 growth sweep.

v0.20 therefore asks a narrower question: can the placement primitive itself have an explicit, global-`N`-independent work cap without hiding an arbitrarily long collision chain?

## Hypothesis

A two-choice bucketized cuckoo placement primitive with finite relocation and stash budgets can replace the unbounded linear-probe tail with an explicit mutation-work cap while retaining shallow lookup. If local collision pressure exceeds the finite placement domain, insertion must fail explicitly and leave all previously admitted keys intact rather than extending an unbounded chain.

## Mechanism

The fixed candidate uses:

- 4 slots per bucket;
- 2 candidate buckets per key;
- at most 32 cuckoo relocations;
- an 8-entry finite stash;
- deterministic rollback if relocation exhausts the kick budget and the stash is full.

The corresponding modeled worst-case insertion work is:

\[
2b + Kb + K + K
\]

with bucket size `b=4` and kick budget `K=32`, giving:

\[
\boxed{MutationSlotWork \le 200}
\]

The model counts bucket-slot inspections, relocation writes, stash writes, and rollback writes. It does not model persistence, allocator/device I/O, or crash recovery.

## Initial successful CI evidence

CI run **#199** completed successfully on head:

`721dd889ffc95f34ebe877018b3e9063352633de`

Artifact:

`v0.20-bounded-placement-results`

Artifact digest:

`sha256:c926c2aea057f07e5e5f964906ab3fd857e4c30a8032506db14f32bcd499b1cc`

Contained JSON payload SHA-256:

`9caca946f3045d2f9fddaedcbc31f286551856791d0eb947bef96eb549dfbe01`

The committed ledger is replayed exactly by `verify_bounded_placement_results.py`; final merge remains gated on the exact documentation/verifier head.

## Ordinary keyed-hash growth

The candidate is compared against fixed-capacity linear probing at approximately the same 0.488 load used in the preceding hash fixtures.

| Membership rows `N` | Linear max insert probes | Cuckoo failures | Cuckoo stash peak | Cuckoo max relocations | Cuckoo max mutation work | Cuckoo lookup page max |
|---:|---:|---:|---:|---:|---:|---:|
| 1,000 | 11 | **0** | **0** | 1 | **17** | **2** |
| 4,000 | 19 | **0** | **0** | 1 | **17** | **2** |
| 16,000 | 21 | **0** | **0** | 2 | **22** | **2** |
| 64,000 | 31 | **0** | **0** | 3 | **27** | **2** |
| 256,000 | 34 | **0** | **0** | 4 | **30** | **2** |

The ordinary fixture is not evidence that cuckoo insertion is always cheap; it is evidence that the fixed candidate stays far below its explicit 200-operation cap on these deterministic keyed hashes while the linear-probe control exposes a larger maximum as the workload grows.

Lookup remained shallow in the ordinary fixture: page p95 was 1 and page max was 2 throughout, with at most eight bucket slots inspected.

## Controlled collision stress

The discriminating test maps every stress key to the same linear-probe start slot and the same two cuckoo buckets.

| Colliding keys | Linear max probes | Cuckoo successes | Cuckoo failures | Stash size | Cuckoo max work | Successful keys still found |
|---:|---:|---:|---:|---:|---:|---:|
| 8 | 8 | **8** | 0 | 0 | 8 | 8 |
| 16 | 16 | **16** | 0 | 8 | 169 | 16 |
| 17 | 17 | **16** | **1** | 8 | **200** | 16 |
| 32 | 32 | **16** | 16 | 8 | **200** | 16 |
| 64 | 64 | **16** | 48 | 8 | **200** | 16 |
| 128 | 128 | **16** | 112 | 8 | **200** | 16 |

The linear control forms a probe chain exactly as wide as the collision set. The bounded candidate instead has finite local admission capacity: two 4-slot buckets plus an 8-entry stash admit exactly 16 concentrated keys. The 17th and later keys fail at the explicit work cap rather than extending work with collision width.

Rollback preserves every previously admitted key after those failures.

## Surviving result

For this fixed candidate:

\[
\boxed{
\begin{aligned}
MutationSlotWork &\le 200,\\
OrdinaryLookupBucketPages &\le 2,\\
ConcentratedDomainCapacity &= 2\cdot4 + 8 = 16,\\
ExcessCollisionDemand &\rightarrow \text{explicit bounded failure}.
\end{aligned}
}
\]

This is stronger than the v0.19 linear-probe placement tail because the locality contract has a finite worst-case work bound in the model.

However, bounded work is achieved partly by permitting bounded failure. Therefore v0.20 does **not** establish a complete durable address index.

## Important negative evidence

The 17-key collision fixture is the key counterexample to an overclaim. A finite relocation/stash budget cannot guarantee both unlimited admission and fixed local work in one concentrated two-bucket domain. The candidate preserves locality by rejecting excess demand.

So the surviving distinction is:

\[
\boxed{BoundedPlacementWork \neq GuaranteedInsertionAvailability}
\]

## Deliberate non-claims

v0.20 does not establish:

- zero-failure insertion under arbitrary keys;
- adversarial-hash security beyond the controlled fixture;
- a bounded escape path after local placement failure;
- integration with v0.19 incremental migration;
- persistent/crash-safe placement metadata;
- physical OS/device page-I/O bounds;
- production latency or allocator/write-amplification cost.

## Revision / next falsification target

v0.21 should test a bounded escape path for local placement failure. A surviving mechanism must preserve an explicit mutation bound and bounded lookup fan-out while avoiding both global rehash and an unbounded overflow chain. Candidate mechanisms include a fixed number of independent bounded placement domains, a small bounded overflow directory with deterministic escalation, or another scheme whose failure/space contract is explicit.

Only after admission availability and placement locality survive together should the project integrate bounded placement with incremental migration and subject the result to durable crash/restart and stale-read tests.
