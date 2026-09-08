# Durable Infinite Context — Minimum Falsifiable Prototype v0.25

This repository is a falsification-first research prototype for **Durable Infinite Context**: durable memory may grow without bound while task context remains bounded and reconstructed on demand.

\[
C_t = F(q_t,M_t), \qquad |C_t| \le B
\]

The project does not attempt to create an actually infinite model context window. It tests whether bounded task context can be reconstructed from growing durable state with correct revision semantics and tractable operational cost.

## Governing discipline

**Observe → Diagnose → Derive → Hypothesize → Predict → Test → Revise → Engineer**

Architecture is treated as a surviving hypothesis, not as the goal. Negative results are retained because they constrain the next design.

## Current surviving production candidate

The durable path currently contains:

- canonical evidence and revisable assertions with lineage;
- correction, supersession, and contested-state reconciliation;
- valid-time and knowledge-time queries;
- bounded context compilation;
- selective materialization and multi-dimensional addressability;
- deterministic non-oracle query planning over oracle assertions;
- scalable indexed candidate generation and incremental index maintenance;
- dependency-aware invalidation/reconstruction/retirement;
- explicit derived lifecycle states (`fresh`, `invalid`, `rebuilding`);
- SQLite WAL persistence with real `SIGKILL` recovery tests;
- durable ordered logical intents and optimistic canonical versions;
- snapshot-consistent derived reads;
- promotion-time topology revalidation;
- explicit missing-output obligations;
- subject-wide profile semantic identity;
- transactional `(subject,predicate)` current heads;
- compositional predicate facets;
- normalized indexed `(subject,predicate)` membership;
- constant-size subject profile descriptors;
- facet-local stale-read protection;
- machine-readable evidence anchors with executable replay verifiers.

The v0.18–v0.25 hash/cuckoo/overflow/fixed-page structures remain **experimental alternatives**, not yet replacements for the production membership B-tree. v0.25 now combines the arithmetic-addressed fixed-page primary with persistent exact overflow under a tested cross-store epoch protocol. It preserves common-path isolation and performs metadata/index-local cleanup after the tested crashes, but recovery interruption, filesystem/device cleanup cost, and production integration remain open.

## Deliberate non-claims

Current evidence does **not** establish:

- production entity linking, semantic embeddings, or extraction accuracy;
- distributed/replicated consistency;
- hardware power-loss guarantees beyond the tested process-crash/storage stack;
- arbitrary physical multi-writer execution;
- production latency or dollar cost;
- cold/archive recovery;
- arbitrary ontology migration;
- constant work for arbitrarily large live subject fan-out or facet values;
- direct OS/device page-read locality independent of global memory;
- constant comparison-tree depth for the current production membership indexes;
- universally constant lookup under arbitrary collisions;
- a production-ready physically direct-addressed hybrid replacement for the membership B-tree;
- equivalence between `os.pread`/`os.pwrite` invocation counts and filesystem/device I/O;
- constant exceptional lookup: exact overflow still inherits B-tree depth;
- crash safety when the v0.25 cleanup procedure itself is interrupted;
- bounded filesystem allocated-block or device-level reclamation from one `ftruncate` call;
- SQLite WAL-frame, filesystem, fsync, or device write-amplification bounds for the complete hybrid;
- a strong agentic-RAG superiority result.

## Milestone ledger

