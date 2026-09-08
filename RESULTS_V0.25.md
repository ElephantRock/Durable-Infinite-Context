# v0.25 — Cross-store fixed-page hybrid reclamation

## Observe

v0.24 removed comparison-tree traversal from the experimental primary lookup path by storing the bounded cuckoo primary in fixed 4096-byte pages addressed arithmetically. It deliberately omitted exact exceptional overflow, so guaranteed fallback admission had not been re-earned for the fixed-page representation.

v0.24 also exposed a separate lower-layer obligation: a process crash during migration start could leave an unreachable fixed-page tail even though the committed logical image was exact and application-level recovery required zero redo.

The next test therefore had to combine admission and cleanup rather than treating them as independent properties.

## Diagnose

The combined candidate has two persistent stores with different commit mechanisms:

- a fixed-page primary whose committed epoch is stored in a dual-copy superblock;
- an exact-key SQLite `WITHOUT ROWID` overflow using WAL and `synchronous=FULL`.

That creates a cross-store atomicity problem. There is no shared database transaction spanning the fixed-page file and SQLite. If overflow becomes visible immediately after its SQLite commit, a crash before the fixed-page coordinator advances would expose a logical state that was never committed by the primary epoch.

A second failure mode is subtler. A durable future-epoch overflow row can remain hidden after a crash, but if it is not removed before a later mutation advances the coordinator epoch, that abandoned row can resurrect without being intentionally committed.

Physical cleanup also must not silently become a global logical scan. The committed primary metadata already carries a monotonic `next_page_id`; that metadata should be sufficient to identify an unreachable file tail.

## Derive

### Cross-store visibility protocol

Let the fixed-page superblock expose committed epoch `E`.

For an exceptional overflow admission:

1. insert exact overflow row `(key, E+1)` into SQLite;
2. commit the SQLite transaction;
3. only then write and `fsync` the alternate fixed-page superblock at epoch `E+1`;
4. readers expose an overflow row only when `row.epoch <= committed_primary_epoch`.

Thus a crash after step 2 but before step 3 leaves a durable row that is physically present but logically invisible.

Startup recovery queries an indexed `overflow_epoch` path for rows with `epoch > E` and deletes them before the next normal mutation can advance the epoch. This prevents a hidden abandoned row from becoming visible later merely because time/epoch advanced.

### Fixed-page tail frontier

The committed primary metadata defines the reachable file-length frontier:

\[
ReachableBytes = 4096(2 + 2\,next\_page\_id).
\]

Because logical page ids are allocated monotonically, bytes beyond that frontier after an uncommitted growth attempt cannot be referenced by the committed superblock. Recovery can truncate that suffix without scanning live primary membership.

For the tested doubling geometry with bucket size 4 and initial capacity `C`, migration start allocates a new generation of `2C` slots, requiring `C/2+1` logical pages and two physical copies. Therefore the stale uncommitted file-length range is predicted to be:

\[
StaleTailBytes(C)=2(C/2+1)4096 = 4096(C+2)=\Theta(C).
\]

This is a file-length range, not a claim about filesystem allocated blocks or device bytes written/reclaimed.

## Hypothesis

The committed fixed-page epoch can coordinate direct exact SQLite overflow admission so that real process crash exposes only the exact pre- or post-commit logical image, while future overflow rows and uncommitted fixed-page tail can be reclaimed without contaminating ordinary primary-hit lookup.

## Prediction

The fixed experiment predicts:

- a SQLite overflow row killed before SQLite commit is absent after reopen;
- a row killed after SQLite commit but before coordinator commit is durable but hidden and is deleted by startup recovery;
- a row killed after the committed fixed-page superblock becomes visible;
- an abandoned future row cannot resurrect when a later overflow mutation advances the epoch;
- migration-start crashes before superblock commit expose the exact pre-operation logical state and leave only truncatable stale tail;
- ordinary primary hits never query SQLite overflow;
- source migration remains bounded at eight source slots / rows;
- exceptional lookup retains explicit B-tree depth rather than being mislabeled constant;
- the stale file-length suffix grows with generation capacity even though cleanup may use one `ftruncate` call.

## Test

The real-process crash matrix contains two cross-store scenarios:

1. `overflow_admission` at `overflow_uncommitted`, `overflow_committed`, and `committed`;
2. `migration_start` at `primary_pages_written`, `primary_data_synced`, and `primary_committed`.

That gives **6 real `SIGKILL` cases**. Each crash image is compared against deterministic pre/post logical snapshots before recovery and again after recovery. Two recovery passes test idempotence and zero logical redo.

A separate resurrection control leaves one committed-but-hidden future overflow row, restarts the store, then performs another exceptional admission. The next mutation must delete the abandoned row before advancing the coordinator epoch.

The common-path envelope uses `N={256,1024,4096}`. The exceptional overflow envelope uses `O={1,16,64,256,1024}`. Tail reclamation is swept across initial capacities `C={32,128,512,2048}`.

## Result

The cross-store logical protocol **survived the tested process-crash envelope**.

All **6/6** `SIGKILL` cases matched the exact deterministic pre- or post-commit logical snapshot required by the fixed-page coordinator epoch, both before and after cleanup. All pre-existing keys remained readable, audits remained valid, and repeated recovery required **zero logical redo**.

### Hidden future overflow

At `overflow_committed`, the crash image contained exactly one durable future overflow row while the target remained logically absent. The first recovery pass deleted exactly that one row through the indexed epoch-cleanup path; the second pass deleted zero rows and made no unnecessary cleanup transaction.

