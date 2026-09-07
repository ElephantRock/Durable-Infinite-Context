# v0.19 — Incremental hash migration

## Problem

v0.18 showed that bounded-load open addressing can keep successful point lookup extremely local while still failing the stronger mutation-locality requirement: a conventional capacity doubling rehashes every live row in the triggering mutation. At 256k rows, that single stop-the-world event moved 131,072 rows.

The next question was whether resize work can be scheduled incrementally instead of charged to one logical mutation.

## Hypothesis

A two-generation open-addressed hash index with a fixed source-slot migration budget can remove the stop-the-world `Theta(N)` resize charge from one mutation while:

1. completing migration under sustained insertion;
2. limiting live lookup to at most two generations; and
3. keeping temporary slot-capacity amplification bounded.

This milestone deliberately tests the algorithmic mechanism before paying for persistent/crash-safe implementation complexity.

## Mechanism

When the current generation would exceed load `0.50`, v0.19 allocates a generation with twice the capacity and keeps the old generation readable. New inserts go to the new generation. Every subsequent insertion scans at most **8 old-generation slots**, copying any live rows found there. Lookups probe the new generation first and, while migration is active, the old generation second.

The model counts source slots scanned, destination linear-probe work, logical slot pages, generation fan-out, and allocated slot arrays. It does **not** model operating-system or device I/O, persistence, allocator behavior, or crash recovery.

## Initial successful CI evidence

The first fixed experiment completed successfully in CI run **#190** on head:

`0f49551e65bb98f23ce33c26442f8b36fcc9038a`

Artifact:

`v0.19-incremental-hash-results`

Artifact digest:

`sha256:f7b1d2d23939c1f45ce17df8d331636a068c3ce7e41ac6123376682c56183dbc`

Contained JSON payload SHA-256:

`f04bd9c62c85ffb241ed6cd7e9642ddac3c4dcb07f4e1f6725de3615341eb8e8`

The committed ledger is subsequently replayed exactly by `verify_incremental_hash_results.py`; final merge remains gated on the exact documentation/verifier head.

## Fixed growth sweep

The stop-the-world control and incremental candidate use the same `N={1k,4k,16k,64k,256k}`, initial capacity 128, maximum load 0.50, and 64 slots per logical page.

| Membership rows `N` | v0.18 largest single rehash | v0.19 max source slots / insert | v0.19 max rows copied / insert | v0.19 interval max total mutation slot work | steady lookup page max |
|---:|---:|---:|---:|---:|---:|
| 1,000 | 512 | **8** | **8** | 26 | 2 |
| 4,000 | 2,048 | **8** | **8** | 26 | 2 |
| 16,000 | 8,192 | **8** | **8** | 35 | 2 |
| 64,000 | 32,768 | **8** | **8** | 39 | 2 |
| 256,000 | 131,072 | **8** | **8** | 50 | 2 |

The stop-the-world spike grows by four orders of magnitude across the sweep. The incremental source scan remains exactly at its configured upper bound of eight slots, and no insertion copies more than eight rows.

## Migration completion

All twelve capacity migrations complete under continuous insertion. Because the source table is scanned eight slots per insertion, a source generation of capacity `C` completes in exactly `C/8` insertions in this fixture:

| Source capacity | Source live rows | Insertions spanning migration |
|---:|---:|---:|
| 128 | 64 | 16 |
| 1,024 | 512 | 128 |
| 16,384 | 8,192 | 2,048 |
| 262,144 | 131,072 | 32,768 |

Thus migration duration still grows with the source generation. The result is a bound on **work charged to one insertion**, not a claim that total migration time or total work is constant.

Cumulative row copies through 256k rows are **262,080**, exactly the same live-row rehash count as the stop-the-world control. Incremental scheduling distributes that work; it does not erase it. The scanner examines **524,160 source slots** in total because the source tables are held at approximately 0.50 load.

## Read amplification during live migration

A deterministic sample is taken halfway through every migration. Across all twelve live-migration snapshots:

- lookup generation p95 = **2**;
- lookup generation max = **2**;
- lookup page p95 = **2**;
- lookup page max = **3**;
- temporary allocated slot capacity = **1.5x** the target/new generation.

At the fixed checkpoints, migration has completed, so lookup returns to one generation with page p95 1 and page max 2.

## Important negative evidence

The fixed source-scan budget does **not** bound all mutation work. Destination rows are still placed using linear probing. The observed interval maximum total mutation slot work is:

`[26, 26, 35, 39, 50]`

for `N={1k,4k,16k,64k,256k}`.

This does not prove a particular asymptotic from five points, but it is sufficient to reject the stronger interpretation that “migration budget = 8” means “total mutation work is bounded by a constant eight.” Linear probing retains an unbounded collision/cluster tail, and taking a maximum over a larger workload exposes that tail more strongly.

## Surviving result

For the tested deterministic fixture and fixed migration budget `B=8`:

\[
\boxed{
\begin{aligned}
SourceScanPerMutation &\le B,\\
RowsCopiedPerMutation &\le B,\\
LookupGenerationFanout &\le 2,\\
TemporaryCapacityAmplification &\le 1.5,\\
MigrationDuration(C) &= C/B\text{ insertions in this scanner},\\
TotalRehashRowWork &= \Theta(N)\text{ over table growth.}
\end{aligned}
}
\]

The mechanism therefore **survives as a resize scheduler**: it removes the demonstrated `Theta(N)` stop-the-world rehash charge from a single insertion. It does **not** yet establish globally bounded total mutation work.

## Deliberate non-claims

v0.19 does not establish:

- worst-case constant destination placement work;
- adversarial-collision robustness;
- physical page-I/O bounds;
- persistent two-generation metadata correctness;
- crash recovery during migration;
- stale-read safety across a crash or restart;
- production latency or write amplification.

## Revision / next falsification target

Before implementing persistent migration, v0.20 should attack the remaining placement tail directly. A candidate must provide a defensible bound or failure mode for collision/relocation work under both growing random-like keys and controlled collision stress. Possible controls include bounded buckets plus overflow, cuckoo-style relocation with a strict kick budget/stash, or another structure whose locality contract can be stated without hiding an unbounded linear-probe chain.

Only if that placement mechanism survives should the project pay for the next mandatory gate: durable migration metadata, crash/restart replay, and stale-read protection.
