# v0.29 — Segmented extent mapping

## Observe

v0.28 falsified naive lazy generation allocation. Removing eager full-generation `ftruncate` changed *when* stale file-length range appeared, but not the capacity-scaled arithmetic offset that caused it: one logical page could still extend process-visible file length by `Theta(C)`.

The remaining question was whether indirection could remove that address-span mechanism without merely moving non-locality into descriptor lookup, descriptor mutation, or persistence ordering.

## Diagnose

A flat extent table indexed directly by logical segment id is not sufficient. Even if each descriptor is only 16 bytes, placing descriptor `segment_id` at an arithmetic file offset makes a sparse high-id descriptor write extend the descriptor file by a capacity-dependent range.

Therefore the mapping layer itself needs all of the following properties simultaneously:

- append-local physical placement near the committed frontier;
- bounded lookup fan-out independent of generation capacity;
- bounded per-allocation metadata writes;
- explicit publication ordering so a committed mapping cannot outlive absent dependencies;
- visible accounting for total metadata growth with the number of materialized segments `K`.

## First-principles requirement

For crash residue to be bounded by a fixed physical allocation unit, every newly reachable physical allocation must remain within a bounded address span of the committed frontier. Logical identity may be large, but it must not determine a sparse physical file offset directly.

The mapping structure may grow in total size, but the *path needed for one lookup or one new sparse allocation* must have a fixed bound under the stated namespace.

## Hypothesis

> A fixed-size physical segment plus an append-allocated, fixed-depth dual-copy radix extent map can decouple high logical segment ids from capacity-scaled physical offsets while keeping sparse first-allocation tail span, lookup fan-out, per-allocation metadata writes, and publication barriers bounded across capacity.

The fixed candidate uses:

- 4096-byte pages;
- four slots per bucket;
- 16 logical bucket pages per physical segment;
- two fixed physical copies per bucket page;
- an eight-level radix map with 8 bits per level and fanout 256;
- a fixed 64-bit logical-segment namespace;
- two physical copies per radix node and two fixed superblock copies.

A packed 16-byte direct-address flat descriptor serves as the discriminating control.

## Prediction

For one fresh sparse high-id mapping, the radix candidate reserves:

\[
2\times16 + 2\times(8-1)=46
\]

physical pages, so the process-visible tail is:

\[
\boxed{46\times4096=188{,}416\text{ bytes}}
\]

independent of generation capacity.

Lookup should remain:

\[
\boxed{2\text{ superblock preads}+2\times8\text{ radix-node preads}+2\text{ data preads}=20}
\]

modeled user-space `pread` calls.

Fresh-path publication should remain eight radix metadata `pwrite`s, one alternate-superblock `pwrite`, and two `fsync` barriers. In contrast, the flat descriptor control should retain a sparse descriptor-file span that grows with the high logical segment id.

Dense descriptor storage is expected to grow with materialized segment count `K`; hiding that growth would falsify the accounting discipline even if the sparse path were bounded.

## Test

The deterministic sweep fixes old capacities:

```text
C = {1024, 16384, 262144, 4194304}
```

with 4096 keyed-BLAKE2 sampled first-bucket keys per capacity. It measures arithmetic process-visible file-length span, modeled user-space `pread`/`pwrite` counts, dense descriptor page growth, and an abstract persistence-ordering fault model.

The persistence test compares two protocols:

1. dependency-before-commit: write segment data and mapping dependencies, `fsync`, write the alternate committed superblock, then `fsync` again;
2. one-barrier control: write data, mapping metadata, parent, and superblock before a single `fsync`.

Before an `fsync` returns, the model permits any subset of writes since the prior barrier to be durable.

## Results

