# v0.17 — B-tree page-locality falsification

## Problem / observation

v0.16 removed the serialized `O(P)` predicate manifest and made selective returned bytes, membership probes, and VM-step counts independent of total live predicate count `P` under the tested workload. But its normalized predicate membership remains a global SQLite B-tree. The v0.16 global-`N` sweep already exposed a height change from 2 to 3.

Counting one SQL `SEARCH` or one VM `Seek` as constant work therefore hid a lower-level physical dependence on global index size.

## First principle

A comparison B-tree with finite page fan-out cannot locate an arbitrary key among an unbounded number of entries in a fixed number of root-to-leaf levels. If a fixed number `S` of such trees partitions the keys, each tree still contains `Theta(N/S)` entries, so:

\[
LookupPages = \Theta(\log_B(N/S)) = \Theta(\log_B N)
\]

for fixed finite `S` and page fan-out `B`.

Fixed sharding can move a height threshold. It cannot change that asymptotic class.

## Hypothesis tested

> A fixed number of hash-partitioned B-tree membership indexes is sufficient to make cold point-lookup index-page traversal independent of global cardinality `N`.

The discriminating prediction was that the maximum root-to-leaf shard height would remain constant as `N` grew while exact point lookup stayed index-backed.

## Measurement

The experiment builds identical small `(subject,predicate)` keys in:

1. one global membership B-tree; and
2. 64 deterministic hash-partitioned B-tree indexes.

It uses 4096-byte SQLite pages and sweeps:

`N={1,000, 10,000, 50,000, 250,000, 1,000,000}`.

SQLite `dbstat` root-to-leaf height is used as the number of index B-tree pages on the search path for these small non-overflow keys. Exact point lookup and intended-index use are checked at every scale. This does **not** claim to count `sqlite_schema`, pager bookkeeping, filesystem metadata, storage-device reads, or cache hits.

A v0.16 semantic guard also passed: membership/head parity, clean-rebuild parity, freshness, and exact full/partial logical profile semantics remained intact.

## CI evidence

| Membership rows `N` | Global height/pages | 64-way max shard rows | 64-way max shard height/pages |
|---:|---:|---:|---:|
| 1,000 | 2 | 28 | 1 |
| 10,000 | 2 | 188 | 2 |
| 50,000 | 3 | 840 | 2 |
| 250,000 | 3 | 4,077 | 2 |
| 1,000,000 | 3 | 15,932 | 3 |

The fixed partition is useful at intermediate scale: at `N=50k` and `250k`, it keeps the measured shard path at two pages while the global B-tree requires three.

But at `N=1m`, the maximum 64-way shard height also becomes three. The prediction of a fixed page bound is therefore falsified.

Initial CI artifact digest:

`sha256:e31803b9215306e2d495f83d4a08355887c7316cdedf5529dc333a84d85df4da`

The committed machine-readable evidence is `page_locality_results.json`; `verify_page_locality_results.py` replays the fixed experiment and requires exact evidence reproduction.

## Revision

The repository must no longer use either of these as evidence for physical global-memory independence:

- a SQL plan that says `SEARCH` rather than `SCAN`;
- a constant SQLite VM `Seek` count.

For the current comparison-index architecture, the defensible lookup contract is logarithmic in global indexed cardinality at the page level, not constant:

\[
\boxed{
AddressLookupPages = O(\log_B N)
}
\]

while previously established locality claims remain separate:

\[
Maintenance_{evidence/value}=O(K)
\]

under the project's logical accounting, and full requested output remains proportional to its true semantic footprint.

## Engineering decision

Do **not** merge fixed B-tree sharding as an asymptotic solution. The experiment shows that it only delays page-height transitions.

Two candidate directions remain:

1. accept `O(log_B N)` indexed addressability as the correct durable-memory contract and test whether the resulting latency remains practically bounded at much larger `N`; or
2. test a growing direct-address/hash structure whose bucket occupancy is actively bounded, including its resize/split, crash-recovery, and consistency costs.

The next experiment should discriminate between those choices rather than assume strict `O(1)` page locality is required.

## Scope / non-claims

v0.17 does not measure operating-system page faults or storage-device reads. It does not establish production latency. The 64-way partition is a falsification control, not a proposed production architecture. No claim is made that logarithmic B-tree lookup is unacceptable; only the stronger constant-page claim is rejected.
