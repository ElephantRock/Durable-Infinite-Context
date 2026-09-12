# v0.35 — Recyclable retirement descriptors

## Observe

v0.34 removes the one-backlog admission restriction with a FIFO of fixed dual-copy retirement descriptors, but every completed generation appends a new two-page descriptor pair. Dequeue removes the descriptor from the committed queue roots without making its physical pair reusable, so total descriptor storage still grows with completed-generation history.

## Diagnose

Reusing only the physical page number is unsafe. A stale queue or free-list pointer can survive long enough to name the same page after that page has been repurposed for a different retirement descriptor. Without a logical incarnation, physical page reuse introduces an ABA identity hazard.

The missing primitive is therefore not merely a descriptor free list. Reusable descriptor identity must distinguish successive logical occupants of the same physical pair, and crash visibility must never let an obsolete physical copy satisfy an old identity after a newer incarnation commits.

## Derive

The v0.35 candidate tags every retirement-descriptor reference as:

```text
(base_page, incarnation)
```

Queue links, queue roots, and descriptor-free-list links carry both fields. A final dequeue rewrites the descriptor pair as `FREE` under its current incarnation and only then commits the updated queue/free roots. Reuse reads the committed free-head identity, increments its uint64 incarnation, rewrites that same physical pair as `QUEUED`, and publishes only the new tagged identity.

Dual-copy reads apply the critical ordering rule:

1. decode both physical copies;
2. discard records newer than the committed superblock epoch;
3. select the newest committed record;
4. only then validate expected incarnation and status.

Filtering by the expected incarnation before selecting the newest committed copy would be unsafe: an obsolete copy could satisfy an old reference after the same page pair had committed a later incarnation.

## Hypothesis

Tagged descriptor recycling can stop retirement-descriptor storage from following completed-generation history once a sufficient reusable pool exists, while keeping enqueue/reclaim work local to current queue/free heads and preserving scan-free crash recovery.

This is a peak-pool claim, not an `O(1)` claim for arbitrary live backlog. Required descriptor storage remains proportional to the peak number of concurrently allocated queue/free descriptors.

## Predictions

The candidate is rejected if any of the following occurs:

1. descriptor pages continue to be appended after reusable pool capacity exists for the tested workload;
2. recycling requires walking completed descriptor history;
3. a stale `(page, incarnation)` resolves after that page commits a later incarnation;
4. reuse fails to increment incarnation exactly once;
5. empty-queue reuse reads or writes beyond the free-head descriptor work expected by the candidate;
6. non-empty reuse grows beyond one free-head read plus one queue-tail read, or beyond one reused descriptor write plus one tail rewrite;
7. fresh tagged descriptor publication or recycled publication can expose mixed committed state under process death;
8. partial reclaim or final dequeue can expose mixed queue/free ownership under process death;
9. recovery scans descriptor history, generation pages, or radix nodes, or requires logical redo;
10. a second recovery performs additional physical truncation;
11. descriptor recycling loses or hides a live primary key.

## Test

### Storage-history controls

For completed-generation counts `H={1,4,16,64,256}`, the append-only control allocates:

```text
[2, 8, 32, 128, 512] descriptor pages
```

while the serial recyclable control remains at one dual-copy descriptor pair:

```text
[2, 2, 2, 2, 2] descriptor pages
```

and reports zero enqueue history walks at every tested history depth.

These are deterministic controls. They do not claim that arbitrary real queue depth can be represented with one descriptor pair.

### Real primary recycling

Starting at capacity 32 with `max_load=0.50` and migration-slot budget 512, 65 inserts create three queued retired generations:

```text
[0, 1, 2]
```

with remaining segment counts:

```text
[1, 1, 2]
```

The initial pool therefore contains three descriptor identities and appends six descriptor pages. Draining the queue moves all three descriptors into the descriptor free list without changing pool depth.

The next completed migration occurs at `k-128` and reuses the committed free-list head:

```text
retirement descriptor reuses          = 1
new retirement descriptor pages        = 0
retirement descriptor preads           = 2
retirement descriptor pwrites          = 1
queue/free descriptor counts           = 1 / 2
```

The captured stale identity on physical page 158 is incarnation 1. That same page becomes generation 3 at incarnation 2. Resolving `(158,1)` after the commit raises an incarnation mismatch, while `(158,2)` resolves the new generation.

The following growth transition starts at `k-256`, but the source contains 520 migration slots (capacity 512 plus eight stash slots), so the fixed budget of 512 cannot complete it on that insert. Completion occurs on `k-257`; the non-empty queue reuse then observes:

```text
retirement descriptor reuses          = 1
new retirement descriptor pages        = 0
retirement descriptor preads           = 4
retirement descriptor pwrites          = 2
queue/free descriptor counts           = 2 / 1
```

After five represented completed generations, the descriptor pool is still three descriptors. Descriptor pages appended after that pool was established are exactly zero, and all 258 inserted keys remain visible.

This does not mean the whole primary stops appending physical pages. The zero-append statement is specifically about retirement-descriptor pages after sufficient reusable descriptor capacity exists.

### ABA read-order regression

A direct unit regression keeps both physical copies valid simultaneously:

```text
copy 0: epoch 1, incarnation 1
copy 1: epoch 2, incarnation 2
```

