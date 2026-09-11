# v0.33 — Generation-boundary reclamation integration

## Observe

v0.32 earned bounded cleanup and safe physical-extent reuse only for a lifecycle model in which one 16-logical-page mapping segment belongs to one generation. The real experimental primary did not satisfy that precondition: generations were allocated back-to-back in logical page-id space.

For capacity `C` and bucket size `4`, a generation occupies:

```math
GenerationPages(C)=C/4+1.
```

That trailing `+1` is the stash page. For every tested power-of-two capacity, the next back-to-back generation begins inside the predecessor's final 16-page mapping segment. At `C=32`, for example, generation 0 occupies logical pages `0..8`; generation 1 would start at page `9`, so both would use mapping segment `0`.

Whole-segment retirement is therefore unsafe if attached directly to the pre-v0.33 layout: reclaiming a retired generation could free a physical segment whose remaining logical addresses belong to a live generation.

## First principle

A physical extent may be reclaimed as one ownership unit only when live logical generations are disjoint at that same ownership granularity.

The repair should not replace the problem with:

- a page-by-page ownership walk over generation capacity;
- an `O(K)` serialized page ownership manifest;
- a global radix scan; or
- capacity-scaled physical padding.

## Hypothesis

Align every new logical generation base to the next 16-page mapping-segment boundary:

```math
AlignedBase(p)=16\left\lceil p/16\right\rceil.
```

The maximum logical padding is therefore 15 page ids. Those skipped logical ids need not allocate physical storage because physical placement remains lazy and append-local through the v0.31 fixed-width radix mapper.

With segment-disjoint generation ranges, the v0.32 intrusive ownership chain can be attached directly to real primary writes. Real migration completion can publish the old generation's ownership head/count as the retirement cursor, and cleanup can reclaim those mappings under the existing fixed budget.

## Fixed controls

The fixed boundary control uses capacities:

```text
32, 128, 2048, 131072, 4194304
```

At every capacity the unaligned layout shares the predecessor's final segment. The aligned candidate shares none:

| Old capacity | Old pages | Unaligned next base | Shared? | Aligned next base | Padding | Shared after alignment? |
|---:|---:|---:|:---:|---:|---:|:---:|
| 32 | 9 | 9 | yes | 16 | 7 | no |
| 128 | 33 | 33 | yes | 48 | 15 | no |
| 2,048 | 513 | 513 | yes | 528 | 15 | no |
| 131,072 | 32,769 | 32,769 | yes | 32,784 | 15 | no |
| 4,194,304 | 1,048,577 | 1,048,577 | yes | 1,048,592 | 15 | no |

Thus the tested address-space cost is bounded by:

```math
0\le Padding<16
```

logical page ids per new generation, independent of capacity.

## Real two-migration integration test

The real-file fixture begins at `C=32`, `max_load=0.50`, with migration slot budget `64`.

### Generation 0 → 1

The 17th key starts the first migration. The new generation base moves from the unaligned page `9` to aligned page `16`, producing 7 logical padding ids. The migration completes in the same transaction because its virtual migration range is 40 slots.

Observed clean trigger:

- migration started and completed;
- 16 rows moved while 40 source slots were scanned;
- 1 new physical data extent;
- 1 lifecycle-header write;
- 1 radix-node rewrite;
- 34 physical pages / 139,264 bytes appended;
- 2 `fsync` barriers.

Migration completion publishes the old generation's real owner chain directly into the retirement cursor. Reclamation then drains that retired generation with zero appended pages and produces one reusable free extent. All 17 keys remain visible.

### Generation 1 → 2

The second generation begins at logical page `16` and ends in mapping segment `2`. The next generation is aligned to page `48`, whose first mapping segment is `3`. Therefore the active old/current generations are segment-disjoint:

```text
old last segment = 2
current first segment = 3
```

The migration range is 72 virtual slots, so the fixed 64-slot migration budget intentionally requires a second bounded transaction to finish. The trigger transaction:

- uses 15 logical padding ids;
- reuses one reclaimed physical extent;
- scrubs exactly 32 physical data pages before remapping it;
- allocates one additional fresh extent;
- appends 34 pages / 139,264 bytes;
- performs 2 radix-node rewrites.

The completion transaction appends zero additional pages. After bounded reclamation of generation 1, all 34 keys remain visible.

This is useful negative evidence against a hidden assumption in the initial test harness: a migration trigger is not required to finish the entire migration when the explicit slot budget is smaller than the source virtual range. The implementation was retained; the test was corrected to require bounded convergence instead of single-transaction completion.

## Process-crash evidence

### First real migration

Four `SIGKILL` points are injected at:

```text
allocated
pages_written
data_synced
committed
```

All 4/4 cases expose exactly the required pre-commit or post-commit state. The maximum uncommitted physical tail is 139,264 bytes and recovery removes it to zero. Recovery performs zero logical redo, zero generation-page scan, and zero radix-node scan; a second recovery pass is physically idempotent.

### Reuse-backed real migration

The second migration is tested with an already reclaimed free extent. Four `SIGKILL` points are injected at:

```text
reused_extent_scrubbed
pages_written
data_synced
committed
```

All 4/4 cases are exact. In the clean control the transaction reuses one extent and performs the required 32 scrub writes before publication. A crash after scrubbing but before superblock publication changes only bytes belonging to a still-FREE extent and therefore leaves committed semantics unchanged.

### Generation-integrated reclaim

Four `SIGKILL` points are injected at:

```text
mapping_unlinked
free_header_written
dependencies_synced
committed
```

All 4/4 cases preserve the exact committed state, and every live current-generation key remains visible. The clean reclaim step uses budget 1, performs 4 lifecycle reads, 1 lifecycle write, 1 radix leaf rewrite, appends 0 pages, and uses 2 `fsync` barriers. Recovery remains scan-free and redo-free.

Across the three matrices, v0.33 therefore covers **12/12 exact real process-crash cases**.

## Surviving result

The tested candidate removes the concrete mixed-generation ownership defect without a per-page ownership manifest:

```math
GenerationBoundaryAlignment
+ IntrusiveGenerationOwnership
+ BudgetedReclaim
+ ScrubBeforeReuse
```

survives the fixed v0.33 real-file test.

The result earns the narrower claim that real primary migration can publish whole-segment retirement ownership safely when new generation bases are aligned to the segment granularity. The logical alignment cost is bounded by 15 unused page ids per generation; reclaim remains zero-append and budget-local; reuse retains v0.32's fixed 32-page invalidation; and tested process-crash recovery remains exact and scan-free.

## Claim boundary

v0.33 remains a single-writer process-crash experiment over fixed 64-bit logical-segment and physical-pointer namespaces. It intentionally permits only one retired-generation cleanup backlog at a time: a later migration is rejected until the previous backlog drains.

It does **not** establish:

- hardware power-loss or torn-sector safety;
- arbitrary multi-writer/distributed correctness;
- filesystem allocated-block reclamation;
- pruning of empty/orphaned radix metadata nodes;
- production latency or throughput;
- mathematically unbounded identifiers; or
- nonblocking progress when retirement production outpaces cleanup.

## Next falsification target

v0.34 should attack the remaining one-backlog restriction. The question is whether multiple completed generations can queue for incremental reclamation without blocking later migrations and without introducing an `O(history)` serialized retirement manifest, a global discovery scan, or unbounded foreground work.

A candidate should make retirement descriptors themselves incrementally addressable and crash-published. It should be rejected if queue growth makes any mutation perform work proportional to backlog/history, if recovery scans the queue, if reclamation can cross generation ownership boundaries, or if descriptor reuse resurrects a retired generation.
