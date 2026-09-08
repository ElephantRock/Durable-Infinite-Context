# v0.23 — Durable hybrid admission

## Observe

v0.22 earned an honest common/exceptional lookup split: a bounded bucketized primary can keep ordinary successful lookups local while an exact-key B-tree overflow guarantees admission after bounded placement exhaustion. That result was still algorithmic. It did not show that incremental generation migration, overflow routing, and their metadata survive process death as one coherent durable state transition.

The existing repository already had a stronger durability lesson from v0.9: SQLite WAL plus `synchronous=FULL` can make a transaction boundary survive real `SIGKILL`, but v0.23 had to test the new hybrid state machine rather than inherit that conclusion by analogy.

## Diagnose

A persistent hybrid creates new torn-state hazards:

- a key could be visible in neither generation after a crash;
- a migrated key could exist in both generations or in primary plus overflow;
- the generation cursor could advance without the corresponding row moves;
- overflow routing could commit without live-size/count metadata, or vice versa;
- a committed insertion could disappear after restart;
- an uncommitted insertion could leak through a crash;
- recovery metadata could impose a new common-path scan or application redo protocol.

Those are transactional-consistency questions. They are distinct from the physical page-locality question left open by v0.17 and later milestones.

## Derive

Commit the entire logical admission boundary in one SQLite transaction:

1. route/check the new key;
2. start a generation migration if the load threshold requires it;
3. place the new key in bounded primary or explicit overflow;
4. scan at most eight virtual source slots;
5. move the live rows found there to the new bounded primary or overflow;
6. advance/complete generation metadata;
7. update live-size and overflow counts;
8. commit all of the above atomically.

For migration budget `m=8` and v0.20 placement cap `C=200`, one insertion plus at most eight migrated rows gives the conservative modeled primary-work envelope

\[
W_{primary} \le m + (m+1)C = 8 + 9(200)=1808.
\]

This is a logical placement/migration bound. It is not a physical SQLite page-write bound.

## Hypothesis

A persistent bounded-primary / bounded-migration / explicit-overflow hybrid can commit each admission process-crash atomically such that `SIGKILL` exposes only the exact pre-operation or exact post-operation logical state, while ordinary successful reads remain overflow-independent and migration source work remains bounded.

## Prediction

Under SQLite WAL with `synchronous=FULL` and one writer:

- `route_uncommitted` and `final_uncommitted` process death must expose the exact deterministic pre-operation snapshot;
- `committed` process death must expose the exact deterministic post-operation snapshot;
- no crash image may lose or duplicate membership;
- two consecutive application recovery passes must be idempotent and require zero logical redo/repair;
- source migration must never scan or move more than eight rows per admission;
- modeled primary mutation work must remain below 1808;
- an ordinary successful lookup must read one singleton routing descriptor and must not query overflow;
- exceptional lookup must continue to expose the overflow B-tree's growing geometry rather than be reported as constant.

## Test

The fixed crash matrix contains five scenarios:

1. ordinary insertion;
2. migration start;
3. migration progress;
4. direct overflow admission;
5. migration that routes source rows to overflow.

Each scenario is killed at three transaction boundaries: `route_uncommitted`, `final_uncommitted`, and `committed`, for **15 real `SIGKILL` cases**.

Every crash database is compared with independently executed deterministic pre/post controls. The oracle compares the complete logical snapshot, not only target-key visibility, and then audits all pre-existing membership plus generation/overflow metadata. Recovery is executed twice.

The ordinary persistent envelope uses `N={256,1024,4096}`. The exceptional overflow envelope uses `O={1,16,64,256,1024}`.

Initial successful CI evidence is anchored in `durable_hybrid_evidence.json`; executable replay is enforced by `verify_durable_hybrid_results.py` against the exact initial `durable_hybrid_results.json` SHA-256.

## Result

The hypothesis **survived in the tested envelope**.

All **15/15** process-crash cases exposed the exact pre- or post-transaction logical snapshot required by the commit boundary. Every case retained valid membership, found all pre-existing keys, and passed two idempotent recovery passes with **zero application-level redo, row repair, or metadata repair**.

