# Durable Infinite Context — Minimum Falsifiable Prototype v0.36

This repository is a falsification-first research prototype for **Durable Infinite Context**: durable memory may grow without bound while task context remains bounded and reconstructed on demand.

\[
C_t = F(q_t,M_t), \qquad |C_t| \le B
\]

The project does not attempt to create an actually infinite model context window. It tests whether bounded task context can be reconstructed from growing durable state with correct revision semantics and tractable operational cost.

## Governing discipline

**Observe → Diagnose → Derive → Hypothesize → Predict → Test → Revise → Engineer**

Architecture is treated as a surviving hypothesis, not as the goal. Negative results are retained because they constrain the next design.

## Current surviving production candidate

The production candidate still uses the normalized SQLite membership B-tree earned through v0.16. It provides revisable evidence/assertion semantics, valid- and knowledge-time queries, bounded context compilation, indexed candidate generation, dependency-aware invalidation/rebuild, transactional current heads, compositional facets, snapshot-consistent reads, and machine-readable replay evidence.

The v0.18–v0.36 hash/cuckoo/overflow/fixed-page structures remain **experimental alternatives**, not replacements for that production B-tree. They have progressively earned bounded migration scheduling, bounded modeled placement work, explicit rare overflow, crash-atomic hybrid admission, arithmetic-addressed fixed pages, cross-store visibility gating, interrupted-recovery convergence, an explicit volatile/durable persistence-ordering model, a modeled bounded-address segment map, an integrated append-local segmented primary, fixed-width radix nodes, fixed-budget mapping-lifecycle reclamation with explicit stale-payload isolation on reuse, segment-aligned real generation ownership, bounded FIFO publication of multiple retired-generation backlogs, tagged retirement-descriptor recycling, and a negative placement constraint on physical descriptor release.

v0.28 adds a negative result: simply removing eager full-generation `ftruncate` does **not** bound stale file-length residue while the same capacity-scaled arithmetic page addresses remain. One logical page can be materialized at a sparse offset `Theta(C)` beyond the committed frontier. v0.29 then survives a narrower modeled falsification with append-local fixed-size segments and an eight-level dual-copy radix map. v0.30 integrates that mapper into the real experimental fixed-page primary: across forced migration at `C={32,2048,131072,4194304}`, the largest observed physical append is 147,456 bytes, target lookup remains 20 user-space `pread`s, and all 16 real `SIGKILL` cases expose exact pre/post committed state with scan-free, redo-free frontier recovery. v0.31 then falsifies the mapper's variable-width JSON node representation: effective one-page fanout falls to 154 near the uint64 limit. A fixed 256-bit occupancy bitmap plus 256 uint64 pointer slots uses 2,103 bytes, round-trips all 256 edges at every tested pointer magnitude, and carries a real 255→256 root transition through 5/5 exact `SIGKILL` cases with zero node splits. v0.32 next falsifies mapping-only free-extent reuse: a CRC-valid retired payload becomes visible under a new logical mapping. Intrusive dual-copy lifecycle headers plus a committed retirement cursor bound each cleanup step to `B=3` ownership visits in the fixed experiment, while a 32-page fixed-footprint scrub prevents the demonstrated payload resurrection before reuse publication. Reclaim is 4/4 exact under `SIGKILL`; reuse is 5/5 exact, with scan-free, redo-free recovery. v0.33 then finds that the real primary cannot safely adopt whole-segment ownership while generations are allocated back-to-back: the next generation starts inside the predecessor's final 16-page mapping segment. Aligning every new generation base to the next segment boundary costs at most 15 unused logical page ids, allocates no capacity-scaled physical padding, and lets real migration completion publish v0.32 ownership directly. Two real growth cycles preserve all keys, safely reuse a reclaimed extent after the fixed 32-page scrub, and pass 12/12 process-crash cases with scan-free recovery. v0.34 removes the resulting one-backlog admission restriction with fixed dual-copy retirement descriptors rooted by superblock head/tail/count scalars. Three real retired generations accumulate without cleanup; enqueue stays at one two-page descriptor plus at most one tail rewrite, fixed-budget reclaim touches only the FIFO head, and 19/19 queue publication/reclaim crash cases expose exact committed state with retirement-queue-scan-free recovery. v0.35 then recycles dequeued descriptor page pairs through a tagged free list: queue/free references are `(base_page, incarnation)`, newest committed copy selection precedes tag validation, the three-descriptor real pool serves later generations with zero further descriptor-page append, stale incarnation 1 is rejected after the same page becomes incarnation 2, and 29/29 fresh/reuse/reclaim process-crash cases are exact with descriptor-history-scan-free recovery. v0.36 then falsifies the narrow next step: under the current interleaved append-local placement, every observed free descriptor pair remains below committed suffix pages after drain, so head-only file-tail truncation releases zero pages without history walks. The full free-chain traversal is diagnostic only; the negative result points next toward placement segregation, relocation, or a broader allocator rather than more tail metadata.

