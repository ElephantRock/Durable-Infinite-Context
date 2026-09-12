# v0.37 — Segregated reclaimable retirement-descriptor arena

## Observe

v0.36 falsified head-only tail truncation for retirement descriptors stored inside the main append-local file. After real retirement work drained, every observed free descriptor pair remained buried below unrelated committed suffix pages. Logical free-list membership was therefore insufficient for physical file-length release.

## Diagnose

The remaining problem is placement. A descriptor can be safe to reuse yet impossible to truncate if it shares an append stream with unrelated durable objects. Moving the descriptor records into an independently truncatable placement domain removes that structural conflict, but introduces two new correctness obligations:

1. descriptor writes in the sidecar must become durable before the primary superblock can publish references to them;
2. truncating and later reusing sidecar page addresses must not make stale `(page, incarnation)` references valid again.

A whole-arena reset has one especially useful boundary: when the committed retirement queue becomes empty, no descriptor identity remains live. At that point the authoritative metadata can forget the complete arena in O(1) work without walking the free chain.

## Derive

The v0.37 candidate uses a sidecar descriptor arena while keeping the primary superblock authoritative for:

- retirement queue head/tail identities;
- descriptor free-list head identity and count;
- committed descriptor-arena length;
- a monotonic `retirement_descriptor_next_incarnation` counter that is stored outside the truncatable arena.

Publication ordering is asymmetric by design:

- **enqueue / partial descriptor update / non-final dequeue:** write and `fsync` the descriptor arena first, then publish through the primary superblock;
- **final dequeue to an empty retirement queue:** publish queue/free-list empty and committed arena length zero in the primary superblock first, then truncate the sidecar and `fsync` it.

The latter ordering means a crash after publication but before physical truncation leaves only derived residue. Recovery reads the committed arena length from the primary superblock and truncates excess sidecar bytes without scanning descriptor history.

Diagnostic queue/free-chain snapshots are allowed to traverse descriptors, but that traversal is measurement only and is not candidate foreground or recovery work.

## Hypothesis

A segregated sidecar arena whose committed length and monotonic descriptor-incarnation source are rooted in the primary superblock can return all tested descriptor file length when the retirement queue drains, with O(1) arena-reset metadata, no descriptor-history scan, no descriptor relocation, stale-reference rejection after address reuse, and restart-convergent cleanup after process crashes.

## Predictions

The candidate survives the bounded test only if all of the following hold:

1. a real three-descriptor peak occupies six sidecar pages and returns to zero committed/file-length arena pages after full drain;
2. repeated independent growth/drain cycles return the arena to zero rather than retaining completed-history capacity;
3. sidecar page zero can be reused after truncation only under a strictly newer incarnation, and the stale pre-reset identity is rejected;
4. fresh enqueue, non-empty tail linking, partial reclaim, and final whole-arena reset survive the stated SIGKILL matrix with exact committed state;
5. recovery performs zero generation-page scans, zero radix-node scans, zero retirement-descriptor scans, and zero logical redo;
6. a crash after authoritative arena reset but before sidecar truncation is repaired from the committed zero-length frontier without descriptor traversal.

## Test

The experiment preserves the v0.16 semantic guard and runs on the real storage stack with initial capacity 32, `max_load=0.50`, and bounded reclamation budget `B=2`.

### Real peak release

With 65 inserted keys, the real store reaches a peak of three descriptor pairs:

```text
peak descriptor pairs = 3
peak arena pages       = 6
peak arena bytes       = 24,576
```

After bounded reclamation drains the retirement queue:

```text
queue count                  = 0
descriptor pool count        = 0
committed descriptor pages   = 0
sidecar file bytes           = 0
released arena pages         = 6
released arena bytes         = 24,576
candidate history walks      = 0
candidate relocations        = 0
```

Each reclaim step remains within `B=2` and reports zero retirement-descriptor scans.

### Address reuse / stale identity

The first descriptor occupies sidecar page `0` at incarnation `1`. After complete drain and sidecar truncation, later growth reuses sidecar page `0` at incarnation `2` (triggering at key index `32`).

```text
old identity = (0, 1)
new identity = (0, 2)
```

The stale `(0,1)` identity is rejected, while `(0,2)` resolves the current descriptor. The incarnation source survives arena truncation because it is rooted in the primary superblock rather than the sidecar.

### Completed-history control

The serial growth/drain control crosses five retirement cycles at trigger indices:

```text
16, 32, 64, 128, 256
```

