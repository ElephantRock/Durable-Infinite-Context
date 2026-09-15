# v0.39 — Bounded non-head free-tail unlink with bidirectional current topology

## Observe

v0.38 established bounded partial descriptor-arena shrink only when the free-list head was itself the physical descriptor tail. Its deliberate negative boundary was the buried-tail case: if the highest descriptor pair was free but sat behind another free-list node, v0.38 performed a zero-read no-op rather than searching the singly linked free list.

That boundary is now the falsification target. The question is not whether an arbitrary historical descriptor can be found cheaply. The physical tail is already known arithmetically from the committed arena frontier. The missing fact is the predecessor of that tail in the **current free topology**.

## Diagnose

A singly linked free list provides only successor authority. Given a directly addressed free tail descriptor, removing it from a non-head position requires either:

1. walking from the free-list head until the predecessor is found; or
2. carrying direct predecessor authority as current-state metadata.

The first mechanism makes shrink work depend on free-chain length and therefore fails the locality target. The second can remain bounded if predecessor maintenance itself touches only the immediately affected neighbors on free-list push and head reuse.

A second constraint comes from compatibility. v0.35-v0.38 descriptor bytes and decoded result shapes are already frozen evidence. Any stronger topology must therefore preserve the historical null encoding for old FREE records rather than changing magic, width, or canonical output where no predecessor exists.

## Derive

v0.39 keeps the existing descriptor width, magic, successor link, segregated sidecar, external incarnation authority, and committed-frontier recovery rule. While a descriptor is `FREE`, fields that are otherwise unused are interpreted as one tagged predecessor identity:

```text
FREE descriptor

successor = (next_page, next_incarnation)      # existing authority
predecessor = (prev_page, prev_incarnation)    # v0.39 authority
```

A null predecessor preserves the exact historical encoding and decoded JSON shape. Therefore the free-list head remains byte-compatible with earlier evidence.

The topology maintenance rules are local:

```text
free-list push during reclaim:
    old head.prev <- newly freed descriptor
    new descriptor.prev <- null
    new descriptor.next <- old head

free-list head reuse:
    new head.prev <- null
```

A shrink step directly addresses the physical tail pair from the committed arena frontier. If the tail is not free, it returns a no-op. If it is free, the descriptor already contains the identities of its current predecessor and successor. The step rewrites at most those adjacent FREE descriptors, makes those dependency writes durable, publishes the shorter authoritative arena frontier and updated free count/head through the primary superblock, then truncates the sidecar as derived physical cleanup.

## Hypothesis

A doubly linked **current free topology** is sufficient to unlink a directly addressed free physical tail from a non-head free-list position with work independent of free-chain length, while preserving tagged identity, exact process-crash publication, scan-free recovery, and byte compatibility with v0.35-v0.38 canonical evidence.

## Predictions

The candidate survives the bounded falsification only if all of the following hold:

1. a real workload produces a free chain `[2 -> 4]` where page `4` is the physical arena tail but page `2` is the free-list head;
2. unchanged v0.38 performs a zero-read no-op on that same fixture;
3. v0.39 releases exactly one descriptor pair, shrinking the arena from six pages to four, with exactly four descriptor preads, one descriptor pwrite, zero descriptor-history scans, and zero live-descriptor relocations;
4. free-list push publishes the predecessor of the old head with bounded local work, and free-head reuse clears the predecessor of the new head;
5. stale `(page, incarnation)` identity for the truncated tail is rejected immediately after shrink and after later reuse of the same physical page;
6. six shrink `SIGKILL` points recover to exact pre- or post-commit state with scan-free, redo-free, physically idempotent recovery;
7. four predecessor-push and four free-head-reuse `SIGKILL` points also recover exactly;
8. as real free-chain length grows from `2` to `5`, shrink work remains exactly `4 preads / 1 pwrite / 0 scans / 0 relocations`;
9. v0.35-v0.38 frozen canonical evidence remains byte-stable under the shared descriptor-code change.

## Test

The experiment preserves the v0.16 semantic guard and uses the real v0.38 storage lineage with initial capacity `32`, `max_load=0.50`, migration budget `4096`, and retirement reclaim budget `B=2`.

### Buried physical-tail fixture