## Deliberate non-claims

Current evidence does **not** establish:

- production entity linking, embedding quality, or extraction accuracy;
- distributed/replicated consistency or arbitrary multi-writer correctness;
- hardware power-loss guarantees from the abstract persistence model;
- torn-sector, drive-cache, or filesystem-journal behavior;
- production latency, throughput, or dollar cost;
- constant work for arbitrarily large live subject fan-out or facet values;
- constant comparison-tree depth for the current production membership B-tree;
- universal constant lookup under arbitrary collisions;
- equivalence between user-space `pread`/`pwrite` calls and device I/O;
- constant exceptional lookup: exact overflow still inherits comparison-tree depth;
- bounded filesystem allocated-block reclamation from file-length truncation;
- bounded stale-generation residue under the current direct-address layout;
- mathematically unbounded logical identifiers from the fixed eight-level/64-bit radix namespace;
- hardware power-loss or torn-write safety from the v0.30–v0.35 process-crash results;
- mathematically unbounded physical identifiers beyond the fixed uint64 pointer model;
- mathematically unbounded retirement-descriptor identities beyond the finite uint64 incarnation namespace;
- constant total retired-generation cleanup work; draining `K` retired materialized segments still takes `Theta(K)` ownership visits;
- filesystem block deallocation, hole punching, or reclamation of now-empty radix metadata nodes;
- safe whole-segment reclamation for deliberately unaligned logical generation boundaries;
- constant retirement-descriptor storage independent of peak live backlog; v0.35 stops history-only growth after reusable pool capacity exists, but storage tracks the peak allocated descriptor pool;
- a real-primary retirement-descriptor pool peak beyond three; the larger v0.35 `H={1,4,16,64,256}` cases are deterministic storage-history controls;
- physical release of buried free descriptor pairs by head-only tail truncation under the tested interleaved layout; v0.36 observes positive committed suffixes above every free pair;
- impossibility of all bounded descriptor-pool reduction mechanisms; v0.36 does not test segregation, relocation, or a generalized allocator;
- foreground locality from the v0.36 full-free-chain diagnostic traversal; that traversal is measurement only;
- a production-ready extent-map, segmented-address, or reclamation replacement;
- a strong agentic-RAG superiority result.

## Milestone ledger

