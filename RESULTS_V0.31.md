# v0.31 — Fixed-width radix nodes

## Observe

v0.30 integrated the segmented extent map into the real fixed-page primary and survived its fixed single-writer process-crash test. One representation boundary remained explicitly untested: radix nodes were still serialized as JSON inside a 4096-byte fixed record.

The logical radix fanout is exactly 256, but JSON entry size is not fixed. A child pointer with more decimal digits consumes more bytes. Therefore a semantically legal 256-edge node can stop fitting one physical page before its radix fanout is exhausted.

## Diagnose

This is not a radix split problem in the logical structure. It is a mismatch between a fixed-fanout address structure and a variable-width textual representation.

For the v0.30 JSON layout, the usable record payload is 4072 bytes. The measured effective fanout falls as pointer magnitude grows:

| Pointer base | Largest JSON node that fits | Full 256-entry payload | Full fanout fits? |
|---:|---:|---:|:---:|
| 1,000 | 256 | 2,729 B | yes |
| 1,000,000 | 256 | 3,497 B | yes |
| 1,000,000,000 | 244 | 4,265 B | no |
| 1,000,000,000,000 | 207 | 5,033 B | no |
| 1,000,000,000,000,000 | 180 | 5,801 B | no |
| 1,000,000,000,000,000,000 | 159 | 6,569 B | no |
| `2^64 - 256` | 154 | 6,825 B | no |

So the effective physical fanout of the JSON control is pointer-magnitude dependent even though the logical fanout is fixed.

## First-principles requirement

A fixed-radix node should have physical capacity determined by its radix fanout, not by serialization length or pointer magnitude.

An eight-bit radix position has exactly 256 possible edges. Therefore a node can be represented directly by:

- a 256-bit occupancy bitmap; and
- 256 fixed-width 64-bit pointer slots.

No node needs more than 256 entries, so no structural node split is necessary if all 256 slots fit one physical page.

## Hypothesis

> Replacing variable-width JSON radix nodes with a fixed 256-slot binary layout can preserve the v0.30 dual-copy publication/recovery protocol while restoring true 256-way physical fanout and eliminating recursive node-split behavior.

The v0.31 node layout is:

- 4096-byte physical page;
- 8-byte magic;
- 64-bit epoch;
- fixed depth and entry-count fields;
- CRC32;
- 32-byte occupancy bitmap;
- 2048-byte array of 256 uint64 pointers.

The complete encoded record uses 2103 bytes and leaves 1993 bytes of padding.

## Prediction

For every tested uint64 pointer magnitude, a full 256-entry node should:

1. encode into exactly one 4096-byte page;
2. round-trip exactly;
3. require no node split.

For a real dense-root transition from 255 to 256 entries, the final fresh top-level mapping should retain the existing v0.30 bound:

\[
2\times16 + 2\times(8-1)=46\text{ appended pages}=188{,}416\text{ bytes}
\]

and require eight radix-node writes: seven fresh child nodes plus one alternate root copy.

Under process death, kills before superblock publication should expose the exact 255-entry committed image; a kill after committed publication should expose the exact 256-entry image. Recovery should use only the committed physical frontier and should not scan generation pages or radix nodes.

## Test

The deterministic encoding sweep uses pointer bases from `10^3` through `2^64 - 256` and compares:

1. the v0.30 JSON payload limit; and
2. the v0.31 fixed-width codec.

The real-file test then:

1. initializes the fixed-width segmented store;
2. materializes 255 mappings whose first radix digits are `0..254`, producing a 255-entry root;
3. materializes the missing `255` edge;
4. verifies a full 256-entry root with zero splits;
5. injects real `SIGKILL` at `allocated`, `children_written`, `parent_written`, `dependencies_synced`, and `committed`;
6. compares each restart image against the exact pre/post committed state;
7. performs recovery twice and checks scan-free convergence.

The experiment also replays the v0.16 semantic guard before exercising the storage alternative.

## Results

The JSON control is falsified as a fixed physical fanout representation. Its effective one-page fanout falls from 256 to 154 across the pointer-magnitude sweep.

The fixed-width codec round-tripped all 256 entries at every tested magnitude, including pointers through `2^64 - 1`. Every node remained one 4096-byte page, with 2103 bytes used and 1993 bytes padding.