| Version | Falsification target | Main surviving result |
|---|---|---|
| v0.1 | Is persistent state semantically necessary? | No under oracle retrieval; persistent state only earned a possible efficiency role. |
| v0.2 | Does materialized state earn write complexity? | Selective materialization reduces repeated current-state reconstruction when read savings justify maintenance. |
| v0.3 | Is one similarity channel sufficient? | No. Identity/time are independent address dimensions; adaptive coverage prevents premature closure. |
| v0.4 | Does addressability survive removal of the oracle plan? | Controlled-language planner matched resolvable oracle cases and abstained on irreducible ambiguity. |
| v0.5 | Can query resolution avoid O(N) subject scans? | Yes after fragment addressability; indexed resolution stayed accurate through 50k entities. |
| v0.6 | Can address indexes be maintained locally? | Fixed-local mutations stayed roughly constant while rebuild work grew with total memory. |
| v0.7 | Can multi-layer invalidation/rebuild remain local? | Yes for tested DAGs after hidden whole-graph invalid discovery was removed. |
| v0.8 | Can interrupted maintenance recover without stale reads? | Initial phase handling failed; idempotent redo repaired the protocol. |
| v0.9 | Do crash invariants survive real process death? | SQLite WAL + `synchronous=FULL` passed 33 real `SIGKILL` cases. |
| v0.10 | Do multiple durable intents preserve conflict/recovery semantics? | Yes after a snapshot race was found and fixed. |
| v0.11 | Can admission-time impact metadata become stale? | Yes. Promotion-time topology revalidation closes the demonstrated leak. |
| v0.12 | Can canonical growth require absent derived outputs? | Yes. Explicit missing-output obligations restore completeness. |
| v0.13 | Can subject-only profiles remain correct across predicate change? | Subject-wide profile semantics restore exact parity. |
| v0.14 | Can current reconstruction avoid historical-depth scans? | Transactional current heads remove H-dependence while preserving true P-dependence. |
| v0.15 | Can selective maintenance scale with K instead of P? | Yes in logical facet work; exposed an O(P) serialized manifest. |
| v0.16 | Can the serialized manifest be removed? | Yes: normalized membership plus a 40-byte descriptor keeps measured selective returned work K-local, but B-tree depth still grows with N. |
| v0.17 | Can fixed B-tree sharding make lookup depth constant? | No. Fixed sharding changes thresholds, not `Theta(log_B N)`. |
| v0.18 | Does conventional hashing solve lookup locality without new spikes? | No. Expected constant lookup coexists with `Theta(N)` stop-the-world resize spikes. |
| v0.19 | Can incremental migration bound resize work per mutation? | Source migration is bounded at 8 slots/rows, but linear placement retains an unbounded tail. |
| v0.20 | Can placement itself have a finite work cap? | Yes with bounded bucketized cuckoo placement, but concentrated capacity is finite: 16 keys. |
| v0.21 | Can finite bounded domains provide an escape path? | Yes: capacity `16D`, mutation cap `200D`, miss pages `3D`; fixed D still has finite admission and proportional space. |
| v0.22 | Can a bounded common path coexist with explicit rare overflow? | Yes in the tested model: ordinary primary hits remain isolated; overflow guarantees admission but honestly inherits logarithmic B-tree depth. |
| v0.23 | Can bounded migration + overflow survive real process death atomically? | Yes in the tested single-writer WAL model: 15/15 crash cases were exact pre/post transaction images with zero application redo; physical primary locality remained unproven. |
| v0.24 | Can the persistent primary remove comparison-tree traversal without losing crash atomicity? | Yes in the tested fixed-page model: 9/9 crash cases were exact pre/post images, arithmetic lookup remained index-free through 16,384 rows, and single-generation misses used 8 user-space `pread` calls; exact overflow and physical reclamation remained open. |
| v0.25 | Can fixed-page primary + exact overflow share a crash-atomic visibility protocol with safe cleanup? | Yes in the tested process-crash envelope: 6/6 cross-store crash cases were exact pre/post images, startup cleanup prevented future-row resurrection, and ordinary hits stayed isolated. The stale uncommitted file-length suffix is `4096(C+2)=Theta(C)`, and cleanup interruption is still untested. |

Detailed narratives and machine-readable evidence live in `RESULTS_V0.*.md`, `*_results.json`, and milestone evidence anchors.

## Selected validated measurements

### v0.16 normalized membership

At fixed `K=1,H=8,N=128`, the subject descriptor stays **40 bytes** and selective SQL payload stays **270 bytes** across `P=1..64`; full profile payload grows with the real output. Predicate topology deltas avoid rewriting a P-sized manifest.

The remaining caveat is physical lookup depth: the membership B-tree height grows with global N, so one SQLite VM `Seek` is not proof of constant physical locality.

### v0.17 comparison-tree page locality

With 4096-byte pages:

| Membership rows `N` | Global height | 64-way max shard height |
|---:|---:|---:|
| 1,000 | 2 | 1 |
| 10,000 | 2 | 2 |
| 50,000 | 3 | 2 |
| 250,000 | 3 | 2 |
| 1,000,000 | 3 | 3 |