| Version | Falsification target | Main surviving result |
|---|---|---|
| v0.1 | Is persistent state semantically necessary? | No under oracle retrieval; persistence only earned a possible efficiency role. |
| v0.2 | Does materialized state earn write complexity? | Selective materialization can reduce repeated reconstruction when read savings justify maintenance. |
| v0.3 | Is one similarity channel sufficient? | No; identity and time are independent address dimensions. |
| v0.4 | Does addressability survive removal of the oracle plan? | Controlled-language planning matched resolvable oracle cases and abstained on irreducible ambiguity. |
| v0.5 | Can query resolution avoid O(N) subject scans? | Indexed fragment addressability stayed accurate through 50k entities. |
| v0.6 | Can indexes be maintained locally? | Fixed-local mutations stayed roughly constant while full rebuild grew with memory. |
| v0.7 | Can multi-layer invalidation/rebuild remain local? | Yes for tested DAGs after hidden whole-graph discovery was removed. |
| v0.8 | Can interrupted maintenance recover without stale reads? | Initial phase handling failed; idempotent redo repaired it. |
| v0.9 | Do crash invariants survive real process death? | SQLite WAL + `synchronous=FULL` passed 33 real `SIGKILL` cases. |
| v0.10 | Do multiple durable intents preserve conflict/recovery semantics? | Yes after a snapshot race was found and fixed. |
| v0.11 | Can admission-time impact metadata become stale? | Yes; promotion-time topology revalidation closes the demonstrated leak. |
| v0.12 | Can canonical growth require absent derived outputs? | Yes; explicit missing-output obligations restore completeness. |
| v0.13 | Can subject-only profiles remain correct across predicate change? | Subject-wide profile semantics restore exact parity. |
| v0.14 | Can current reconstruction avoid historical-depth scans? | Transactional current heads remove H-dependence while preserving real P-dependence. |
| v0.15 | Can selective maintenance scale with K instead of P? | Yes in logical facet work; exposed an O(P) serialized manifest. |
| v0.16 | Can the serialized manifest be removed? | Normalized membership keeps measured selective returned work K-local; B-tree depth still grows with N. |
| v0.17 | Can fixed B-tree sharding make lookup depth constant? | No; fixed sharding changes thresholds, not `Theta(log_B N)`. |
| v0.18 | Does conventional hashing solve locality without new spikes? | No; expected constant lookup coexists with `Theta(N)` stop-the-world resize spikes. |
| v0.19 | Can incremental migration bound resize work per mutation? | Source migration is bounded at 8 slots/rows, but linear placement retains an unbounded tail. |
| v0.20 | Can placement itself have a finite work cap? | Yes with bounded cuckoo placement, but concentrated capacity is finite at 16 keys. |
| v0.21 | Can finite bounded domains provide an escape path? | Capacity `16D`, mutation cap `200D`, miss pages `3D`; fixed D still has finite admission. |
| v0.22 | Can a bounded common path coexist with explicit rare overflow? | Yes; ordinary primary hits remain isolated while overflow guarantees admission with logarithmic exceptional cost. |
| v0.23 | Can bounded migration + overflow survive real process death atomically? | 15/15 crash cases were exact pre/post images with zero application redo. |
| v0.24 | Can persistent primary lookup remove comparison-tree traversal? | 9/9 crash cases passed; arithmetic addressing stayed index-free, but stale physical tail appeared. |
| v0.25 | Can fixed-page primary + exact overflow share one visibility protocol? | 6/6 cross-store crash cases passed; startup cleanup prevented future-row resurrection. |
| v0.26 | Does cleanup itself restart-converge when killed? | 8/8 interrupted-recovery cases preserved state and converged with zero logical redo. |
| v0.27 | Does cleanup survive explicit volatile/durable ordering? | Yes for derivation-based cleanup; a premature durable cleanup marker is falsified by a stranded-tail counterexample. |
| v0.28 | Does naive lazy allocation bound stale-generation residue? | No; one lazy page write can still create `Theta(C)` file-length residue because the direct address itself scales with capacity. |
| v0.29 | Can segment mapping remove capacity-scaled sparse address span without moving non-locality into the mapper? | Yes in the fixed model: one fresh high-id mapping stays at 188,416 bytes, 20 modeled preads, 8 metadata pwrites + 1 superblock pwrite, and 2 fsyncs; dense metadata still grows with `K`. |
| v0.30 | Does the segmented mapper survive integration with real fixed-page writes and process death? | Yes for the tested single-writer matrix: max append 147,456 B, target lookup 20 preads, 16/16 exact crash images, and frontier recovery with zero generation/radix scan or logical redo. |
| v0.31 | Does dense radix-node growth force representation-driven overflow or recursive splits? | The JSON control loses one-page fanout as pointers widen; a fixed 2,103-byte binary node preserves all 256 edges, needs 0 splits at 255→256, and passes 5/5 exact crash cases with scan-free recovery. |
| v0.32 | Can retired mappings be reclaimed incrementally and their extents safely reused? | Intrusive lifecycle state caps tested cleanup at `B=3` segments/step with zero append; mapping-only reuse leaks stale payloads, while a fixed 32-page scrub repairs the demonstrated leak and passes 4/4 reclaim + 5/5 reuse crash cases. |
| v0.33 | Can real primary-generation retirement use whole-segment ownership safely? | Back-to-back generations share a 16-page mapping segment; segment-aligning new bases costs at most 15 logical ids, removes the overlap, preserves two real growth cycles and reuse, and passes 12/12 exact process-crash cases. |
| v0.34 | Can multiple completed generations queue for reclamation without blocking later migration? | A dual-copy FIFO descriptor queue accumulates three real retired generations without cleanup; enqueue uses one two-page descriptor plus at most one tail rewrite, reclaim stays head-local under `B=2`, and 19/19 queue crash cases are exact with queue-scan-free recovery. |
| v0.35 | Can dequeued retirement descriptors be recycled without ABA aliasing or history scans? | Tagged `(page,incarnation)` recycling reuses a three-descriptor real pool with zero later descriptor-page append, rejects the demonstrated stale identity, and passes 29/29 exact fresh/reuse/reclaim crash cases with descriptor-scan-free recovery. |
| v0.36 | Can retained free descriptor pairs be returned by bounded file-tail truncation after backlog drain? | No under the current interleaved append-local layout: every observed free pair remains below committed suffix pages, so the head-only candidate releases 0 pages with 0 history walks. |

