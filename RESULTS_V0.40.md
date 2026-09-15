# v0.40 — Bounded live physical queue-tail evacuation

## Observe

v0.39 removes the predecessor-discovery barrier for a physical descriptor tail that is already `FREE`. The next obstruction is a physical tail descriptor that remains live. File-length truncation cannot cross that pair even when a lower descriptor pair is free.

## Diagnose

For the first live-tail case, constrain the target so the physical tail is also the logical retirement-queue tail and the lower relocation destination is the **sole committed FREE descriptor**. The live tail has no queue successor. Relocation therefore requires only four pieces of current authority: the live tail identity, its queue predecessor, the sole lower free destination, and the committed arena frontier.

The live tail identity and arena frontier already exist in the superblock. v0.39 already provides a directly usable free-list head. The missing fact is the queue-tail predecessor. Discovering it by walking the queue would make maintenance depend on queue depth.

A review of the first candidate exposed an important boundary: if the free-list head has a successor, consuming that head also requires clearing the successor's predecessor link. Leaving that edge untouched would corrupt the bidirectional FREE topology. v0.40 therefore refuses relocation whenever `retirement_descriptor_free_count != 1`; that broader free-topology rewrite is deliberately deferred rather than hidden inside the claim.

## Derive

Maintain one tagged `retirement_queue_tail_predecessor` in the authoritative superblock whenever queue depth is at least two. Enqueue can maintain this in bounded work because the old queue tail is exactly the predecessor of the new tail. Head reclamation does not change the tail predecessor while queue depth remains at least two. When the queue becomes a singleton the predecessor is semantically null; the next enqueue overwrites the authority from the then-current sole tail.

For the sole-free geometry, a relocation step can then:

1. directly read the live physical queue tail;
2. directly read its superblock-named queue predecessor;
3. directly read the sole lower free-list head as the destination;
4. stage the tail payload into that destination under a fresh incarnation;
5. rewrite the queue predecessor to point to the new destination identity;
6. publish the new queue tail, empty free-list metadata, incarnation frontier, and shorter arena frontier atomically through the primary superblock;
7. truncate the old physical tail pair as derived cleanup.

## Hypothesis

A superblock-owned tagged queue-tail predecessor is sufficient to evacuate a live physical descriptor tail that is also the logical queue tail into a **sole lower FREE pair** with work independent of queue depth, while preserving stale-reference rejection and scan-free restart recovery.

## Predictions

The candidate survives only if:

1. a real workload creates queued descriptor pages `[0,2,4]`, then reclaims page `0` so the live queue is `[2,4]`, the sole free head is page `0`, and page `4` is both physical and logical queue tail;
2. unchanged v0.39 cannot shrink this state because the physical tail is `QUEUED`;
3. v0.40 relocates page `4` into page `0`, rewrites page `2` to point to the new page-0 identity, preserves queue depth, consumes the sole free descriptor, and shrinks the arena from six pages to four;
4. measured relocation work is exactly three dual-copy reads and two descriptor rewrites (`6 preads / 2 pwrites / 0 descriptor-history scans`) in the discriminating fixture;
5. the old physical-tail identity and the old FREE identity of the destination are both rejected after commit;
6. real process-`SIGKILL` cases around destination staging, predecessor staging, dependency fsync, authoritative commit, truncate, and final sidecar fsync recover to exact pre/post state with zero descriptor scans and physically idempotent second recovery;
7. as live queue depth grows while the same sole-free geometry is preserved, measured relocation work remains `6R / 2W / 0 scans`;
8. a fixture with multiple FREE descriptors is rejected before descriptor I/O and leaves committed topology unchanged;
9. v0.35-v0.39 canonical evidence remains unchanged.

## Deliberate boundary

v0.40 does **not** claim arbitrary live-tail relocation. The physical tail must be the logical queue tail, and the relocation destination must be the sole committed FREE descriptor/current free-list head. Multiple FREE descriptors require an additional successor-predecessor topology rewrite and are explicitly refused. A live physical tail in the middle of the queue still requires additional inbound/outbound current-state authority. The normalized SQLite design through v0.16 remains the production candidate; this remains an experimental storage-layer falsification.

## Status

Implementation and falsification are in progress on the stacked v0.40 branch. v0.39 remains independently merge-gated by the external approving-review requirement on `main`.
