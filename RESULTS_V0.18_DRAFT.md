# v0.18 — bounded-load hash resize falsification target

## Observation

v0.17 falsifies fixed finite B-tree sharding as an asymptotic constant-page lookup mechanism. A direct-address/hash structure is the natural next candidate because a bounded-load hash table can offer expected constant point-lookup probes.

That lookup property alone is not sufficient for Durable Infinite Context. If maintaining the hash structure introduces global work during growth, the system merely moves the non-locality from reads to writes.

## Hypothesis under test

A conventional bounded-load, open-addressed hash index with stop-the-world capacity doubling is sufficient when the requirement is both:

1. expected `O(1)` point-lookup page probes; and
2. mutation work independent of global durable cardinality `N`.

## Prediction

If the mechanism is sufficient, then as `N` grows:

- successful point lookup should remain inside a small page-probe envelope; and
- the largest relocation/migration work charged to any single insertion must remain bounded.

## Fixed experiment

The runner:

- preserves the v0.16 semantic guard before testing the alternative address structure;
- uses a deterministic keyed BLAKE2b hash, power-of-two capacity, linear probing, 64 slots per logical page, and maximum load factor `0.50`;
- sweeps `N={1k,4k,16k,64k,256k}`;
- measures successful lookup page-probe p50/p95/max over a deterministic sample;
- records every capacity doubling and the number of previously live rows that must be rehashed;
- explicitly treats the hash implementation as an algorithmic page model rather than an OS/device I/O measurement.

No v0.18 result is claimed until CI executes the fixed runner and its artifact is inspected.