For fixed finite shard count `S`:

\[
\boxed{AddressLookupPages=\Theta(\log_B(N/S))=\Theta(\log_B N)}
\]

### v0.18–v0.19 resize locality

v0.18 stop-the-world resize produces largest single migrations of:

`512, 2,048, 8,192, 32,768, 131,072` rows

at `N={1k,4k,16k,64k,256k}`.

v0.19 limits source migration to **8 source slots / 8 copied rows per insertion**, but total mutation slot-work maxima still grow `26,26,35,39,50` because destination placement uses linear probing.

### v0.20 bounded placement

The fixed two-choice bucketized cuckoo candidate uses 4 slots per bucket, 32 relocation attempts, and an 8-entry stash. Ordinary growth through 256k entries has zero failures and observed mutation maxima `17,17,22,27,30`. The modeled insertion cap is **200 slot operations**.

Concentrated collision stress admits 16 keys and rejects the 17th at the explicit bound:

\[
\boxed{BoundedPlacementWork \neq GuaranteedInsertionAvailability}
\]

### v0.21 bounded finite escalation

For `D={1,2,4,8}` concentrated domains:

| D | Capacity | First failure | Mutation cap | Missing lookup pages | Reserved space |
|---:|---:|---:|---:|---:|---:|
| 1 | 16 | 17 | 200 | 3 | 1x |
| 2 | 32 | 33 | 400 | 6 | 2x |
| 4 | 64 | 65 | 800 | 12 | 4x |
| 8 | 128 | 129 | 1,600 | 24 | 8x |

Finite escalation is coherent, but unlimited escalation would simply move non-locality into mutation work, lookup fan-out, and reserved capacity.

### v0.22 explicit rare overflow

v0.22 keeps **one bounded v0.20 primary domain** and sends only exhausted placements to an exact-key SQLite `WITHOUT ROWID` B-tree overflow.

Ordinary `N={1k,4k,16k,64k,256k}` remains entirely on the primary path. After deliberately saturating the primary's concentrated capacity, exceptional lookup follows:

\[
\boxed{ExceptionalLookupPages(O)=3+Height_{BTree}(O)}
\]

The defensible result is **common/exceptional-path separation**, not universal constant lookup. See `RESULTS_V0.22.md`, `rare_overflow_results.json`, and `verify_rare_overflow_results.py`.

### v0.23 durable hybrid admission

The persistent candidate commits one logical admission, one bounded source-migration step, overflow routing, and generation metadata in a single SQLite WAL transaction with `synchronous=FULL`.

The fixed real-process crash matrix covers five scenarios × three failpoints = **15 `SIGKILL` cases**. All 15 produced exactly the deterministic pre-transaction or post-transaction logical snapshot required by the commit boundary. Every case preserved membership audits and pre-existing keys, and two consecutive recovery passes required **zero application redo or repair**.

Ordinary persistent growth:

| Membership rows | Interval max primary work | Max source slots | Max rows moved | Overflow rows | Successful overflow checks | Metadata rows |
|---:|---:|---:|---:|---:|---:|---:|
| 256 | 27 | 8 | 8 | 0 | 0 | 1 |
| 1,024 | 30 | 8 | 8 | 0 | 0 | 1 |
| 4,096 | 34 | 8 | 8 | 0 | 0 | 1 |

Six migrations started and all six completed. The conservative derived primary-work bound is **1808 modeled operations**; the observed ordinary maximum was **34**.

Persistent exceptional overflow still exposes B-tree geometry:

| Overflow rows `O` | B-tree height | Overflow-hit modeled pages | Missing-key modeled pages |
|---:|---:|---:|---:|
| 1 | 1 | 4 | 4 |
| 16 | 1 | 4 | 4 |
| 64 | 1 | 4 | 4 |
| 256 | 2 | 5 | 5 |
| 1,024 | 2 | 5 | 5 |

The durable claim is transactional and logical. Primary bucket/stash page probes are model pages; the backing SQLite primary tables are themselves comparison B-trees. SQL row-write counts are not WAL frames, filesystem writes, fsyncs, or device writes. See `RESULTS_V0.23.md`, `durable_hybrid_evidence.json`, and `verify_durable_hybrid_results.py`.