The resurrection control also passed: one abandoned future row existed before startup recovery, startup deleted exactly one row, the abandoned key remained absent after a later coordinator epoch advance, and the replacement overflow admission remained visible.

This closes a real correctness hole that epoch gating alone would not close.

### Common path

| Membership rows | Max source slots scanned | Max rows moved | Successful lookup max fixed-file `pread` calls | Overflow checks on successful primary lookup |
|---:|---:|---:|---:|---:|
| 256 | 8 | 7 | 6 | 0 |
| 1,024 | 8 | 8 | 6 | 0 |
| 4,096 | 8 | 8 | 6 | 0 |

Six migrations started and all six completed. Ordinary growth created zero visible overflow rows and retained valid audits.

The important result is path isolation: adding durable exact overflow did not force successful common-path reads through the overflow index.

### Exceptional overflow

| Overflow rows `O` | SQLite B-tree height | Primary miss fixed-file `pread`s | Coordinator extra `pread`s | Primary hit queried overflow? |
|---:|---:|---:|---:|---:|
| 1 | 1 | 8 | 2 | No |
| 16 | 1 | 8 | 2 | No |
| 64 | 1 | 8 | 2 | No |
| 256 | 2 | 8 | 2 | No |
| 1,024 | 2 | 8 | 2 | No |

Each exceptional admission used one SQLite logical commit, one fixed-page coordinator superblock write, and one explicit fixed-file `fsync` in the measured wrapper. SQLite's internal WAL frames and synchronization calls are not counted by those numbers.

The overflow B-tree height grows, as expected. Universal fallback admission therefore does not imply universally constant lookup.

### Tail reclamation

| Initial capacity `C` | Stale tail before recovery | Reclaimed file-length range | `ftruncate` calls | Fixed-file `fsync`s | Tail after recovery |
|---:|---:|---:|---:|---:|---:|
| 32 | 139,264 B | 139,264 B | 1 | 1 | 0 B |
| 128 | 532,480 B | 532,480 B | 1 | 1 | 0 B |
| 512 | 2,105,344 B | 2,105,344 B | 1 | 1 | 0 B |
| 2,048 | 8,396,800 B | 8,396,800 B | 1 | 1 | 0 B |

The measurements exactly follow:

\[
\boxed{StaleTailBytes(C)=4096(C+2)=\Theta(C)}.
\]

Cleanup is metadata-local in the implementation and needs one truncate syscall plus one fixed-file `fsync`, but **constant syscall count is not evidence of constant physical cleanup work**. The experiment measures the file-length suffix removed; it does not measure filesystem allocated blocks, storage-device traffic, or media work.

## Revise

v0.25 re-earns an exact exceptional overflow fallback for the fixed-page experimental primary under a tested single-writer process-crash protocol. The fixed-page committed epoch is sufficient to gate visibility across the two stores, provided startup cleanup removes abandoned future rows before another epoch-advancing mutation.

The cleanup path also avoids a live-membership scan: future-row discovery uses an `overflow_epoch` index, and fixed-tail reachability is derived from committed `next_page_id` metadata.

However, three boundaries remain important:

1. overflow lookup still inherits comparison-B-tree depth;
2. the stale file-length range created by eager generation allocation is `Theta(C)` even though truncation takes one user-space syscall;
3. recovery was tested for idempotent repeated completion, but the recovery procedure itself was **not yet killed at internal cleanup failpoints**.

The third point is now the next correctness obligation. A recovery state machine that cannot itself survive process death is not yet durable recovery.

## Engineer

The surviving tested contract is:

\[
\boxed{
\begin{aligned}
CrashImage &\in \{ExactPreCommit, ExactPostCommit\}\\
FutureOverflowVisibility &\iff row.epoch \le CommittedPrimaryEpoch\\
StartupFutureCleanup &\text{ uses indexed epoch discovery}\\
ApplicationLogicalRedo &= 0\\
MigrationSourceSlots &\le 8\\
SuccessfulCommonOverflowChecks &= 0\\
StaleTailBytes(C) &= 4096(C+2)
\end{aligned}}
\]

for the tested single-writer process-crash model.

The next discriminating experiment should ask:

> **Can recovery itself be interrupted by real process death at every cleanup boundary and still converge to the same committed cross-store state without resurrection, logical redo, or unreclaimed tail?**

A v0.26 recovery-interruption experiment should kill during:

- future-row cleanup before and after SQLite delete commit;
- fixed-tail truncation before and after fixed-file `fsync`;
- the boundary between overflow cleanup and tail cleanup;
- repeated restart/recovery cycles.

Only after cleanup interruption safety survives should the project optimize the `Theta(C)` stale file-length range, for example by testing incremental generation allocation rather than eager full-generation extension.

## Measurement scope

Validated here:

- exact logical state across the fixed-page primary and persistent overflow after real `SIGKILL`;
- epoch-gated cross-store overflow visibility;
- indexed discovery/deletion of abandoned future overflow rows;
- prevention of later-epoch future-row resurrection;
- metadata-derived fixed-tail truncation;
- idempotent completed recovery with zero application logical redo;
- common-path isolation from exceptional overflow;
- bounded source migration;
- exact user-space fixed-file `pread`/superblock write/`fsync` wrapper counts described above;
- SQLite B-tree height via `dbstat`;
- stale file-length byte range.

Not validated here:

- interruption of the recovery procedure itself;
- filesystem allocated-block reclamation or device-level cleanup work;
- SQLite internal WAL-frame or fsync counts;
- arbitrary hardware power-loss/torn-write behavior;
- constant exceptional lookup independent of overflow cardinality;
- multi-writer or distributed semantics;
- production latency, throughput, or cost.