At committed epoch 1, the old identity resolves. At committed epoch 2, a read expecting incarnation 1 is rejected even though the old physical copy remains CRC-valid; a read expecting incarnation 2 resolves the newer copy. This directly exercises newest-committed-copy selection before identity validation.

### Process-crash matrices

The final fixed experiment injects real `SIGKILL` across six protocols:

| Protocol | Cases | Tagged publication/reclaim edge |
|---|---:|---|
| Fresh enqueue into empty queue | 4 | new tagged descriptor written |
| Fresh enqueue into non-empty queue | 5 | new descriptor written; prior tail linked |
| Reuse into empty queue | 4 | free-head descriptor rewritten to next incarnation |
| Reuse into non-empty queue | 5 | reused descriptor written; prior tail linked |
| Partial reclaim of queue head | 5 | tagged head cursor/count updated |
| Final reclaim/dequeue | 6 | descriptor rewritten `FREE`; queue/free roots advanced |

All **29/29** cases expose the exact expected committed pre/post state. Every tested recovery reports:

```text
logical_work                    = 0
generation_pages_scanned        = 0
mapping_nodes_scanned           = 0
retirement_descriptors_scanned  = 0
```

and every second recovery performs zero additional physical truncation. Partial-reclaim and final-dequeue matrices also preserve all live keys.

## Result

**The v0.35 candidate survives the fixed falsification.** Within the tested single-writer process-crash model, descriptor page pairs can be recycled by tagging logical identity with a monotonically increasing incarnation. The real workload establishes a three-descriptor pool, reuses it for later completed generations with zero further descriptor-page append, rejects the demonstrated ABA-style stale reference, keeps reuse head-local, preserves all 258 live keys, and passes 29/29 exact crash cases with descriptor-history-scan-free recovery.

The surviving storage claim is deliberately narrow:

```text
retirement descriptor storage = Θ(peak concurrently allocated descriptor pool)
```

for this design, rather than growth with total completed-generation history once reusable capacity is available. The tested real pool peak is three descriptors; larger arbitrary live backlogs were not exercised.

## Evidence anchor

Initial successful exact-head gate with the corrected bounded-migration fixture and expanded crash coverage:

```text
workflow: CI
run number: 362
run id: 34646916905
head: 1966ec4f9c837af3081fdc0789c6cba9ae9087ff
artifact id: 10282253414
artifact digest: sha256:d31f27abd0397eea43aa63d85b1cb4d9c735a380517f62cc929adffea833ef80
recyclable_retirement_descriptor_results.json sha256: f571c4d986c1c1a5b85cb9b2e3fa89583c1eb3fab2fbcdfc36cbdd869f86802f
```

The repository freezes those exact result bytes as deterministic gzip evidence:

```text
recyclable_retirement_descriptor_results.json.gz sha256: b37f74697fdc1ce69752c2ae440d931d873dd2f6429ef5ffb172e81859641711
frozen gzip git blob sha1: 5caac8ba75de462264e13aabb1ddd25b5cb01f49
```

The verifier hashes the frozen compressed artifact, decompresses it, checks the raw-result hash and claim-bearing semantics, reruns the experiment, and requires byte-exact reproduction.

The verifier-enabled freeze head then passed the complete historical replay chain plus the v0.35 verifier:

```text
workflow: CI
run number: 365
run id: 34654495689
head: b47641f88ee693fdc746d2750caea07187726a64
v0.35 experiment: success
v0.35 frozen-evidence verifier: success
complete job: success
```

## Deliberate non-claims

- Descriptor storage is not constant for arbitrary peak live queue depth; it scales with peak concurrently allocated descriptor pool size.
- The real-primary experiment reaches a descriptor-pool peak of three and represents five completed generations; the `H={1,4,16,64,256}` history cases are deterministic storage controls.
- Recycled descriptor pairs are reused inside the file; v0.35 does not shrink the physical file or prove filesystem block deallocation.
- The incarnation field is a finite uint64 namespace. Exhaustion raises rather than providing mathematical unboundedness.
- The fixed crash matrix is single-writer process-death evidence. It does not establish hardware power-loss, torn-sector, drive-cache, filesystem-journal, arbitrary multi-writer, or distributed correctness.
- The demonstrated stale-reference rejection and direct read-order regression are bounded falsification evidence, not a universal proof that no ABA defect exists under every possible implementation or fault model.
- User-space `pread`/`pwrite` counts are invocation counts, not device-I/O guarantees.
- Total retired-generation cleanup remains proportional to retired materialized segments; descriptor recycling does not make total reclamation work constant.
- Diagnostic queue/free snapshots may traverse current descriptor chains; the foreground enqueue/reclaim locality claim does not rely on those diagnostics.
- v0.35 remains an experimental storage-layer alternative and does not replace the normalized SQLite production candidate earned through v0.16.

## Next falsification target

The next experiment should challenge the remaining peak-pool and physical-retention boundary rather than treating v0.35 as universal bounded storage. A candidate must distinguish whether descriptor-pool capacity can be safely reduced, returned to a broader allocator, or compacted after backlog peaks without reintroducing history walks, stale-identity aliasing, crash resurrection, or unbounded foreground rewrite work.
