# v0.34 — Queued retirement descriptors

## Observe

v0.33 makes each real primary generation segment-exclusive, so a completed generation can safely hand its intrusive ownership chain to reclamation. It still stores that retirement work in one scalar cursor. A new migration is rejected while `retire_remaining != 0`, which makes future growth depend on cleanup progress.

## Diagnose

The one-backlog restriction is not required by the segment-ownership invariant. The missing primitive is a durable way to publish more than one completed generation without serializing all older retirement state into the superblock or walking retirement history to find an enqueue point.

Two obvious controls both move history dependence onto the foreground path:

- a flat serialized list of all queued generations grows with queue depth;
- a singly linked queue with only a head pointer requires a head-to-tail walk to append the next generation.

For queued-generation counts `G={1,4,16,64,256}`, the deterministic flat-manifest control grows from 70 to 12,351 bytes and the naive tail-walk control grows from 1 to 256 descriptor visits.

## Derive

A completed generation needs only a fixed retirement descriptor:

```text
generation
ownership_cursor
remaining_segments
next_descriptor
```

The descriptor itself can use the same committed-epoch discipline as radix nodes and lifecycle headers: two physical copies, with readers selecting the newest valid copy whose epoch is no newer than the committed superblock epoch.

The superblock therefore needs only three queue roots:

```text
retirement_queue_head_page
retirement_queue_tail_page
retirement_queue_count
```

Enqueue can append one descriptor and update at most the existing tail's alternate copy. Reclaim can read only the head descriptor and follow at most an explicit segment budget. Restart needs none of these traversals because the committed physical frontier remains a superblock scalar.

## Hypothesis

A FIFO of fixed-size dual-copy retirement descriptors can remove v0.33's one-backlog admission restriction while keeping enqueue and reclaim foreground work independent of queued-generation history.

This hypothesis is intentionally narrower than constant metadata storage. Descriptor pages are append-only in v0.34, so total descriptor history still grows with completed generations.

## Predictions

The candidate is rejected if any of the following occurs:

1. enqueue descriptor reads/writes or descriptor-page allocation grows with existing queue depth;
2. real primary growth cannot accumulate multiple retired generations without draining earlier ones;
3. reclaim traverses descriptor history instead of touching only the FIFO head;
4. reclaim exceeds its explicit segment budget or appends new physical storage;
5. a generation is skipped, duplicated, or reordered while draining the queue;
6. a crash can expose a linked-but-uncommitted descriptor, a partially advanced head, or ambiguous queue state;
7. recovery scans the retirement queue, generation ranges, or radix map, or requires logical redo;
8. post-reclaim extent reuse bypasses the v0.32 fixed 32-page stale-payload scrub.

## Test

### Enqueue controls

| Queued generations | Flat manifest bytes | Naive tail-walk visits | Candidate descriptor pages | Candidate max preads | Candidate max pwrites |
|---:|---:|---:|---:|---:|---:|
| 1 | 70 | 1 | 2 | 2 | 2 |
| 4 | 205 | 4 | 2 | 2 | 2 |
| 16 | 758 | 16 | 2 | 2 | 2 |
| 64 | 3,014 | 64 | 2 | 2 | 2 |
| 256 | 12,351 | 256 | 2 | 2 | 2 |

The candidate performs zero retirement-history walks on enqueue. The first enqueue writes only the new descriptor. Subsequent enqueues read the committed tail's two copies, write the new descriptor, and rewrite one alternate tail copy.

### Real primary queue

Starting at capacity 32 with a migration budget of 512, 65 inserts force three completed migrations without running reclamation between them. The committed FIFO then contains retired generations:

```text
[0, 1, 2]
```

with materialized segment counts:

```text
[1, 1, 2]
```

Observed enqueue maxima across those real migrations are:

```text
retirement descriptor preads   <= 2
retirement descriptor pwrites  <= 2
new descriptor pages           = 2
history walks                   = 0
```

The first three migration transactions append 36, 70, and 138 total physical pages respectively, but only two pages in each transaction are retirement-descriptor storage. The larger totals are generation data/lifecycle/radix work and are not claimed as queue-enqueue constants.

### FIFO reclamation

