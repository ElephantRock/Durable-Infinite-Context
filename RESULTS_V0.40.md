# v0.40 — Bounded live physical queue-tail evacuation

## Observe

v0.39 can reclaim a directly addressed physical retirement-descriptor tail when that descriptor is already `FREE`. The next obstruction is a physical tail that remains live while a lower descriptor pair is reusable.

## Diagnose

The narrow first live-tail case makes the physical tail also the logical retirement-queue tail. Its queue successor is therefore null, so relocation needs direct authority for the live tail, its queue predecessor, a lower destination, and the committed arena frontier.

The first candidate exposed a topology boundary. Consuming a free-list head that still has a FREE successor also requires clearing that successor's predecessor link. v0.40 therefore narrows the claim-bearing path to a **sole committed FREE descriptor/current free-list head** and explicitly refuses the multiple-FREE case before descriptor I/O.

Review also identified two evidence issues. First, the new queue-tail-predecessor authority needed its own publication crash matrix, not only clean-path tests. Second, the generic insert trace carried a filesystem `allocated_bytes` observation that is not part of the semantic claim and can vary by filesystem. Both issues are now addressed.

## Derive

Maintain one tagged `retirement_queue_tail_predecessor` in authoritative superblock state whenever queue depth is at least two. Enqueue maintains it in bounded work because the old queue tail is exactly the predecessor of the newly appended tail. Head reclamation leaves it unchanged while depth remains at least two; at singleton depth it is semantically null, and the next enqueue overwrites it from the then-current sole tail.

For the sole-FREE geometry, relocation directly reads the live physical/queue tail, its named predecessor, and the sole lower free head; stages the tail payload into the destination under a fresh incarnation; rewrites the queue predecessor; durably publishes the new queue tail, empty free-list state, next-incarnation state, and shorter arena frontier; then truncates the old physical tail pair as derived cleanup.

## Hypothesis

A tagged authoritative queue-tail predecessor is sufficient to evacuate a live physical descriptor tail that is also the logical queue tail into a sole lower FREE pair with work independent of queue depth, while preserving tagged stale-reference rejection and scan-free recovery.

## Surviving bounded result

The discriminating workload creates descriptors `[0,2,4]`, then reclaims page `0` so the live queue is `[2,4]`, page `4` is both physical and logical queue tail, page `2` is its named predecessor, and page `0` is the sole lower FREE descriptor.

- unchanged v0.39 control: no release;
- v0.40 queue: `[2,4] -> [2,0]`;
- retirement arena: `6 -> 4` pages, releasing `8,192` bytes;
- relocation work: exactly `6 descriptor preads / 2 descriptor pwrites / 0 descriptor-history scans / 1 live relocation`;
- old live-tail identity `(page 4, incarnation 3)` is rejected;
- old FREE destination identity `(page 0, incarnation 1)` is rejected; the relocated live descriptor uses incarnation `4`;
- queue-depth scaling over live depths `2,3,4,5` preserves exactly `6R / 2W / 0 scans / 1 relocation`;
- a multiple-FREE fixture with live queue `[4,6]` and free chain `[2,0]` is refused with `0R / 0W / 0 scans / 0 relocations` and exact state preservation.

## Crash evidence

The v0.40-specific evidence covers 12 real process-crash cases: seven around live-tail relocation publication and five around publication of queue-tail-predecessor authority during enqueue.

The five predecessor-maintenance cases exercise new-descriptor staging, old-tail link staging, retirement-arena durability, primary-data durability, and authoritative commit. The clean `k-0032` transition moves queue depth `1 -> 2`, advances tail page `0 -> 2`, and publishes `(page 0, incarnation 1)` as the new tail predecessor. Every pre-commit case recovers the exact singleton state and removes exactly one uncommitted descriptor pair (`8,192` bytes) from the retirement arena. The committed case preserves the exact two-node post-state. Recovery performs zero generation/radix/retirement-descriptor scans and zero logical redo, and second recovery is physically idempotent.

The seven relocation cases likewise recover exact pre/post committed state, remain scan-free, and are idempotent on second recovery. At post-commit/pre-truncate points, recovery removes exactly `8,192` bytes of derived arena residue.

## Canonical evidence

Canonical raw `live_tail_evacuation_results.json` SHA-256:

`4a53dd4bfa336d040fd82d599f1050932e4b1746578fd3f15706650519443d18`

`verify_live_tail_evacuation_results.py` reruns and validates the v0.39 control, exact sole-FREE relocation topology/work, tagged identity rejection, multiple-FREE zero-I/O refusal, both crash matrices, scan-free/idempotent recovery, queue-depth scaling, the compact allocation-independent predecessor trace, canonical serialization, and exact hash reproduction. Dedicated v0.40 experiment/verifier workflows and full CI exercise this evidence.

## Review status

Three review findings were resolved before evidence closure: the destination claim was narrowed to the sole-FREE geometry; crash atomicity of the newly introduced predecessor authority was added to the evidence; and environment-sensitive filesystem allocation data was removed from the canonical predecessor trace. No remaining blocker has been identified in the bounded claim path.

PR #40 remains intentionally stacked behind v0.39 and does not bypass PR #39's independent review/merge gate.

## Deliberate boundary

v0.40 does not claim arbitrary live-tail relocation. The physical tail must also be the logical queue tail, and the destination must be the sole committed FREE descriptor/current free-list head. Multiple FREE descriptors require an additional successor-predecessor topology rewrite and are explicitly refused. A live physical tail in the middle of the queue requires additional inbound/outbound authority. File-length truncation is not a claim about filesystem allocated-block reclamation. Process-crash evidence is not a hardware power-loss or torn-write proof. Descriptor page/incarnation authority remains finite-width. The normalized SQLite design through v0.16 remains the production candidate; v0.40 remains an experimental storage-layer alternative.

## Next falsification target

The immediate remaining obstruction is the multiple-FREE live queue-tail case. Its missing bounded operation is already isolated: consuming the current free head as relocation destination must also rewrite the directly named new free head so its predecessor becomes null before authoritative publication.

A v0.41 candidate should test whether that additional direct successor-predecessor repair extends live queue-tail evacuation to an arbitrarily long current FREE chain while work remains independent of free-chain length and queue depth. A live physical tail in the middle of the retirement queue remains a later target.