Detailed evidence lives in `RESULTS_V0.*.md`, `*_results.json`, frozen result hashes/artifacts where applicable, `*_evidence.json`, and executable `verify_*_results.py` gates.

## Current storage-layer evidence

### v0.24 — fixed-page primary

The experimental primary uses two CRC-protected physical copies per logical page and two fixed superblocks. Its address is:

\[
\boxed{ByteOffset=4096(2+2p+c)}
\]

All 9/9 real `SIGKILL` cases matched the required pre/post committed image. Single-generation missing lookup used 8 user-space `pread` calls; active two-generation missing lookup used 14. These are invocation counts, not device-I/O bounds.

### v0.25–v0.27 — cleanup and persistence ordering

Cross-store overflow rows are visibility-gated by the fixed-page committed epoch. Recovery deletes hidden future overflow rows and truncates file length back to the committed `next_page_id` frontier.

The eager migration-start stale range is:

\[
\boxed{EagerResidue(C)=4096(C+2)=\Theta(C)}
\]

v0.26 shows completed cleanup restart-converges after real process `SIGKILL`. v0.27 then separates volatile and durable file-size state:

\[
\boxed{ftruncate:V\leftarrow new,\quad fsync:D\leftarrow V,\quad PowerLoss:V\leftarrow D}
\]

All 9/9 derivation-based power-loss-model cases converge. A cleanup marker committed after `ftruncate` but before file `fsync` is unsafe: the marker can survive while the truncation is lost.

### v0.28 — naive lazy allocation

v0.28 retains the exact v0.24 direct-address geometry but removes the assumption of eager full-generation extension. For old capacity `C` and four slots per bucket, the doubled generation has `C/2` bucket pages. First writing copy 0 of bucket `b` creates:

\[
LazyResidue(b)=(2b+1)\times4096
\]

bytes beyond the committed frontier even though exactly one logical page is materialized.

| Old `C` | Eager bytes | Lazy last-bucket bytes | Lazy stash bytes | Sample p50 | Sample p95 | Sample max |
|---:|---:|---:|---:|---:|---:|---:|
| 32 | 139,264 | 126,976 | 135,168 | 61,440 | 126,976 | 126,976 |
| 128 | 532,480 | 520,192 | 528,384 | 258,048 | 495,616 | 520,192 |
| 512 | 2,105,344 | 2,093,056 | 2,101,248 | 1,069,056 | 1,994,752 | 2,093,056 |
| 2,048 | 8,396,800 | 8,384,512 | 8,392,704 | 4,206,592 | 7,958,528 | 8,384,512 |

Therefore:

\[
\boxed{OneLazyPageWritten\not\Rightarrow BoundedFileLengthResidue}
\]

and, more generally:

\[
\boxed{LazyPhysicalMaterialization\neq BoundedAddressSpan}
\]

This is a file-length result only. It is not a filesystem allocated-block or device-write measurement.