With explicit segment budget `B=2`, the three retirement descriptors drain in generation order `0 -> 1 -> 2`. Every reclaim step:

```text
reclaimed segments             <= 2
head descriptor preads         <= 2
head descriptor pwrites        <= 1
retirement descriptors scanned  = 0
physical pages appended          = 0
generation pages scanned         = 0
radix nodes scanned              = 0
logical redo                     = 0
```

The real fixture happens to drain each head descriptor within its step, so its observed descriptor-pwrite maximum is zero. A separate partial-head fixture forces a descriptor cursor update and observes exactly one descriptor `pwrite`.

After the queue drains, the next real migration reuses four reclaimed extents. Reuse performs 128 scrub writes — exactly 32 data pages per reused extent — before publishing the new mappings. All 129 live keys remain visible.

### Process-crash matrices

The fixed experiment injects real `SIGKILL` at four distinct queue protocols:

| Protocol | Cases | Additional queue-specific failure point |
|---|---:|---|
| Enqueue into empty queue | 4 | new descriptor written |
| Enqueue into non-empty queue | 5 | new descriptor written; prior tail linked |
| Partial reclaim of queue head | 5 | head descriptor cursor/count updated |
| Reclaim that dequeues queue head | 5 | queue head advanced in pending metadata |

All **19/19** cases expose the exact expected committed pre/post state. In particular, killing after the old tail's alternate copy points at a newly appended descriptor still exposes the old committed queue because both writes carry the future epoch and the superblock has not committed it.

Every tested recovery reports:

```text
logical_work                    = 0
generation_pages_scanned        = 0
mapping_nodes_scanned           = 0
retirement_descriptors_scanned  = 0
```

and a second recovery performs zero additional physical truncation.

## Result

**The v0.34 candidate survives the fixed falsification.** Real primary growth can accumulate three simultaneously retired generations without waiting for earlier cleanup. Enqueue touches one new two-page descriptor and at most the committed tail; reclaim touches only the committed head descriptor plus the explicit segment budget. FIFO order, stale-payload scrubbing, exact process-crash visibility, and scan-free recovery survive the tested cases.

This is not evidence that total retirement metadata is bounded. The descriptor log is append-only and therefore grows with completed-generation history. The larger queue depths `{4,16,64,256}` are deterministic enqueue controls, not real primary runs across exponentially growing capacities.

## Evidence anchor

Initial successful focused gate:

```text
workflow: v0.34 experiment
run number: 5
run id: 34561502953
head: 716de117f3d39ce9e373a9b9f449716bf33af624
artifact id: 10184464408
artifact digest: sha256:82b489a6c22df4a5b43f4410af6e7b874a070c008255f4f646ea178c5bbbff17
retirement_queue_results.json sha256: 5441d47b882d4f16545e63c18c910885c509cf46aeb2a8b32860efd6d372a213
retirement_queue_results.json git blob sha1: afd2c277c3930c2cc6e29431872657e9db0c6b0b
```

The captured committed result ledger is byte-identical to that initial artifact.

## Deliberate non-claims

- Descriptor pages are not reclaimed or reused; total descriptor storage grows with completed generations.
- The real-primary experiment reaches queue depth three, not 256. Larger depths are arithmetic/control cases.
- Empty radix metadata nodes and filesystem blocks are not reclaimed.
- The fixed 64-bit logical-segment and physical-page pointer namespaces are not mathematical unboundedness.
- The result is single-writer and process-crash scoped. It does not establish hardware power-loss, torn-sector, drive-cache, filesystem-journal, arbitrary multi-writer, or distributed correctness.
- User-space `pread`/`pwrite` counts remain invocation counts, not device-I/O guarantees.

## Next falsification target

v0.35 should challenge **retirement-descriptor accumulation itself**. The immediate question is whether completed descriptor records can be safely recycled or compacted under a fixed foreground budget without introducing an ABA-style pointer hazard, queue-history scan, or crash resurrection.

A candidate should be rejected if descriptor reclamation requires walking all historical descriptors, if a recycled descriptor page can be mistaken for an older queue incarnation after crash, if queue roots can reference a descriptor whose identity has been reused ambiguously, or if reclamation moves an unbounded rewrite onto migration/reclaim foreground work.
