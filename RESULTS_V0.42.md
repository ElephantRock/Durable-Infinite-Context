# v0.42 — Bounded Interior Live Physical-Tail Evacuation

## Observe

v0.41 can evacuate a live physical retirement-descriptor tail only when that physical tail is also the logical retirement-queue tail. Once a lower FREE descriptor is reused for a later enqueue, the old physical tail can remain live while becoming an interior queue node. The v0.41 mechanism correctly refuses that geometry because logical-tail predecessor authority does not identify the inbound reference to the interior physical tail.

## Diagnose

A live interior descriptor has two queue relationships that matter to relocation: an inbound predecessor edge and an outbound successor edge. The descriptor itself already carries its tagged outbound successor. The missing fact is the tagged identity of the queue node whose `next` field names the physical tail.

## Derive

If the current physical tail and its queue predecessor are authoritative current-state metadata, relocation does not require a queue walk. The relocation can directly read:

1. physical tail `T`;
2. named predecessor `P`;
3. successor `S = T.next`;
4. lower FREE destination `D`.

It can then stage `T`'s live payload and outbound `S` edge into `D` under a fresh incarnation, rewrite `P.next` to the new `D` identity, durably publish all descriptor dependencies, atomically advance authoritative metadata to the shorter arena frontier, and truncate `T` as derived cleanup.

## Hypothesis

For the first interior geometry — exactly three live queue descriptors, `P` equal to the queue head, `S` equal to the queue tail, and one lower FREE destination — direct physical-tail predecessor authority is sufficient for bounded relocation independent of queue/history traversal.

## Predictions

The candidate survives only if:

- the unchanged v0.41 mechanism refuses the same interior-tail state without descriptor I/O;
- queue `[4,6,2]` with sole FREE page `0` becomes `[4,0,2]` with no FREE descriptors;
- the arena shrinks `8 -> 6` pages and releases exactly `8,192` bytes;
- relocation requires exactly `8 descriptor preads / 2 descriptor pwrites / 0 descriptor-history scans / 1 live relocation`;
- the logical tail remains page `2` while its predecessor authority changes `6 -> 0`;
- old physical-tail and old destination identities are rejected while the successor identity and relocated payload are preserved;
- crash recovery is exact, scan-free and second-recovery idempotent around both relocation publication and publication of the new physical-tail authority itself.

## Test

The discriminating fixture first allocates retirement descriptors `[0,2,4,6]`, then reclaims two queue heads to produce live queue `[4,6]` and FREE chain `[2,0]`. The exact next descriptor-producing insertion, `k-0256`, reuses FREE head page `2`, yielding live queue `[4,6,2]` and sole FREE page `0`. Page `6` is now the physical tail but not the logical queue tail; page `4` is its directly named predecessor and page `2` is its directly named successor/logical tail.

The relocation crash matrix uses seven real process-`SIGKILL` points:

- destination staged;
- predecessor rewrite staged;
- descriptor dependencies fsynced;
- authoritative commit;
- post-commit relocation marker;
- physical arena truncate;
- final arena fsync.

A second five-case real process-`SIGKILL` matrix attacks the FREE-reuse transaction that publishes the interior physical-tail authority:

- descriptor reused;
- prior logical-tail link staged;
- descriptor arena fsynced;
- primary data fsynced;
- authoritative commit.

## Result

The candidate survived the bounded falsification.

The unchanged v0.41 control sees queue `[4,6,2]`, detects that physical page `6` is not the logical tail at page `2`, performs `0R / 0W / 0 scans / 0 relocations`, and preserves exact state.

The v0.42 candidate relocates page `6` into FREE page `0` under fresh incarnation `6`, rewrites page `4` to point to `(0,6)`, preserves page `2` as the logical queue tail at incarnation `5`, and publishes page `4` as the new physical tail. The committed queue is `[4,0,2]`; the FREE list is empty; the descriptor arena shrinks from eight to six pages.

Observed claim-bearing work is exactly:

- `8` descriptor preads;
- `2` descriptor pwrites;
- `0` retirement-descriptor scans;
- `1` live descriptor relocation;
- `2` retirement-arena fsyncs;
- `8,192` descriptor-arena bytes released.

The stale old physical-tail identity `(page 6, incarnation 4)` and stale old FREE destination identity `(page 0, incarnation 1)` are rejected. The successor identity `(page 2, incarnation 5)` remains valid, and the relocated generation/cursor/remaining payload plus outbound successor identity exactly match the old physical-tail payload.

All seven relocation crash cases recover the exact expected committed state. The three pre-commit cases recover the pre-state without arena truncation. Crashes at authoritative commit and the post-commit marker expose an 8,192-byte physical arena residue and recovery removes exactly that pair. Crashes after physical truncation require no additional arena shrink. All cases require zero logical redo, zero generation/radix/retirement-descriptor scans, and second recovery is physically idempotent.

All five physical-tail-authority publication crash cases also recover exactly. The four pre-commit cases return to queue `[4,6]` with FREE chain `[2,0]` and physical-tail authority `(6 <- 4)`. The committed case preserves queue `[4,6,2]`, FREE `[0]`, and the same interior physical-tail authority. FREE reuse does not alter the descriptor-arena frontier; all five cases preserve the 32,768-byte committed arena length. The `data_synced` pre-commit case additionally demonstrates cleanup of uncommitted primary-file growth while leaving descriptor state exact.

## Evidence anchor

Canonical raw `middle_live_tail_evacuation_results.json` SHA-256:

`9a62e5a95978c2a680602539eb9be6312b51a9e46a23956e0ed5837eec5477e0`

`verify_middle_live_tail_evacuation_results.py` reruns the real experiment, first requires the repository's frozen result bytes to equal the reproduced canonical serialization, validates the exact v0.41 control boundary, exact relocation topology/work, tagged-identity and payload invariants, both real-process crash matrices, scan-free/idempotent recovery, exclusion of environment-sensitive `allocated_bytes`, and exact SHA-256 reproduction.

Final immutable-head workflow and artifact identifiers are recorded in PR #42 after the final review head is frozen.

## Non-claims

v0.42 does **not** establish arbitrary interior queue relocation. The tested physical tail has exactly one predecessor and one successor in a three-node live queue; the predecessor is the queue head, the successor is the queue tail, and the destination is the sole lower FREE descriptor. Later reclamation that changes the physical-tail predecessor, deeper successor chains, and composition with the v0.41 multiple-FREE destination repair are separate falsification targets.

File-length truncation is not a claim about filesystem allocated-block reclamation. Process-`SIGKILL` evidence is not hardware power-loss or torn-write proof. Multi-writer concurrency is not tested. Descriptor page/incarnation authority remains finite-width.

The normalized SQLite design earned through v0.16 remains the production candidate. v0.42 remains an experimental storage-layer alternative.

## Next target

The immediate next obstruction is maintaining correct physical-tail predecessor authority when the predecessor is no longer the queue head — for example after queue-head reclamation or with a longer live prefix before the physical tail. A v0.43 candidate should test whether bounded maintenance of that reverse edge can survive those topology changes without queue traversal, before broadening to deeper successor chains or composing multiple-FREE destination repair.