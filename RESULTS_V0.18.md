# v0.18 — bounded-load hash resize envelope

## Observation

v0.17 established that neither one global B-tree nor any fixed finite number of B-tree shards gives global-`N`-independent root-to-leaf page depth. A hash/direct-address structure is therefore a plausible candidate if strict expected point-lookup locality is worth pursuing.

The candidate must be judged on both sides of the trade: read locality and growth/mutation locality.

## Hypothesis tested

A conventional bounded-load, open-addressed hash index with stop-the-world capacity doubling is sufficient to provide both:

\[
ExpectedLookupPageProbes=O(1)
\]

and

\[
MutationGrowthWork=O(1)
\]

independent of global durable cardinality `N`.

## Fixed experiment

The experiment uses a deterministic keyed BLAKE2b hash, power-of-two capacity, linear probing, 64 slots per logical page, and a maximum load factor of `0.50`. It sweeps:

`N={1k,4k,16k,64k,256k}`

and samples 512 successful point lookups at each checkpoint. It records logical page probes and every capacity-doubling migration.

This is an **algorithmic page model**, not an OS/device I/O measurement. The experiment deliberately asks whether the mechanism is sufficient before implementing a new crash-safe physical storage engine.

## Result

**The sufficiency hypothesis is falsified.**

The attractive part survives strongly:

| Membership rows | Lookup page p50 | Lookup page p95 | Lookup page max |
|---:|---:|---:|---:|
| 1,000 | 1 | 1 | 2 |
| 4,000 | 1 | 1 | 2 |
| 16,000 | 1 | 1 | 2 |
| 64,000 | 1 | 1 | 2 |
| 256,000 | 1 | 1 | 2 |

At the tested load factor, ordinary successful lookup remains inside a tiny expected page envelope.

But growth introduces the opposite failure:

| Membership rows | Largest single resize migration |
|---:|---:|
| 1,000 | 512 rows |
| 4,000 | 2,048 rows |
| 16,000 | 8,192 rows |
| 64,000 | 32,768 rows |
| 256,000 | 131,072 rows |

Every doubling rehashes every row live before that insertion. The triggering mutation therefore pays:

\[
\boxed{ResizeSpike(N)=\Theta(N)}
\]

The cumulative rehash work through 256k rows is **262,080 row migrations** in addition to the ordinary insertions.

The existing semantic guard also passed: normalized membership parity, clean-rebuild parity, current-head parity, freshness, and exact full/partial logical profile semantics remained intact before testing the alternative address structure.

Initial successful CI artifact digest:

`sha256:ce960ad0f660d88cfb504814dfac9432d77418428c1aeff754feacf48f65d412`

## Revision

The relevant requirement is no longer merely:

\[
ExpectedPointLookup=O(1)
\]

It is:

\[
\boxed{
ExpectedPointLookup=O(1)
\quad\text{and}\quad
PerMutationMigration=O(1)\text{ or otherwise bounded}
}
\]

A mechanism that turns logarithmic read depth into periodic global write spikes has not established stronger locality for the durable system as a whole.

## Engineering decision

Do **not** replace the current production B-tree membership index with a conventional stop-the-world resized hash table.

The next falsification target should test incremental growth: linear hashing, extendible hashing, or dual-generation rehash with a strict migration budget per logical mutation. That mechanism must then be tested for lookup amplification during migration, bounded per-mutation movement, space amplification, crash recovery, and stale-read protection before it can displace the much simpler B-tree.

Machine-readable evidence is in `hash_resize_results.json`; executable replay is `verify_hash_resize_results.py`.