### v0.29 — segmented extent mapping

v0.29 changes placement rather than merely delaying the same high-offset write. Sixteen logical bucket pages share one physical segment; segment data and radix metadata are append-allocated near the committed frontier. The fixed candidate uses an eight-level, fanout-256 radix map over a 64-bit logical-segment namespace with dual physical node copies.

A fresh sparse high-id mapping reserves:

\[
\boxed{2\times16 + 2\times(8-1)=46\text{ pages}=188{,}416\text{ bytes}}
\]

and modeled lookup uses:

\[
\boxed{2 + 2\times8 + 2 = 20\text{ user-space preads}}
\]

across all tested capacities `C={1024,16384,262144,4194304}`. Fresh-path metadata remains eight radix `pwrite`s plus one superblock `pwrite`, with two `fsync` barriers. The packed flat-descriptor control grows from 4,096 to 2,097,152 bytes of sparse descriptor span, while dense dual-copy radix descriptor storage grows from 16 to 1,040 pages. Thus v0.29 bounds the *sparse high-id path* but does not claim constant total metadata.

In the abstract persistence-ordering model, dependency-before-commit had 0 invalid states across 12 enumerated cases. The one-barrier control had 7 invalid states across 17 cases, including a durable superblock with absent data and mapping dependencies.

This remains a deterministic arithmetic and abstract persistence-ordering result. v0.30 supplies the first real fixed-page integration.

### v0.30 — integrated segmented primary

v0.30 replaces the experimental fixed-page primary's capacity-scaled physical page placement with append-local 16-logical-page segments published through the eight-level dual-copy radix map. The dual-copy superblock carries the committed physical append frontier. The writer uses a transaction-local radix view for staged nodes while restart readers retain committed-epoch filtering.

Forced `C -> 2C` migration produced:

| Old `C` | New logical base | Target segment id | Fresh segments | Physical append | Target lookup preads | Active-migration miss preads |
|---:|---:|---:|---:|---:|---:|---:|
| 32 | 9 | 1 | 1 | 131,072 B | 20 | 46 |
| 2,048 | 513 | 80 | 1 | 131,072 B | 20 | 98 |
| 131,072 | 32,769 | 4,618 | 1 | 139,264 B | 20 | 88 |
| 4,194,304 | 1,048,577 | 98,773 | 1 | 147,456 B | 20 | 84 |

The logical identifiers grow with capacity, but the physical append remains operation-local. The maximum observed append is 147,456 bytes, below the 188,416-byte one-fresh-segment envelope. Target lookup remains 20 user-space `pread` calls; the largest active-migration miss is 98, below the fixed two-generation envelope of 110.

The real crash matrix injects `SIGKILL` after allocation, dependency writes, dependency `fsync`, and committed-superblock publication at all four capacities. All **16/16** cases match the exact expected committed state. Pre-commit tails equal the deterministic control append; committed kills have zero tail. Recovery uses two superblock reads, performs zero generation-page scan, zero mapping-node scan, and zero logical redo, truncates the uncommitted suffix to zero, and is physically idempotent on the second pass.

This remains a single-writer process-crash result. It does not establish hardware power-loss/torn-write behavior, device-I/O counts, production performance, mathematically unbounded identifiers, or dense radix-node split/overflow behavior.

### v0.31 — fixed-width radix nodes

v0.31 isolates a representation defect in the v0.30 mapper. The logical radix fanout is exactly 256, but JSON entry widths grow with decimal pointer magnitude. With the fixed 4,072-byte payload envelope, the JSON control's effective one-page fanout falls from 256 at small pointers to **154** near `2^64`.

The replacement encodes each byte-radix node as a 256-bit occupancy bitmap plus 256 fixed uint64 pointer slots. The complete CRC-protected record uses **2,103 bytes** and leaves **1,993 bytes** of padding in one 4,096-byte page. Full 256-entry nodes round-trip exactly at every tested pointer magnitude.