The real dense-root transition produced:

| Metric | Observed |
|---|---:|
| Root entries before | 255 |
| Root entries after | 256 |
| Node splits | 0 |
| Recursive split depth | 0 |
| Fresh segments allocated | 1 |
| Appended physical pages | 46 |
| Appended bytes | 188,416 |
| Radix-node `pwrite`s | 8 |
| Publication `fsync`s | 2 |

The 255-entry fixture itself materialized 255 sparse top-level paths. Its process-visible file length was 48,062,464 bytes while allocated blocks were 7,360,512 bytes; this is a sparse-file fixture characteristic, not a reclamation result.

The crash matrix was **5/5 exact**:

- `allocated`: exact pre-state, 188,416-byte uncommitted tail;
- `children_written`: exact pre-state, 188,416-byte uncommitted tail;
- `parent_written`: exact pre-state, 188,416-byte uncommitted tail;
- `dependencies_synced`: exact pre-state, 188,416-byte uncommitted tail;
- `committed`: exact post-state, zero uncommitted tail.

Every pre-commit recovery truncated the tail back to the committed frontier. Every case converged with:

- zero generation-page scans;
- zero mapping-node scans;
- zero logical redo;
- two superblock reads for frontier discovery;
- zero uncommitted tail after recovery;
- zero bytes truncated on the second recovery pass.

## Result

The fixed-width hypothesis **survived this falsification**.

The important revision is that v0.31 does not add a bounded split algorithm. It removes the artificial split requirement:

\[
\boxed{FixedRadixFanout + FixedWidthSlots \Rightarrow NoRepresentationDrivenNodeSplit}
\]

within the fixed uint64 pointer model.

The v0.30 JSON node representation is therefore rejected as the durable representation for a full byte-radix node. The fixed-width representation becomes the surviving experimental mapper candidate.

## Surviving claim

Within the fixed eight-level byte-radix and uint64 physical-pointer model, a 256-bit occupancy bitmap plus 256 uint64 pointer slots restores true 256-way one-page physical fanout independent of pointer magnitude. A real 255→256 root transition preserved the v0.30 two-barrier publication protocol, required no node split, remained within the 46-page fresh-path append bound, and produced exact pre/post process-crash images with scan-free frontier recovery.

## Evidence anchor

Initial successful v0.31 CI:

- workflow: `v0.31 experiment`;
- run #1;
- run ID `34432870241`;
- head `58c4e34f4469dc5882b543d8eefb11e2a827ef04`;
- artifact ID `10135116587`;
- artifact SHA-256 `df78992947a07cb6a4eb54a25a6b2135e311238795299446c001b7598a8fa9f1`;
- `fixed_width_radix_node_results.json` SHA-256 `b208fa84c4bd1eec0a5e77f24cdfe23794e09c1fa3cf41ba667808dcb3888e88`.

The committed ledger is replayed by `verify_fixed_width_radix_node_results.py`.

## Non-claims

v0.31 does **not** establish:

- mathematically unbounded logical or physical identifiers; both are fixed-width in this experiment;
- hardware power-loss, torn-sector, drive-cache, or filesystem-journal correctness;
- arbitrary multi-writer, distributed, or replicated consistency;
- device-I/O bounds from user-space `pread`/`pwrite` counts;
- constant total mapping storage as materialized paths accumulate;
- filesystem allocated-block reclamation;
- production latency, throughput, concurrency, or a production-ready replacement for the SQLite membership B-tree.

## Next falsification target — retired-generation reclamation

v0.31 removes representation-driven node overflow, but the segmented primary still accumulates committed mappings and physical segments for retired generations. Those bytes are implementation history, not necessarily live semantic state.

The next question is:

\[
\boxed{
Can retired generation storage be reclaimed incrementally without scanning generation capacity or the full radix map,
while preserving crash visibility and a fixed per-mutation cleanup budget?
}
\]

A useful v0.32 experiment should make retired-segment ownership explicit without a serialized `O(K)` manifest, reclaim through a bounded incremental queue, inject process death across unlink/reuse/publication phases, and verify that recovery neither scans the logical generation range nor resurrects reclaimed mappings. If reclamation requires a capacity-sized walk, a global map scan, or an unbounded rewrite, the current segmented design still leaks operational non-locality into lifecycle management.