| Old `C` | Logical segments | Flat last-descriptor span | Radix sparse tail | Radix lookup preads | Fresh metadata pwrites | Dense radix descriptor pages |
|---:|---:|---:|---:|---:|---:|---:|
| 1,024 | 32 | 4,096 B | 188,416 B | 20 | 8 + 1 superblock | 16 |
| 16,384 | 512 | 8,192 B | 188,416 B | 20 | 8 + 1 superblock | 18 |
| 262,144 | 8,192 | 131,072 B | 188,416 B | 20 | 8 + 1 superblock | 78 |
| 4,194,304 | 131,072 | 2,097,152 B | 188,416 B | 20 | 8 + 1 superblock | 1,040 |

The flat descriptor control grows from 4 KiB to 2 MiB over the sweep. Its sampled p95 rises to 2,002,944 bytes at the largest capacity.

The segmented radix candidate remains fixed at 188,416 bytes of sparse first-allocation tail, 20 modeled user-space `pread`s, eight fresh-path radix metadata `pwrite`s plus one superblock `pwrite`, and two `fsync` barriers across all tested capacities.

Dense radix descriptor storage does not remain constant: dual-copy descriptor pages grow from 16 to 1,040 over the same sweep. This is expected and explicitly retained in the result.

Persistence enumeration produced:

- dependency-before-commit protocol: **12 cases, 0 invalid**;
- one-barrier control: **17 cases, 7 invalid**.

The first one-barrier counterexample has only the new superblock durable before the single `fsync`, exposing a committed pointer whose data and mapping dependencies are absent.

## Result

The hypothesis **survived this modeled capacity-scaling falsification**.

\[
\boxed{AppendLocalSegmentation + FixedDepthMapping \Rightarrow BoundedSparseAddressSpan}
\]

under the fixed 64-bit namespace and stated abstract persistence model.

This removes the specific v0.28 mechanism: a high logical bucket identity no longer forces a high sparse physical file offset on first materialization.

## Surviving claim

Within a fixed 64-bit logical-segment namespace and the stated persistence model, append-local segmentation plus a fixed-depth dual-copy radix extent map removes the v0.28 capacity-scaled address-span mechanism for a first sparse high-id write. This earns a bounded-address candidate, not a production replacement: total descriptor storage still grows with materialized segments.

## Evidence anchor

Initial successful CI:

- run #271;
- run ID `34258805241`;
- head `e4d1045a0bf4282cd991f610039b1baaa4604e8c`;
- artifact ID `10069861619`;
- artifact SHA-256 `14320ed2f11fce149a735da64d442cf4d594eec73d664fb13da842a0de285034`;
- `segmented_extent_mapping_results.json` SHA-256 `af728a264be667eb23de0292a4c40a42011ec412133939d5b32f49da53431702`.

The committed ledger is replayed by `verify_segmented_extent_mapping_results.py`.

## Non-claims

v0.29 does **not** establish:

- mathematically unbounded logical identifiers; the tested map has fixed depth and a 64-bit logical-segment namespace;
- constant total metadata storage; dense radix descriptor storage grows with materialized segment count `K`;
- filesystem allocated-block bounds, device-I/O bounds, latency, or journaling behavior from modeled user-space call counts;
- hardware power-loss, torn-sector, drive-cache, or filesystem-journal correctness;
- end-to-end integration with the v0.24/v0.25 fixed-page primary;
- production concurrency, throughput, reclamation, or a production-ready extent map.

## Next falsification target

v0.30 should integrate the surviving extent-map mechanism into the experimental fixed-page primary rather than continue reasoning about it only as an arithmetic model.

The discriminating question is:

\[
\boxed{
Can the segmented mapping preserve the v0.24/v0.25 lookup and visibility semantics under real file operations and process death,
while keeping uncommitted file-length residue bounded independently of generation capacity?
}
\]

A useful v0.30 test should replace direct generation-page arithmetic with the segmented map, exercise real `pread`/`pwrite`/`fsync` ordering, inject `SIGKILL` across allocation and publication phases, verify exact pre/post committed images, measure process-visible residue against generation capacity, and ensure restart recovery does not scan the generation or require unbounded logical redo. If end-to-end crash safety requires a capacity-scaled scan, unbounded descriptor repair, or a larger sparse tail, the surviving v0.29 mechanism is insufficient as an integrated storage design.