A real dense-root experiment materializes 255 distinct top-level edges and then commits the 256th. The transition requires **0 node splits**, **0 recursive split depth**, **8 radix-node `pwrite`s**, **46 appended pages / 188,416 bytes**, and **2 `fsync` barriers**. The real process-crash matrix is **5/5 exact** across kills after allocation, child writes, parent rewrite, dependency `fsync`, and committed-superblock publication. Recovery remains frontier-derived with zero generation-page scan, zero mapping-node scan, zero logical redo, and zero residual tail after recovery.

The surviving result removes a representation-driven split mechanism; it does not make the fixed-width address space mathematically unbounded or reclaim retired-generation storage.

### v0.32 — fixed-budget mapping lifecycle reclamation

v0.32 makes physical-segment ownership explicit with dual-copy lifecycle headers. The headers form an intrusive ownership chain and free list; the committed superblock stores only scalar owner/retirement/free-list heads and counts. Retirement therefore does not serialize an `O(K)` manifest, and `reclaim_step(B)` follows at most `B` ownership links instead of walking logical generation capacity.

With `B=3` and retirement backlogs `K={1,8,32,64}`, the largest observed cleanup step remained **3 reclaimed segments, 12 lifecycle `pread`s, 3 lifecycle `pwrite`s, 3 radix leaf `pwrite`s, and 0 appended pages**. Total drain work still grows with `K`; the result is a per-step foreground bound, not constant total reclamation.

The experiment also found a stronger reuse failure. Mapping-only reuse of a reclaimed extent exposed a seeded valid retired record through the new logical mapping. The repaired candidate invalidates all **32 data pages** in the fixed 16-logical-page/dual-copy segment before publishing the new mapping and OWNED header. In the shared-radix-path fixture, the same physical extent is reused with **0 appended pages, 1 radix-node write, 32 scrub writes, and 2 `fsync`s**, and the old payload is not visible.

The real reclaim crash matrix is **4/4 exact** and the reuse matrix is **5/5 exact**. Recovery remains generation-scan-free, radix-scan-free, redo-free, and physically idempotent on a second pass. This is still a scoped mapping-lifecycle result: current primary generations can begin at logical pages such as 9 or 513, so a 16-page mapping segment may straddle a primary-generation boundary. v0.32 does not claim safe automatic reclamation of such shared segments.

### v0.33 — segment-aligned real generation ownership

v0.33 integrates v0.32 with the real primary-generation lifecycle. The unaligned control confirms that back-to-back generation allocation shares a mapping segment at every tested capacity `C={32,128,2048,131072,4194304}`. The candidate changes only the logical base formula for a new generation:

\[
\boxed{AlignedBase(p)=16\left\lceil p/16\right\rceil}
\]

so each new generation starts on a mapping-segment boundary. The tested padding is 7 page ids at `C=32` and 15 at every larger control capacity; it is always strictly less than 16 and does not itself allocate physical storage.

Two real growth cycles exercise actual ownership transfer. Generation `0→1` aligns page `9` to `16`, completes migration, publishes generation 0's owner chain for retirement, and reclaims it with zero appended pages. Generation `1→2` aligns the next base to page `48`; while migration is active, old generation 1 ends in segment `2` and current generation 2 begins in segment `3`, so whole-segment ownership is disjoint. The trigger safely reuses one reclaimed extent only after **32 scrub writes**, allocates one additional fresh extent, and appends **34 pages / 139,264 bytes**; its bounded migration completes on the next transaction with **0 additional appended pages**. After cleanup, all 34 keys remain visible.

The process-crash evidence covers **12/12 exact cases**: four first-migration kills, four reuse-backed migration kills, and four generation-integrated reclaim kills. Maximum pre-recovery tail in the insert matrices is **139,264 bytes** and is truncated to zero. Recovery remains generation-scan-free, radix-scan-free, redo-free, and idempotent on a second pass.

The result establishes safe whole-segment retirement only for the aligned candidate and the tested single-writer process-crash model. v0.33 intentionally allows only one retirement backlog at a time; a new migration is rejected until that backlog drains.

### v0.34 — bounded FIFO retirement publication

v0.34 removes v0.33's rule that one retired generation must be fully reclaimed before another migration can start. Each completed generation now receives one fixed retirement descriptor containing its generation id, intrusive ownership cursor, remaining segment count, and next-descriptor pointer. Descriptors have two committed-epoch copies; the superblock stores only queue head, tail, and count.