Each cycle allocates one two-page descriptor pair and returns both the committed arena length and physical sidecar file length to zero after drain. The result therefore does not retain descriptor file length as completed-generation history grows in this serial-drain control.

### Process-crash matrix

The v0.37 SIGKILL matrix covers **26/26 exact cases**:

| Case | Failpoints |
|---|---:|
| fresh empty-queue enqueue | 5 |
| fresh non-empty enqueue / tail link | 6 |
| partial head reclaim | 6 |
| final whole-arena reset | 9 |

All cases reproduce the expected pre-commit or post-commit semantic state. Recovery is generation-scan-free, radix-scan-free, retirement-descriptor-scan-free, redo-free, and physically idempotent on a second pass.

The final-reset matrix directly exercises the critical publication ordering. At failpoints `committed` and `retirement_arena_reset_committed`, the primary superblock already commits an arena target of zero while the sidecar still contains the full **24,576-byte** six-page peak. The first recovery truncates exactly **24,576 bytes** with zero descriptor scans. At `retirement_arena_truncated` and `retirement_arena_reset_synced`, the sidecar is already zero length and recovery performs zero additional truncation.

## Result

**The v0.37 segregated-arena candidate survives the stated bounded falsification.** In the tested real three-pair peak, descriptor placement is no longer blocked by unrelated committed suffix pages: full queue drain publishes zero committed descriptor capacity and returns the sidecar file length from 24,576 bytes to zero. Repeated serial growth/drain cycles also return to zero. Page-zero reuse is protected by a superblock-persisted monotonic incarnation, and the 26-case process-crash matrix preserves exact committed state with scan-free restart convergence.

This is a positive placement result, but its scope is narrower than “bounded shrink at all times.” v0.37 releases the whole descriptor arena only when the committed retirement queue becomes empty. While any descriptor remains queued, excess free descriptor pairs are retained inside the sidecar.

## Evidence anchor

The first canonical result artifact was produced by the focused evidence workflow on:

```text
workflow: v0.37 candidate-result evidence
run number: 1
run id: 34710742870
head: e4c0bb8a88e7fe0c00392bbea5b7fb619ba75e86
artifact id: 10302992453
artifact digest: sha256:e132ee91f5e01847e8754ab87e30815f3020d6f97de95cc2eb6bc7ab6a49cbdb
segregated_retirement_descriptor_results.json sha256: 15b5beb9b9065e4231ab7455066729bb0ba431ea8689d25049c120e7a2ad6135
```

`verify_segregated_retirement_descriptor_results.py` freezes that canonical **raw-result SHA-256**, reruns the real experiment, validates the claim-bearing peak, identity, history-cycle, and crash-matrix values, requires canonical byte serialization, and requires exact hash reproduction.

The first frozen verifier pass was:

```text
workflow: v0.37 canonical-result verifier
run number: 3
run id: 34710842243
head: 1a1befe15e03027327ff5eae16b29b93907e502b
artifact id: 10303251889
artifact digest: sha256:4996535aa5cdd532cd0b5d472d67110f8e0f055e2274b2fc490499150a6a66b5
result verifier: success
```

## Deliberate non-claims

- v0.37 does not release excess descriptor-arena capacity while the retirement queue remains non-empty.
- It establishes sidecar **file-length truncation**, not filesystem allocated-block deallocation, hole punching, or device-level discard.
- It does not establish hardware power-loss, torn-sector, controller-cache, or multi-writer correctness; the crash evidence is process `SIGKILL` under the experiment's persistence model.
- The descriptor incarnation source remains a finite uint64 namespace.
- The sidecar adds an additional durable file and ordering boundary; v0.37 does not claim that this is the optimal production layout.
- Diagnostic descriptor-chain snapshots can traverse the queue/free chains; those traversals are measurement only and are excluded from foreground/recovery locality claims.
- Zero descriptor-history scans during whole-arena reset do not prove a future partial-shrink mechanism can remain scan-free.
- The normalized SQLite design earned through v0.16 remains the production candidate; v0.37 remains an experimental storage-layer alternative.

## Next falsification target

v0.37 solves the zero-live-descriptor boundary but retains capacity whenever the queue remains non-empty. The next direct question is therefore **bounded partial arena shrink under a live retirement backlog**:

Can tail-free descriptor capacity be returned while at least one descriptor remains queued, without traversing the retained pool, relocating live descriptors, weakening `(page, incarnation)` identity, or making crash recovery proportional to arena size?

A candidate must distinguish reusable interior capacity from releasable physical suffix capacity and must preserve the authoritative-publication / derived-cleanup ordering earned here.
