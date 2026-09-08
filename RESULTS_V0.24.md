# v0.24 — Fixed-page primary locality

## Observe

v0.23 earned process-crash atomicity for bounded generation migration plus explicit overflow, but the persistent primary was stored in SQLite `WITHOUT ROWID` tables. Those tables are comparison B-trees, so the experiment still had no evidence that a primary bucket could be reached without a root-to-leaf comparison-index traversal whose physical depth can grow with global cardinality.

The remaining question was below the logical hash model: can the primary itself be represented as fixed-size persistent pages whose byte offsets are computed directly from bucket identity, while retaining a crash-atomic commit boundary?

## Diagnose

Moving below SQLite removes a large amount of storage-engine machinery and exposes new obligations directly:

- page addressing must not hide another comparison index;
- partially written page state must not become committed logical state;
- a metadata/root switch must be durable only after all referenced data pages are durable;
- migration still has to obey the eight-source-slot budget;
- direct addressing must remain fixed across growth checkpoints rather than silently adding a directory tree;
- the bounded-placement failure control must remain state-preserving;
- stale uncommitted physical pages may remain after a process crash even when logical recovery is exact.

The last point matters because logical crash atomicity and physical space reclamation are different properties.

## Derive

The candidate uses fixed **4096-byte** records and two physical copies for every logical primary page. Two fixed superblock copies hold the committed epoch and routing metadata.

For logical page id `p` and copy slot `c in {0,1}`, the address is computed arithmetically:

\[
PhysicalPageIndex = 2 + 2p + c
\]

\[
ByteOffset = 4096(2 + 2p + c).
\]

There is no primary comparison index or page-directory tree.

For commit epoch `T`:

1. write changed logical pages into their alternate physical copies tagged for `T`;
2. `fsync` the data file;
3. write the alternate superblock declaring `T` committed;
4. `fsync` again.

A reopen selects the newest valid CRC-protected superblock and ignores page copies newer than the committed epoch.

The v0.23 source-migration budget remains `m=8` source slots per logical admission.

## Hypothesis

A dual-copy fixed-page primary with arithmetic addressing and a fixed-size dual-superblock commit record can remove growing comparison-index traversal from the primary lookup path while preserving the tested v0.23 process-crash atomicity contract.

## Prediction

The fixed experiment predicts:

- `SIGKILL` after page writes but before the data `fsync` exposes the exact pre-operation logical snapshot;
- `SIGKILL` after the data `fsync` but before the committed superblock exposes the exact pre-operation logical snapshot;
- `SIGKILL` after the committed superblock `fsync` exposes the exact post-operation logical snapshot;
- recovery requires zero application-level logical redo;
- source migration scans/moves at most eight source slots/rows;
- a single-generation lookup performs two superblock `os.pread` calls plus at most three dual-copy primary-page reads, for at most **8 user-space `os.pread` calls**;
- a two-generation missing lookup performs two superblock reads plus six dual-copy primary-page reads, for exactly **14 user-space `os.pread` calls**;
- the 17th concentrated two-bucket collision remains an explicit state-preserving rejection because overflow is intentionally not integrated in this milestone.

`os.pread` invocation count is a user-space call count, not an OS/device-read count.

## Test

The crash matrix has three scenarios:

1. ordinary insertion;
2. migration start;
3. migration progress.

Each is killed at `pages_written`, `data_synced`, and `committed`, giving **9 real `SIGKILL` cases**. Every crash image is compared with deterministic pre/post logical snapshots and then reopened twice through the recovery path.

The ordinary growth envelope is `N={256,1024,4096,16384}`. It measures exact user-space `os.pread`/`os.pwrite` calls, arithmetic offsets, logical pages written, migration work, sparse file length, and allocated bytes. A separate fixture measures lookup while two generations are active. The concentrated-collision control fills 16 keys and attempts a 17th.

Initial successful CI evidence is anchored by `fixed_page_primary_evidence.json`; replay is enforced by `verify_fixed_page_primary_results.py` against the exact initial `fixed_page_primary_results.json` SHA-256.

## Result

The primary hypothesis **survived in the tested logical and user-space-I/O envelope**.

All **9/9** real process-crash cases produced the exact deterministic pre- or post-operation logical snapshot required by the committed epoch. Every case preserved all pre-existing keys, passed the membership audit, and two consecutive recovery passes required **zero logical redo**.