The deterministic enqueue controls compare a flat serialized manifest and a head-to-tail linked enqueue against the candidate:

| Queued generations | Flat manifest bytes | Naive tail-walk visits | Candidate descriptor pages | Candidate max descriptor preads | Candidate max descriptor pwrites |
|---:|---:|---:|---:|---:|---:|
| 1 | 70 | 1 | 2 | 2 | 2 |
| 4 | 205 | 4 | 2 | 2 | 2 |
| 16 | 758 | 16 | 2 | 2 | 2 |
| 64 | 3,014 | 64 | 2 | 2 | 2 |
| 256 | 12,351 | 256 | 2 | 2 | 2 |

A real primary run reaches three simultaneously retired generations `[0,1,2]` with materialized-segment counts `[1,1,2]` before any cleanup. Enqueue performs zero history walks and appends exactly two descriptor pages per completed generation. With `B=2`, reclaim follows only the FIFO head and at most two ownership links, appends zero pages, performs at most two descriptor `pread`s and at most one descriptor `pwrite`, and drains generations in order.

After cleanup, the next real migration reuses four reclaimed extents only after **128 scrub writes** — exactly 32 data pages per extent — and all 129 live keys remain visible. The crash evidence covers **19/19 exact cases** across empty-queue enqueue, non-empty enqueue with tail linking, partial-head cursor update, and head dequeue. Recovery performs zero generation-page scan, zero radix-node scan, zero retirement-descriptor scan, and zero logical redo, and a second recovery performs zero additional truncation.

The result is a bounded-foreground-work claim, not a constant-storage claim. Descriptor pages are append-only in v0.34, and the real-primary queue depth exercised is three; the larger queue counts above are arithmetic/control cases.

### v0.35 — recyclable retirement descriptors

v0.35 replaces append-only retirement-descriptor history with a tagged descriptor free list. Queue roots, queue links, free-list roots, and free-list links all carry `(base_page, incarnation)`. A dequeued descriptor is rewritten `FREE` under its current incarnation; reuse increments that incarnation before the descriptor is republished as `QUEUED`. Dual-copy reads first select the newest record visible at the committed epoch and only then validate the expected tag, so a still-CRC-valid obsolete copy cannot satisfy an old identity after reuse.

For completed-generation controls `H={1,4,16,64,256}`, the append-only baseline consumes `[2,8,32,128,512]` descriptor pages while the serial recyclable control remains `[2,2,2,2,2]`, with zero enqueue history walks. The real primary establishes a three-descriptor/six-page pool across generations `[0,1,2]`, drains it to the free list, then reuses the same physical pairs for later generations. The first reuse needs 2 descriptor `pread`s and 1 descriptor `pwrite`; non-empty reuse needs 4 reads and 2 writes. No additional descriptor pages are appended after that pool exists.

The stale-reference fixture captures physical page 158 at incarnation 1 and then reuses it for generation 3 at incarnation 2. `(158,1)` is rejected with an incarnation mismatch while `(158,2)` resolves the newer descriptor. A direct two-copy regression separately verifies the ordering rule with epoch-1/incarnation-1 and epoch-2/incarnation-2 records.

The process-crash matrix covers **29/29 exact cases** across fresh empty/non-empty enqueue, empty/non-empty reuse, partial head reclaim, and final dequeue-to-free. Recovery performs zero generation-page scan, zero radix-node scan, zero retirement-descriptor scan, and zero logical redo; every second recovery performs zero additional physical truncation. All 258 inserted keys remain visible in the real recycling run.

The surviving storage claim is `Θ(peak concurrently allocated descriptor pool)`, not constant storage for arbitrary live backlog. The tested real pool peak is three, recycled pages remain inside the file rather than shrinking it, and descriptor incarnations are finite uint64 values.

### v0.36 — descriptor tail-release falsification

v0.36 challenges that retained peak-pool boundary with a deliberately head-local file-tail candidate. After real retirement work drains, the candidate inspects only the committed descriptor free-list head and can release its two-page pair only when `free_head_page + 2 == next_physical_page`; otherwise it stops without scanning history or relocating data.

