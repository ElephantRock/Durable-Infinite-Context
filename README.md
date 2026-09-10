# Durable Infinite Context — Minimum Falsifiable Prototype v0.32

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

The v0.18–v0.32 hash/cuckoo/overflow/fixed-page structures remain **experimental alternatives**, not replacements for that production B-tree. They have progressively earned bounded migration scheduling, bounded modeled placement work, explicit rare overflow, crash-atomic hybrid admission, arithmetic-addressed fixed pages, cross-store visibility gating, interrupted-recovery convergence, an explicit volatile/durable persistence-ordering model, a modeled bounded-address segment map, an integrated append-local segmented primary, fixed-width radix nodes, and now a scoped fixed-budget mapping-lifecycle reclamation mechanism with explicit stale-payload isolation on reuse.

v0.28 adds a negative result: simply removing eager full-generation `ftruncate` does **not** bound stale file-length residue while the same capacity-scaled arithmetic page addresses remain. One logical page can be materialized at a sparse offset `Theta(C)` beyond the committed frontier. v0.29 then survives a narrower modeled falsification with append-local fixed-size segments and an eight-level dual-copy radix map. v0.30 integrates that mapper into the real experimental fixed-page primary: across forced migration at `C={32,2048,131072,4194304}`, the largest observed physical append is 147,456 bytes, target lookup remains 20 user-space `pread`s, and all 16 real `SIGKILL` cases expose exact pre/post committed state with scan-free, redo-free frontier recovery. v0.31 then falsifies the mapper's variable-width JSON node representation: effective one-page fanout falls to 154 near the uint64 limit. A fixed 256-bit occupancy bitmap plus 256 uint64 pointer slots uses 2,103 bytes, round-trips all 256 edges at every tested pointer magnitude, and carries a real 255→256 root transition through 5/5 exact `SIGKILL` cases with zero node splits. v0.32 next falsifies mapping-only free-extent reuse: a CRC-valid retired payload becomes visible under a new logical mapping. Intrusive dual-copy lifecycle headers plus a committed retirement cursor bound each cleanup step to `B=3` ownership visits in the fixed experiment, while a 32-page fixed-footprint scrub prevents the demonstrated payload resurrection before reuse publication. Reclaim is 4/4 exact under `SIGKILL`; reuse is 5/5 exact, with scan-free, redo-free recovery.

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
- hardware power-loss or torn-write safety from the v0.30–v0.32 process-crash results;
- mathematically unbounded physical identifiers beyond the fixed uint64 pointer model;
- constant total retired-generation cleanup work; draining `K` retired materialized segments still takes `Theta(K)` ownership visits;
- filesystem block deallocation, hole punching, or reclamation of now-empty radix metadata nodes;
- automatic safe ownership attribution across unaligned primary-generation boundaries;
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

Detailed evidence lives in `RESULTS_V0.*.md`, `*_results.json`, `*_evidence.json`, and executable `verify_*_results.py` gates.

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
```

CI runs this chain and uploads the milestone evidence ledgers as artifacts.

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
```

## Next falsification target — generation-boundary integration

v0.32 bounds scoped mapping-lifecycle cleanup, but the real primary's logical generation ranges are not guaranteed to align to the 16-page mapping-segment geometry. A physical mapping segment can therefore contain logical pages from more than one primary generation.

The next question is:

\[
\boxed{
Can actual primary-generation retirement acquire safe segment ownership without capacity-sized padding, global scans, or unbounded migration work?
}
\]

v0.33 should compare at least two mechanisms: segment-aligned generation allocation versus finer-grained/shared-segment ownership (for example, per-segment live-generation references). It should force multiple real growth transitions, retire actual primary generations, and inject process death across ownership transfer and reclamation. Reject any mechanism that frees a segment while still-live logical pages share it, requires `Theta(C)` alignment padding, scans the full radix/generation range, or moves an unbounded rewrite onto the mutation path.