### v0.24 fixed-page primary locality

v0.24 moves the experimental primary below SQLite comparison indexes. Every logical primary page has two CRC-protected 4096-byte physical copies, and two fixed superblocks carry the committed epoch. The primary address is computed directly:

\[
\boxed{ByteOffset=4096(2+2p+c)}
\]

for logical page `p` and copy `c in {0,1}`. `primary_index_structure` is explicitly `none`.

All **9/9 real `SIGKILL` cases** across ordinary insertion, migration start, and migration progress matched the exact deterministic pre/post logical image required by the committed superblock epoch. Recovery required zero logical redo.

Ordinary growth:

| Membership rows | Max source slots | Max rows moved | Max logical pages written | Max placement work | Successful lookup max `pread` calls | Missing lookup `pread` calls |
|---:|---:|---:|---:|---:|---:|---:|
| 256 | 8 | 7 | 7 | 19 | 6 | 8 |
| 1,024 | 8 | 8 | 9 | 26 | 6 | 8 |
| 4,096 | 8 | 8 | 9 | 39 | 6 | 8 |
| 16,384 | 8 | 8 | 12 | 39 | 6 | 8 |

Eight migrations started and all eight completed. During active two-generation migration, successful lookup used at most **12** user-space `os.pread` calls and a missing lookup used **14**.

The crash matrix also exposed an important non-equivalence: uncommitted `migration_start` crashes retained **139,264 bytes of unreachable file tail** even though logical recovery was exact. Therefore zero logical redo does not imply zero cleanup. `os.pread` invocation counts likewise do not prove storage-device I/O locality. See `RESULTS_V0.24.md`, `fixed_page_primary_evidence.json`, and `verify_fixed_page_primary_results.py`.

### v0.25 cross-store fixed-page hybrid

v0.25 restores persistent exact exceptional overflow to the fixed-page primary without placing overflow on the successful common path. The fixed-page superblock epoch is the visibility coordinator. An exceptional row is committed to SQLite at future epoch `E+1`; it becomes logically visible only after the fixed-page coordinator commits `E+1`.

All **6/6 real cross-store `SIGKILL` cases** matched the required exact pre/post logical image before and after cleanup. A committed-but-hidden future row remained absent, was deleted through the `overflow_epoch` index, and could not resurrect when a later admission advanced the epoch.

Common-path growth:

| Membership rows | Max source slots | Max rows moved | Successful lookup max fixed-file `pread`s | Successful overflow checks |
|---:|---:|---:|---:|---:|
| 256 | 8 | 7 | 6 | 0 |
| 1,024 | 8 | 8 | 6 | 0 |
| 4,096 | 8 | 8 | 6 | 0 |

Exceptional overflow remains explicit:

| Overflow rows | B-tree height | Primary miss fixed-file `pread`s | Extra coordinator `pread`s |
|---:|---:|---:|---:|
| 1 | 1 | 8 | 2 |
| 16 | 1 | 8 | 2 |
| 64 | 1 | 8 | 2 |
| 256 | 2 | 8 | 2 |
| 1,024 | 2 | 8 | 2 |

The stale migration-start suffix obeyed:

\[
\boxed{StaleTailBytes(C)=4096(C+2)=\Theta(C)}
\]

with measured ranges `139,264`, `532,480`, `2,105,344`, and `8,396,800` bytes for initial capacities `C={32,128,512,2048}`. Each completed cleanup used one `ftruncate` and one fixed-file `fsync`, but that constant syscall count is **not** a device-work claim. Filesystem allocated-block reclamation was not measured. See `RESULTS_V0.25.md` and the v0.25 evidence/verifier once hardened.

## Reproducing the hardened path