The one-descriptor fixture places the free pair at `[52,54)` with committed frontier 88, leaving **34 committed suffix pages**. The three-descriptor peak leaves free pairs at `[158,160)`, `[88,90)`, and `[52,54)` with committed frontier 296; the closest pair still has **136 committed suffix pages** above it. A diagnostic full-free-chain snapshot confirms every observed free pair is buried, but that traversal is measurement only and is not counted as candidate work.

The candidate therefore performs **0 history walks, 0 relocations, and 0 physical-page releases**. This falsifies head-only tail truncation under the current interleaved append-local placement; it does not prove all bounded descriptor-pool reduction schemes impossible. Because the surviving candidate mutates no state, v0.36 adds no new crash-publication claim. The next positive design must change or escape placement before it can earn release and recovery evidence.

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
python run_recovery_interruption_experiment.py
python run_persistence_fault_experiment.py
python run_lazy_generation_allocation_experiment.py
python run_segmented_extent_mapping_experiment.py
python run_integrated_segmented_primary_experiment.py
python run_fixed_width_radix_node_experiment.py
python run_retired_generation_reclamation_experiment.py
python run_generation_boundary_reclamation_experiment.py
python run_retirement_queue_experiment.py
python run_recyclable_retirement_descriptor_experiment.py
python run_descriptor_tail_release_experiment.py
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
python verify_recovery_interruption_results.py
python verify_persistence_fault_results.py
python verify_lazy_generation_allocation_results.py
python verify_segmented_extent_mapping_results.py
python verify_integrated_segmented_primary_results.py
python verify_fixed_width_radix_node_results.py
python verify_retired_generation_reclamation_results.py
python verify_generation_boundary_reclamation_results.py
python verify_retirement_queue_results.py
python verify_recyclable_retirement_descriptor_results.py
python verify_descriptor_tail_release_results.py
```

CI runs the historical chain, while the v0.36 verifier workflow additionally pins and reproduces the canonical v0.36 result hash.

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

Experimental membership alternative
  -> bounded cuckoo primary + explicit exact overflow
  -> bounded incremental migration
  -> fixed-page arithmetic addresses + dual committed copies
  -> cross-store visibility epoch
  -> residue re-derived on restart
  -> explicit volatile/durable persistence-ordering controls
  -> naive lazy generation allocation rejected for Theta(C) address span
  -> append-local segmented extent mapping survives bounded sparse-path model
  -> integrated segmented primary survives tested real process-crash matrix
  -> fixed-width 256-slot radix nodes remove representation-driven node splitting
  -> intrusive segment lifecycle bounds reclamation work per explicit cleanup step
  -> fixed-footprint scrub is required before safe observed cross-owner extent reuse
  -> segment-aligned generation bases make real whole-segment ownership disjoint
  -> real migration completion publishes retired ownership for bounded cleanup
  -> dual-copy FIFO retirement descriptors decouple later migrations from earlier cleanup
  -> superblock queue roots keep enqueue head/tail-local and recovery queue-scan-free
  -> tagged descriptor identities `(page, incarnation)` make physical descriptor reuse explicit
  -> descriptor free-list reuse stops completed-history-only descriptor growth after sufficient pool capacity
  -> physical tail release requires tail-compatible placement; a free identity alone does not make a buried pair truncatable
```

## Next falsification target — segregated reclaimable descriptor arena

v0.36 shows that more tail metadata cannot release descriptor pairs already buried beneath committed append-local suffix state. The next candidate must change placement rather than merely describe it more precisely.

The next question is:

\[
\boxed{
Can retirement descriptors be placed in a segregated reclaimable region so excess post-peak capacity
can be returned or transferred with bounded metadata work, without history scans, ABA aliasing,
crash resurrection, or foreground work proportional to retained pool size?
}
\]

A positive mechanism must preserve incarnation-aware identity if physical addresses are reused, define publication order for arena release/reallocation, keep recovery scan-free, and distinguish internal reuse, file-length reduction, and filesystem block reclamation. Reject it if release requires descriptor-history traversal, if stale tagged references can resolve after reuse, if crash recovery can resurrect transferred capacity, or if foreground work scales with the historical retained pool.
