# v0.38 — Bounded partial retirement-descriptor tail shrink

## Observe

v0.37 moved retirement descriptors into an independently truncatable sidecar and proved whole-arena release at the zero-live-descriptor boundary. It still retained all descriptor-arena capacity while any retirement descriptor remained queued.

The next question is narrower than general compaction: can a physically releasable descriptor pair at the sidecar suffix be returned while the retirement backlog remains live, without searching descriptor history or moving live records?

## Diagnose

A free descriptor is not automatically releasable. Physical suffix release needs two facts at the same time:

1. the descriptor pair is logically free;
2. the descriptor pair is exactly the current physical arena tail.

With the v0.37 singly linked free list, finding an arbitrary tail-free descriptor could require traversal proportional to free-list history. That would violate the locality target. The narrowest scan-free candidate therefore acts only when the authoritative free-list head is already the physical arena tail.

## Derive

The v0.38 candidate preserves the v0.37 sidecar, external incarnation authority, and publication ordering. It adds one explicit maintenance operation:

```text
shrink_retirement_arena_tail_step()
```

The operation reads only current superblock metadata first. If the free-list head does not equal the highest allocated descriptor pair, it returns a no-op without reading descriptor storage. If they are aligned, it:

1. reads that one dual-copy free descriptor;
2. obtains its next free-list identity;
3. validates that committed live queue endpoints remain below the retained frontier;
4. publishes the advanced free-list head/count and an arena frontier shorter by exactly one descriptor pair through the primary superblock;
5. truncates the sidecar to the newly committed frontier as derived physical cleanup.

The operation does not traverse the free list, rewrite descriptor records, or relocate live descriptors.

A descriptor reference is also required to lie within the committed arena frontier. This matters in the crash window after the shorter frontier is committed but before the sidecar is physically truncated: bytes beyond the committed frontier are residue, not committed descriptor state.

## Hypothesis

When the committed descriptor free-list head is also the physical arena tail, one bounded maintenance transaction can release exactly that descriptor pair while at least one retirement descriptor remains queued, with one direct descriptor read, zero descriptor-history scans, zero live-descriptor relocation, stale-reference rejection after truncation/reuse, and scan-free crash recovery.

## Predictions

The candidate survives the bounded test only if all of the following hold:

1. an aligned live-backlog fixture can be produced from real insertion/reclamation behavior rather than hand-written metadata;
2. the v0.37 control retains the six-page sidecar in that fixture;
3. v0.38 reduces the committed and physical sidecar length from six pages to four pages while the queue remains non-empty;
4. the candidate reads exactly one dual-copy free descriptor, performs zero descriptor-history scans, zero descriptor rewrites, and zero live-record relocations;
5. a non-aligned free-list head produces a zero-read no-op rather than searching for the physical tail;
6. the truncated tail address can later be reused only under a newer global incarnation and the stale pre-shrink identity is rejected both immediately after shrink and after reuse;
7. SIGKILL before authoritative publication preserves the pre-shrink state, while SIGKILL after publication but before physical truncation exposes the post-shrink committed state and recovery removes exactly the derived tail residue without scans.

## Test

The experiment preserves the v0.16 semantic guard and uses the real v0.37 storage lineage with initial capacity 32, `max_load=0.50`, migration budget 4096, and retirement reclamation budget `B=2`.

### Real aligned live-backlog fixture

The workload first reaches three descriptor pairs at pages `0, 2, 4`. Bounded reclamation leaves page `4` live while pages `0` and `2` enter the free list. Later retirement work reuses those free pairs, producing queued order `[4, 2, 0]`. Reclaiming the former page-4 head then yields the target state:

```text
retirement queue count       = 2
free descriptor count        = 1
free-list head page           = 4
committed arena pages         = 6
physical arena bytes          = 24,576
```

The physical tail pair is pages `4–5`, so the free-list head is directly aligned with the arena tail.

### v0.37 control

The unchanged v0.37 store retains the aligned tail pair because v0.37 only resets the sidecar when the complete queue drains:

```text
queue count          = 2
retained arena pages = 6
retained arena bytes = 24,576
```

### v0.38 bounded partial release

One v0.38 maintenance step produces:

```text
queue count before             = 2
queue count after              = 2
committed arena pages before   = 6
committed arena pages after    = 4
physical arena bytes before    = 24,576
physical arena bytes after     = 16,384
released arena pages           = 2
released arena bytes           = 8,192
retirement descriptor preads   = 2
retirement descriptor pwrites  = 0
retirement descriptors scanned = 0
candidate relocations          = 0
```

The two preads are the two physical copies of one directly addressed free descriptor. No retained descriptor chain is traversed.

### Non-aligned control

A second real fixture has one free descriptor at page `0` while the physical arena tail is page `4`. The maintenance step returns without release and without reading descriptor storage:

```text
released                       = false
retirement descriptor preads   = 0
retirement descriptors scanned = 0
candidate relocations          = 0
```