Ordinary fixed-page growth produced:

| Membership rows | Current capacity | Max source slots | Max rows moved | Max logical pages written / mutation | Max placement work | Successful lookup max `pread` calls | Missing lookup `pread` calls |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 256 | 512 | 8 | 7 | 7 | 19 | 6 | 8 |
| 1,024 | 2,048 | 8 | 8 | 9 | 26 | 6 | 8 |
| 4,096 | 8,192 | 8 | 8 | 9 | 39 | 6 | 8 |
| 16,384 | 32,768 | 8 | 8 | 12 | 39 | 6 | 8 |

Across the envelope, **8 migrations started and all 8 completed**. The global source-scan and moved-row maxima were both **8**. The primary address formula remained fixed and `primary_index_structure` remained `none`.

During an active two-generation migration, successful lookups touched at most two generations and used at most **12** total `os.pread` calls; the missing-key path used exactly **14**.

The concentrated-collision control remained honest: 16 keys were admitted, the 17th was rejected, and the committed logical snapshot was unchanged.

### Crash-space observation

The crash matrix exposed a new lower-layer distinction. In the `migration_start` cases killed before superblock commit, logical recovery was exact but the file retained **139,264 bytes of unreachable physical tail** written by the uncommitted generation allocation.

This is not a membership or commit-atomicity failure: the committed superblock does not reference those bytes, and recovery needs no logical redo. It is nevertheless a real storage-layer obligation. The experiment therefore does **not** claim that crash recovery is physically garbage-free or that space reclamation is already crash-safe.

## Revise

v0.24 removes the specific comparison-tree dependency that remained in the v0.23 primary experiment. In the tested implementation, the primary bucket path is derived by fixed arithmetic and user-space lookup call counts stayed constant across `N=256..16,384`.

That is narrower than a claim of constant physical storage I/O. `os.pread` and `os.pwrite` calls may be served by caches; the experiment does not observe block-device requests, page-cache misses, controller operations, or media latency.

The commit protocol also relies on the tested process-crash/filesystem stack. CRC plus dual copies and ordered `fsync` calls do not by themselves prove arbitrary hardware torn-write or power-loss behavior on every filesystem/device combination.

Finally, v0.24 deliberately omits the v0.22/v0.23 exact-key overflow path. Universal admission has therefore not yet been re-earned for the fixed-page representation.

## Engineer

The surviving experimental contract is:

\[
\boxed{
\begin{aligned}
PrimaryAddress(p,c) &= 4096(2+2p+c)\\
PrimaryComparisonIndex &= \varnothing\\
CrashImage &\in \{ExactPreCommit, ExactPostCommit\}\\
ApplicationLogicalRedo &= 0\\
MigrationSourceSlots &\le 8\\
SingleGenerationMissPreads &= 8\\
TwoGenerationMissPreads &= 14
\end{aligned}}
\]

for the tested single-writer process-crash model.

The next discriminating experiment should combine the two remaining requirements rather than treating them independently:

> **Can the fixed-page primary and exact exceptional overflow share one crash-atomic commit protocol, while bounding common-path user-space page work and making physical reclamation/write amplification explicit?**

A v0.25 candidate should integrate exact overflow with the fixed-page primary, include crash boundaries that span both stores, and measure at least:

- fixed-page data/superblock writes per mutation;
- overflow page/WAL writes rather than only logical row writes;
- `fsync` count and ordering;
- exceptional lookup amplification;
- stale-tail/reclamation behavior after aborted growth;
- whether reclamation can be made idempotent without contaminating the bounded common path.

The current v0.23 SQLite hybrid remains the durability/admission control until that combined candidate survives.

## Measurement scope

Validated here:

- exact logical state after real process `SIGKILL`;
- fixed 4096-byte record images with CRC;
- arithmetic primary-page byte offsets;
- exact user-space `os.pread`/`os.pwrite` invocation counts;
- bounded source migration;
- logical pages written per mutation;
- sparse file length and allocated bytes;
- state-preserving bounded collision rejection.

Not validated here:

- OS/page-cache miss counts or storage-device read counts;
- hardware power-loss/torn-sector guarantees beyond the tested process-crash stack;
- integrated exact overflow / universal admission;
- crash-safe physical tail reclamation;
- filesystem/device write amplification;
- multi-writer or distributed semantics;
- production latency or cost.
