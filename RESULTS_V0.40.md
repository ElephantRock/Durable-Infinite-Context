# v0.40 — Bounded live physical queue-tail evacuation

## Observe

v0.39 removes the predecessor-discovery barrier for a physical descriptor tail that is already `FREE`. The next obstruction is a physical tail descriptor that remains live. File-length truncation cannot cross that pair even when a lower descriptor pair is free.

## Diagnose

For the first live-tail case, constrain the target so the physical tail is also the logical retirement-queue tail and the lower relocation destination is the **sole committed FREE descriptor**. The live tail has no queue successor. Relocation therefore requires only four pieces of current authority: the live tail identity, its queue predecessor, the sole lower free destination, and the committed arena frontier.

The live tail identity and arena frontier already exist in the superblock. v0.39 already provides a directly usable free-list head. The missing fact is the queue-tail predecessor. Discovering it by walking the queue would make maintenance depend on queue depth.

A review of the first candidate exposed an important boundary: if the free-list head has a successor, consuming that head also requires clearing the successor's predecessor link. Leaving that edge untouched would corrupt the bidirectional FREE topology. The claim-bearing v0.40 store therefore refuses relocation whenever `retirement_descriptor_free_count != 1`; that broader free-topology rewrite is deliberately deferred rather than hidden inside the claim.

A second review exposed an evidence gap rather than a relocation defect: the new queue-tail-predecessor authority was maintained by enqueue, but its own publication had not been subjected to real process-crash falsification. v0.40 now includes a dedicated five-point enqueue crash matrix in addition to the seven-point relocation crash matrix.

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

## Surviving bounded result

The discriminating workload first creates descriptors `[0,2,4]`, then reclaims page `0` so the live queue is `[2,4]`, page `4` is both physical and logical queue tail, page `2` is its named predecessor, and page `0` is the sole lower FREE descriptor.

- unchanged v0.39 control: no release; live physical tail remains in place;
- v0.40: queue `[2,4] -> [2,0]`, arena `6 -> 4` pages, releasing exactly `8,192` bytes;
- measured relocation work: exactly `6 descriptor preads / 2 descriptor pwrites / 0 descriptor-history scans / 1 live relocation`;
- queue depth remains `2`; the free-list becomes empty;
- old live-tail identity `(page 4, incarnation 3)` is rejected after commit;
- old FREE destination identity `(page 0, incarnation 1)` is rejected after relocation; the new live identity uses incarnation `4`;
- queue-depth scaling over target descriptor counts `3,4,5,6` — live depths `2,3,4,5` — preserves exactly `6R / 2W / 0 scans / 1 relocation`;
- a multiple-FREE fixture with live queue `[4,6]` and free chain `[2,0]` is refused before descriptor I/O with `0R / 0W / 0 scans / 0 relocations` and exact state preservation.

## Crash evidence

The v0.40-specific evidence covers **12 real process-`SIGKILL` cases**.

Seven cases surround live-tail relocation publication:

- destination staging;
- predecessor rewrite staging;
- dependency fsync;
- authoritative commit;
- post-commit relocation marker;
- physical arena truncate;
- final arena fsync.

Five additional cases surround publication of the new queue-tail-predecessor authority during enqueue:

- new retirement descriptor staging;
- old-tail link staging;
- retirement-arena fsync;
- primary-data fsync;
- authoritative commit.

For predecessor maintenance, the clean `k-0032` transition moves queue depth `1 -> 2`, advances tail page `0 -> 2`, and publishes `(page 0, incarnation 1)` as the new tail predecessor. At each of the four pre-commit crash points, recovery restores the exact singleton pre-state, removes exactly one uncommitted descriptor pair (`8,192` bytes) from the retirement arena, performs zero generation/radix/retirement-descriptor scans and zero logical redo, and a second recovery is physically idempotent. At `committed`, recovery preserves the exact two-node post-state with no arena cleanup required.

Every relocation crash case likewise recovers to the exact committed pre/post state, recovery remains scan-free, and the second recovery is physically idempotent. At post-commit/pre-truncate relocation points, recovery removes exactly `8,192` bytes of derived retirement-arena residue.

## Canonical evidence

Canonical raw `live_tail_evacuation_results.json` SHA-256:

`82902b1ff4cf5d265fe828c9dbfda7b7abfec71501da2608812bbb359391e63f`

`verify_live_tail_evacuation_results.py` reruns the real experiment, validates the v0.39 control, exact sole-FREE relocation topology/work, tagged stale-identity rejection, multiple-FREE zero-I/O refusal, all seven relocation crashes, all five queue-tail-predecessor maintenance crashes, scan-free/idempotent recovery, queue-depth scaling, canonical byte serialization, and exact hash reproduction. The dedicated `v0.40 canonical-result verifier` workflow runs that gate on pull requests and on `main`; full CI also runs the v0.40 experiment and uploads its result artifact.

## Review status

Two review findings were resolved before closure of the candidate evidence:

1. the original destination claim was too broad for a free-list head with a successor, so the claim-bearing implementation was narrowed to the sole-FREE geometry and a multiple-FREE zero-I/O refusal fixture was added;
2. crash atomicity of the newly introduced queue-tail-predecessor authority itself was initially untested, so a five-failpoint real-process enqueue crash matrix was added and frozen into the canonical verifier.

No remaining blocker has been identified in the bounded sole-FREE claim path. PR #40 remains intentionally stacked behind v0.39; this result does not bypass the independent merge/review gate on PR #39.

## Deliberate boundary

v0.40 does **not** claim arbitrary live-tail relocation. The physical tail must be the logical queue tail, and the relocation destination must be the sole committed FREE descriptor/current free-list head. Multiple FREE descriptors require an additional successor-predecessor topology rewrite and are explicitly refused. A live physical tail in the middle of the queue still requires additional inbound/outbound current-state authority. File-length truncation is not a claim about filesystem allocated-block reclamation. Process-`SIGKILL` evidence is not hardware power-loss or torn-write proof. Descriptor page/incarnation authority remains finite-width. The normalized SQLite design through v0.16 remains the production candidate; this remains an experimental storage-layer falsification.

## Next falsification target

The immediate remaining obstruction is the multiple-FREE live queue-tail case. Its missing bounded fact is already isolated: consuming the current free head as the relocation destination must also rewrite the directly named new free head so its predecessor becomes null before authoritative publication.

A v0.41 candidate should test whether that additional direct successor/predecessor repair extends live queue-tail evacuation from a sole FREE destination to an arbitrarily long current FREE chain while keeping relocation work independent of free-chain length and queue depth, preserving tagged identity and scan-free crash recovery. A live physical tail in the middle of the retirement queue remains a later obstruction.
