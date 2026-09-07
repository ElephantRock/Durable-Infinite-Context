# v0.17 — B-tree page-locality falsification target

## Observation

v0.16 removes the serialized `O(P)` predicate manifest and keeps selective SQL-returned bytes and VM instruction counts flat under the tested global-`N` sweep. However, `dbstat` reports that the normalized membership B-tree height grows as global durable memory grows.

## Hypothesis under test

A fixed number of hash-partitioned B-tree membership indexes is sufficient to make cold point-lookup index-page traversal independent of global cardinality `N`.

## Prediction

If that mechanism is sufficient, then with a fixed shard count `S`, the maximum shard root-to-leaf height must remain constant as `N` grows while point lookups remain exact and index-backed.

## Fixed experiment

The runner:

- preserves a v0.16 semantic guard;
- builds the same small `(subject,predicate)` membership keys in one global B-tree and 64 deterministic hash shards;
- uses SQLite `dbstat` to measure root-to-leaf B-tree height at `N={1k,10k,50k,250k,1m}` with 4096-byte pages;
- verifies exact point lookup through the intended indexes;
- rejects any measurement where the small keys spill to overflow pages;
- treats B-tree height as the cold index-page search-path count only, not as an OS/device read counter.

No v0.17 result is claimed until CI executes the fixed experiment and its artifact is inspected.