The workload first creates three retirement descriptor pairs at pages `0`, `2`, and `4`. Reclamation and later queue growth reuse the lower free descriptors, producing queued order `[4, 2, 0]`. Reclaiming page `4` first makes it the free-list head; reclaiming page `2` then pushes page `2` ahead of it.

The final target state is:

```text
retirement queue count       = 1
free descriptor count        = 2
free-list order              = [2, 4]
free-list head page          = 2
physical tail page           = 4
committed arena pages        = 6
physical arena bytes         = 24,576
```

For v0.39, the page-4 FREE descriptor carries predecessor authority pointing directly to page `2` at incarnation `4`.

### v0.38 control

The unchanged v0.38 control sees that the free-list head is page `2`, not the physical tail page `4`, and returns without descriptor reads:

```text
released                       = false
retirement descriptor preads   = 0
retirement descriptors scanned = 0
retained arena pages            = 6
retained arena bytes            = 24,576
```

This confirms that the positive v0.39 result is not inherited from the previous head-only mechanism.

### v0.39 non-head free-tail release

One v0.39 maintenance step produces:

```text
queue count before              = 1
queue count after               = 1
free count before               = 2
free count after                = 1
free-list head before           = 2
free-list head after            = 2
committed arena pages before    = 6
committed arena pages after     = 4
physical arena bytes before     = 24,576
physical arena bytes after      = 16,384
released arena pages            = 2
released arena bytes            = 8,192
retirement descriptor preads    = 4
retirement descriptor pwrites   = 1
retirement descriptors scanned  = 0
candidate relocations           = 0
```

The four preads are two dual-copy reads: one for the physical tail and one for its directly named predecessor. The one pwrite updates the predecessor's successor to bypass the removed tail. Because the physical tail has no successor, no second neighbor rewrite is required in this fixture.

### Topology-maintenance cost

When reclaim pushes a newly freed descriptor in front of an existing free head, v0.39 performs one dual-copy read and one rewrite of the old head to publish its predecessor:

```text
free_predecessor_preads   = 2
free_predecessor_pwrites  = 1
retirement scans          = 0
```

When the free-list head is reused for a new retirement descriptor, the new head's predecessor is cleared before authoritative publication. In the real fixture this transition advances the free head from page `2` to page `4` at insertion key `k-0512`.

### Tagged identity after shrink and reuse

The released tail identity is:

```text
old identity = (page 4, incarnation 3)
```

After authoritative shrink, that identity is rejected from the committed arena frontier even before any stale physical residue is cleaned up. Later growth re-extends the arena and uses page `4` under a newer incarnation:

```text
new identity = (page 4, incarnation 7)
reuse trigger key index = 1024
current descriptor generation = 6
```

The stale `(4,3)` identity remains rejected after reuse.

### Shrink process-crash matrix

The shrink-specific matrix contains **6/6 exact `SIGKILL` cases**:

```text
retirement_tail_unlink_staged
retirement_tail_unlink_dependencies_synced
committed
retirement_tail_unlink_committed
retirement_arena_truncated
retirement_tail_unlink_synced
```

At the first two failpoints, neighbor changes are uncommitted and recovery exposes the pre-shrink six-page state. At `committed` and `retirement_tail_unlink_committed`, the primary superblock already authorizes a four-page arena while six pages remain physically; first recovery removes exactly `8,192` bytes of derived residue. At the post-truncate failpoints the physical arena is already four pages and recovery performs no additional truncation.

Every case has:

```text
logical redo                    = 0
generation pages scanned        = 0
radix nodes scanned             = 0
retirement descriptors scanned  = 0
```

A second recovery is physically idempotent.

### Topology-maintenance process-crash matrix

The new predecessor topology itself is covered by **8 additional exact `SIGKILL` cases**.

Free-list push:

```text
retirement_descriptor_freed
retirement_arena_synced
dependencies_synced
committed
```

Free-head reuse:

```text
retirement_descriptor_reused
retirement_arena_synced
data_synced
committed
```

In each submatrix, pre-commit failpoints recover to the old topology and `committed` recovers to the new topology. Recovery remains scan-free and the second recovery remains idempotent.

### Real locality scaling

The scaling experiment constructs real free chains of lengths `2`, `3`, `4`, and `5`, with total descriptor counts `3`, `4`, `5`, and `6`. The physical tail and its predecessor move outward with arena growth:

```text
free chain   tail page   predecessor   arena pages   shrink work
2            4           2             6 -> 4        4R / 1W / 0 scans
3            6           4             8 -> 6        4R / 1W / 0 scans
4            8           6             10 -> 8       4R / 1W / 0 scans
5            10          8             12 -> 10      4R / 1W / 0 scans
```

The setup workload grows substantially to produce the larger real chains, but the measured shrink path itself remains unchanged.

## Result

**The v0.39 candidate survives the stated bounded falsification.** A free physical tail that is not the free-list head can be detached with bounded current-topology work once the descriptor carries direct tagged predecessor authority. In the tested non-head case, v0.38 retains all six pages while v0.39 releases exactly one two-page descriptor pair with `4` descriptor preads, `1` descriptor pwrite, `0` free-list/history scans, and `0` live relocation.

The key architectural result is narrower than a general doubly linked allocator: the experiment establishes that direct predecessor authority removes free-chain-length dependence from **this directly addressed tail-unlink operation**. It also establishes the bounded local maintenance needed to keep that current topology valid on the tested push and head-reuse transitions.

## Evidence anchor

The first complete exact-head evidence on candidate head:

```text
head: 5205d2b1f737fed497225f625dd4403c255a56a0
full CI: #426 / run 34915305130
full CI v0.39 artifact id: 10376143113
full CI v0.39 artifact digest: sha256:53b111e336e6ef328be6e209252efb0f568cd2f5afc1afd3e17bd646a851f176
v0.39 result-verifier run: #2 / run 34915305122
v0.39 result-verifier artifact id: 10376305670
v0.39 result-verifier artifact digest: sha256:28662fe8c7720422f6bde257d5f1af4b165c32f1564c3ccb2aab8f496b592b76
bidirectional_tail_unlink_results.json sha256: 224c39ef61415e5f59eecc42741ff937bd9c7b783ae68ae0e35bcdc99368838e
```

On that same head, the inherited v0.36, v0.37, and v0.38 canonical-result workflows also passed, preserving backward canonical evidence after the shared descriptor-code change.

`verify_bidirectional_tail_unlink_results.py` freezes the raw-result SHA-256, reruns the real experiment, validates the claim-bearing control/release/topology/identity/crash/scaling values, requires canonical byte serialization, and requires exact hash reproduction. The dedicated `v0.39 canonical-result verifier` workflow runs that gate on pull requests and on `main`.

## Deliberate non-claims

- v0.39 does not search arbitrary descriptor history. Its direct unlink depends on the target being the known physical tail and on that FREE descriptor carrying current predecessor authority.
- The experiment establishes current free-topology locality, not zero metadata maintenance: reclaim/reuse now perform bounded predecessor-link work.
- The tested shrink releases one physical descriptor pair per maintenance step. Total release of a longer free suffix requires repeated steps.
- v0.39 does not relocate a live physical-tail descriptor. If a live descriptor occupies the arena tail, this maintenance operation cannot shrink past it.
- File-length truncation is not a claim about filesystem allocated-block deallocation, discard, or device-level reclamation.
- Process `SIGKILL` evidence is not proof against hardware power loss, torn sectors, controller-cache loss, or arbitrary multi-writer execution.
- Descriptor page and incarnation identities remain finite-width domains.
- Diagnostic queue/free-list snapshots may traverse current chains; those traversals are evidence collection and are excluded from candidate foreground/recovery locality claims.
- The normalized SQLite design earned through v0.16 remains the production candidate. v0.39 remains an experimental storage-layer alternative.

## Next falsification target

v0.39 removes the singly linked free-list search barrier for a **free** physical tail. The remaining physical-release obstruction is a different mechanism: a **live** descriptor can occupy the physical tail while lower descriptor pairs are free, preventing truncation even though sufficient internal free capacity exists.

The next useful falsification target is therefore bounded live-tail evacuation:

Can a live physical-tail descriptor be moved into a lower free descriptor pair, with all queue/free references and tagged identity updated atomically, using bounded current-topology work and scan-free recovery, so that the arena can shrink below its historical high-water mark without a descriptor-history walk?

A candidate that finds the live descriptor's inbound reference by scanning queue history, weakens incarnation identity, or makes crash recovery proportional to arena size would fail the intended locality contract.