Ordinary persistent growth produced:

| Membership rows | Max modeled primary mutation work in interval | Max source slots scanned | Max rows moved | Overflow rows | Successful lookup overflow checks | Routing metadata rows |
|---:|---:|---:|---:|---:|---:|---:|
| 256 | 27 | 8 | 8 | 0 | 0 | 1 |
| 1,024 | 30 | 8 | 8 | 0 | 0 | 1 |
| 4,096 | 34 | 8 | 8 | 0 | 0 | 1 |

Across this envelope, six migrations started and all six completed. The observed global maximum modeled primary mutation work was **34**, far below the derived 1808 bound. Successful sampled lookups touched at most two logical primary bucket pages, one routing metadata row, and zero overflow lookups.

The persistent overflow remained explicit:

| Overflow rows `O` | B-tree height | Overflow-hit modeled pages | Missing-key modeled pages |
|---:|---:|---:|---:|
| 1 | 1 | 4 | 4 |
| 16 | 1 | 4 | 4 |
| 64 | 1 | 4 | 4 |
| 256 | 2 | 5 | 5 |
| 1,024 | 2 | 5 | 5 |

The saturated primary contributes three modeled logical pages before exceptional B-tree lookup, so the v0.22 separation remains visible rather than being hidden by persistence.

A deliberately hostile `migration_to_overflow` committed case also exercised migration completion while source rows were diverted to overflow; the same transaction-boundary oracle still held.

## Revise

The durable conclusion is narrower than "persistent constant-time hash storage."

SQLite WAL transaction atomicity is sufficient, in this single-writer local-process experiment, to persist the bounded migration plus overflow state machine **without an application recovery journal or redo pass**. Persistence metadata did not contaminate the tested ordinary read path beyond one singleton routing row.

However, the persistent primary representation uses SQLite `WITHOUT ROWID` tables keyed by generation/bucket/slot. Those tables are themselves comparison B-trees. Therefore v0.23 does **not** prove direct-address physical page locality for the primary path. Its bucket/stash `page_probes` are logical model pages, not SQLite pager, filesystem, or device reads.

Likewise, `primary_sql_row_writes` and `overflow_row_writes` count explicit logical SQL row mutations. They do not measure SQLite page rewrites/splits, WAL frames, fsyncs, filesystem writes, or storage-device write amplification.

## Engineer

The surviving production-oriented contract is now:

\[
\boxed{
\begin{aligned}
CrashImage &\in \{ExactPreTransaction, ExactPostTransaction\}\\
ApplicationRedo &= 0\\
MigrationSourceWork &\le 8\\
OrdinaryOverflowChecks &= 0\\
OrdinaryRoutingMetadataRows &= 1
\end{aligned}}
\]

within the tested single-writer SQLite WAL model.

The next discriminating target should therefore move down one layer rather than adding more logical crash machinery:

> **Can a persistent bounded primary eliminate comparison-tree root-to-leaf growth on its common path while preserving v0.23 crash atomicity and the explicit overflow contract?**

A v0.24 experiment should compare the current SQLite B-tree-backed primary against a fixed-page/page-addressed primary representation and measure actual persistent page topology and write/recovery obligations. It must not equate logical bucket addresses with physical page I/O, and the new representation must earn its additional allocator, versioning, checksum, and recovery complexity.

## Measurement scope

Validated here:

- exact logical SQLite state after real process `SIGKILL`;
- WAL + `synchronous=FULL` transaction boundary in the tested stack;
- one-writer hybrid membership correctness;
- bounded logical primary placement/migration work;
- logical bucket/stash page probes;
- singleton routing-metadata row work;
- overflow B-tree height through SQLite `dbstat`.

Not validated here:

- OS/device page-read counts;
- physical direct-address primary locality;
- WAL-frame or device write amplification;
- hardware power loss beyond the tested SQLite/storage stack;
- multi-writer/distributed semantics;
- production latency or cost.