```bash
python -m unittest discover -s tests -v
python run_planner_experiment.py
python run_scalable_planner_experiment.py
python run_maintenance_experiment.py
python run_compositional_profile_experiment.py
python run_normalized_membership_experiment.py
python run_page_locality_experiment.py
python run_hash_resize_experiment.py
python run_incremental_hash_experiment.py
python run_bounded_placement_experiment.py
python run_bounded_escape_experiment.py
python run_rare_overflow_experiment.py
python run_durable_hybrid_experiment.py
python run_fixed_page_primary_experiment.py
python run_cross_store_hybrid_experiment.py
python verify_scanfree_cascade_results.py
python verify_recovery_results.py
python verify_process_recovery_results.py
python verify_multi_intent_results.py
python verify_topology_results.py
python verify_growth_results.py
python verify_predicate_schema_results.py
python verify_subject_fanout_results.py
python verify_compositional_profile_results.py
python verify_normalized_membership_results.py
python verify_page_locality_results.py
python verify_hash_resize_results.py
python verify_incremental_hash_results.py
python verify_bounded_placement_results.py
python verify_bounded_escape_results.py
python verify_rare_overflow_results.py
python verify_durable_hybrid_results.py
python verify_fixed_page_primary_results.py
python verify_cross_store_hybrid_results.py
```

CI runs this chain on pull requests and uploads the hardened evidence ledgers as artifacts.

## Current architectural hypothesis

```text
Canonical evidence
  -> revisable assertion history
  -> transactional current heads
  -> normalized current predicate membership
  -> predicate-specific evidence-bearing facets
  -> constant-size subject descriptor / compositional profile
  -> selectively materialized state / derived views

Question
  -> ambiguity-aware resolution
  -> indexed candidate generation
  -> justified constraints + coverage control
  -> requested semantic subset
  -> indexed membership validation
  -> one-snapshot facet assembly
  -> bounded context compilation

Canonical mutation
  -> durable ordered intent
  -> conflict validation
  -> promotion-time topology revalidation
  -> local invalidation / missing-output derivation
  -> local reconstruction / current-head update
  -> affected membership/facet repair
  -> selective retirement
  -> crash-safe completion

Experimental membership alternative
  -> fixed-page arithmetic-addressed bounded primary
  -> bounded incremental migration
  -> dual-page copies + dual committed superblocks
  -> persistent exact-key B-tree overflow
  -> fixed-page epoch as cross-store visibility coordinator
  -> indexed abandoned-future-row cleanup
  -> metadata-derived stale-tail truncation
```

Fifteen distinctions are now central:

> Memory is durable state. Context is a bounded compiled artifact reconstructed for a task.

> Correct derived state requires freshness, completeness, and semantic-identity consistency.

> Locality must be judged against the true semantic footprint, not lifetime database size.

> Logical row counts can hide serialized fan-out.

> One indexed VM `Seek` can hide growing B-tree page depth.

> Fixed comparison-index sharding changes constants, not asymptotic depth.

> Expected constant lookup does not imply bounded mutation if resize is global.

> Bounded migration scheduling does not bound an unbounded placement primitive.

> Bounded placement work does not imply guaranteed admission.

> Finite bounded escalation raises admission only by proportional work/lookup/space.

> Guaranteed admission can coexist with a bounded common path only by making exceptional cost explicit; exceptional lookup is not thereby constant.

> Transactional crash atomicity does not imply physical direct-address locality; logical bucket pages and SQL row writes must not be confused with storage-engine or device I/O.

> Arithmetic page addressing and bounded `os.pread` call counts remove comparison-index traversal from the tested implementation, but they do not prove bounded device I/O.

> Zero logical recovery work does not imply zero cleanup: an uncommitted generation can leave an unreachable suffix after crash.

> Cross-store epoch gating is insufficient by itself: a durable hidden future row must be removed before a later coordinator advance can make it accidentally visible.

## Next falsification target — interrupted recovery

v0.25 establishes completed, idempotent cleanup after the tested crashes, but the cleanup procedure itself has not yet been subjected to process death at its internal boundaries.

The next question is:

\[
\boxed{
Can recovery itself be killed at every cross-store cleanup boundary and still converge to the same committed state,
without future-row resurrection, logical redo, or unreclaimed tail?
}
\]

A v0.26 experiment should add real `SIGKILL` failpoints around future-row deletion and fixed-tail truncation, including before/after SQLite cleanup commit, between SQLite cleanup and tail cleanup, and before/after the fixed-file cleanup `fsync`. Every interrupted recovery must reopen to the same committed logical image and converge under repeated restart.

Only after cleanup interruption safety survives should the project optimize the `Theta(C)` stale file-length range, for example by testing incremental generation allocation rather than eager full-generation extension.