This is an important negative boundary: v0.38 does not search the singly linked free list for a buried tail-free descriptor.

### Partial-shrink address reuse / stale identity

The released tail identity is:

```text
old identity = (page 4, incarnation 3)
```

Immediately after authoritative shrink, that reference is rejected because page `4` lies outside the committed four-page arena frontier, even if physical tail residue has not yet been truncated.

Later growth re-extends the arena and reuses physical page `4` at incarnation `6`:

```text
new identity = (page 4, incarnation 6)
reuse trigger key index = 512
```

The stale `(4,3)` identity remains rejected and the current `(4,6)` identity resolves generation `5`. The global incarnation authority remains outside the truncatable sidecar.

### Process-crash matrix

The shrink-specific SIGKILL matrix contains **5/5 exact cases**:

```text
retirement_tail_release_staged
committed
retirement_tail_release_committed
retirement_arena_truncated
retirement_tail_release_synced
```

At `retirement_tail_release_staged`, no authoritative publication has occurred. Recovery retains the six-page / 24,576-byte arena and truncates nothing.

At `committed` and `retirement_tail_release_committed`, the primary superblock already commits a four-page / 16,384-byte arena while the sidecar still physically contains six pages / 24,576 bytes. The 8,192-byte suffix is therefore uncommitted residue. Descriptor lookup rejects the old tail identity from the committed frontier alone, and first recovery truncates exactly 8,192 bytes with:

```text
logical redo                    = 0
generation pages scanned        = 0
radix nodes scanned             = 0
retirement descriptors scanned  = 0
```

At `retirement_arena_truncated` and `retirement_tail_release_synced`, physical length is already 16,384 bytes and recovery performs zero additional arena truncation. A second recovery is physically idempotent in every case.

## Result

**The v0.38 candidate survives the stated bounded falsification.** In the tested aligned case, one directly reachable free tail descriptor pair is returned while two retirement descriptors remain queued. The v0.37 control retains 24,576 bytes; v0.38 publishes and physically converges to 16,384 bytes, releasing exactly 8,192 bytes with one dual-copy descriptor read, zero descriptor-history scans, zero descriptor rewrites, and zero live-descriptor relocation.

The result is deliberately narrower than arbitrary partial compaction. It proves a bounded direct-tail case only. If a physically free tail descriptor is buried inside the singly linked free list, v0.38 performs a no-op rather than searching for it.

## Evidence anchor

The first exact-head candidate evidence passed on:

```text
workflow: CI
run number: 405
run id: 34759245524
head: 8e5b48eb832679379ac78f03ef90812ce16e14a9
artifact id: 10318627520
artifact digest: sha256:d1dc881c9b7ac535edf3b7477ae08cad2238dc7847a9973f0feacc72edf8eab9
partial_tail_shrink_results.json sha256: 853f66bc3af81b0a1ba071d342b953795fabf1d0c415863da319f2a030dc7de0
```

That CI run passed the complete unit suite, the v0.38 experiment, and the historical verifier chain. On the same candidate head, the v0.36 canonical-result verifier run `#23` and v0.37 canonical-result verifier run `#13` also passed.

`verify_partial_tail_shrink_results.py` freezes the canonical raw-result SHA-256, reruns the real experiment, validates the claim-bearing control/release/no-op/identity/crash values, requires canonical byte serialization, and requires exact hash reproduction. A dedicated `v0.38 canonical-result verifier` workflow runs that gate on pull requests and on `main`.

## Deliberate non-claims

- v0.38 does not discover or unlink an arbitrary free physical tail descriptor that is buried inside the singly linked free list.
- It releases at most one two-page descriptor pair per tested maintenance step.
- It establishes sidecar **file-length truncation**, not filesystem allocated-block deallocation, hole punching, discard, or device-level reclamation.
- It does not establish hardware power-loss, torn-sector, controller-cache, or arbitrary multi-writer correctness; the crash evidence is process `SIGKILL` under the experiment's persistence model.
- The descriptor incarnation source remains a finite uint64 namespace.
- Diagnostic descriptor-chain snapshots can traverse queue/free chains; those traversals are measurement only and are excluded from candidate foreground/recovery locality claims.
- The tested direct-tail condition does not prove a generalized allocator can maintain the required alignment under arbitrary workload history.
- The normalized SQLite design earned through v0.16 remains the production candidate; v0.38 remains an experimental storage-layer alternative.

## Next falsification target

v0.38 shows that partial shrink is bounded when the exact removable descriptor is already the authoritative free-list head. The next unresolved case is **arbitrary free physical tail under a non-head free-list position**:

Can the system identify and unlink a physically free arena-tail descriptor in bounded work when that descriptor is not the free-list head, without free-list traversal, live-descriptor relocation, stale-identity weakening, or recovery work proportional to arena history?

A positive answer likely requires stronger current-state topology than a singly linked free list. A design that finds the tail by scanning retained free descriptors would falsify the intended locality claim rather than satisfy it.
