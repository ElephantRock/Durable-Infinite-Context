# v0.41 — Bounded Multiple-FREE Live Queue-Tail Evacuation

## Observe

v0.40 can evacuate a live physical retirement-descriptor tail when that descriptor is also the logical queue tail and the relocation destination is the **sole** committed FREE descriptor. It deliberately refuses a multiple-FREE state before descriptor I/O because consuming the current free head without repairing its successor would leave stale bidirectional FREE topology.

The discriminating refusal fixture has live queue `[4,6]` and FREE chain `[2,0]`. Page `6` is the live physical/logical tail, page `4` is its tagged queue predecessor, page `2` is the current FREE head/relocation destination, and page `0` is the directly named next FREE node whose predecessor is page `2`.

## Diagnose

The multiple-FREE obstruction does not require free-chain search. The current free head already names its successor, and the successor already carries tagged predecessor authority. Consuming free head `F` as the QUEUED relocation destination changes exactly one additional FREE-topology edge: the directly named successor `S` must move from `S.prev = F` to `S.prev = null` before the superblock publishes `S` as the new free head.

## Derive

For a physical tail that is also the logical queue tail, bounded relocation with a non-empty FREE successor can therefore:

1. directly read the live tail `T`;
2. directly read its tagged queue predecessor `Q`;
3. directly read current free head/destination `F`;
4. directly read `S = F.next` and validate `S.prev = F` with tagged identities;
5. stage `T` into `F` under a fresh incarnation;
6. rewrite `Q.next` to the new `F` identity;
7. rewrite `S.prev` to null while preserving `S.next`;
8. fsync all descriptor dependencies;
9. atomically publish the relocated queue tail, `S` as the new free head, decremented free count, next-incarnation state, and shorter arena frontier;
10. truncate the old physical tail pair as derived cleanup.

No queue walk, FREE-chain walk, descriptor-history scan, or relocation-candidate search is required.

## Hypothesis

Direct repair of the new free head's predecessor is sufficient to extend v0.40 live queue-tail evacuation from a sole FREE destination to an arbitrarily long current FREE chain with work independent of tested FREE-chain length and queue depth, while preserving tagged identities and scan-free crash recovery.

## Surviving bounded result

The real discriminating fixture begins with live queue `[4,6]`, FREE chain `[2,0]`, and an eight-page retirement-descriptor arena.

- unchanged v0.40 control: exact-state no-op before descriptor I/O;
- v0.41 queue: `[4,6] -> [4,2]`;
- FREE chain: `[2,0] -> [0]`;
- the new FREE head at page `0` retains incarnation `1` and has a null predecessor;
- retirement arena: `8 -> 6` pages, releasing exactly `8,192` bytes;
- measured multiple-FREE relocation work: exactly `8 descriptor preads / 3 descriptor pwrites / 0 descriptor-history scans / 1 live relocation`;
- old tail identity `(page 6, incarnation 4)` is rejected after commit;
- old FREE destination identity `(page 2, incarnation 2)` is rejected after relocation; page `2` becomes the live tail at incarnation `5`;
- the surviving page-0 FREE identity is preserved while its predecessor authority is cleared;
- the sole-FREE v0.40 geometry remains compatible at exactly `6R / 2W / 0 scans / 1 relocation`.

## Locality scaling

Two independent scaling axes were exercised with real workloads.

FREE-chain scaling holds live queue depth at `2` while FREE-chain length grows `2,3,4,5`. Target descriptor counts `4,5,6,7` produce workload triggers `129,257,513,1025`; each relocation remains exactly `8R / 3W / 0 scans / 1 relocation` and releases one two-page descriptor pair.

Queue-depth scaling holds FREE-chain length at `2` while live queue depth grows `2,3,4,5`. The same target descriptor counts and workload triggers again remain exactly `8R / 3W / 0 scans / 1 relocation`.

This evidence establishes bounded work across the tested current-topology dimensions; it is not a proof over unbounded integer domains.

## Crash evidence

The v0.41-specific relocation matrix covers **8 real process-`SIGKILL` cases**:

- destination staging;
- queue-predecessor staging;
- **new free-head predecessor-clear staging**;
- dependency fsync;
- authoritative commit;
- post-commit relocation marker;
- physical arena truncate;
- final arena fsync.

All four pre-commit cases recover the exact original `[4,6]` / `[2,0]` state. The two post-commit/pre-truncate cases recover the exact relocated state and remove exactly `8,192` bytes of derived arena residue. Post-truncate cases require no further arena shortening. Every recovery performs zero generation/radix/retirement-descriptor scans and zero logical redo, and the second recovery is physically idempotent.

## Canonical evidence

Canonical raw `multi_free_live_tail_evacuation_results.json` SHA-256:

`2dbbeb08c35c7fd913d992305fe7e0a4a4e0ef4fdebf8234536463aca6a3371f`

The first complete candidate run on head `96a36f02178175ad01075d414648c18d60527cac` succeeded in dedicated v0.41 experiment run `35031907606`; artifact `10420919407` has archive digest `sha256:fecacf37128d1e3147326551b376789bd37dc750de889b1df375e06710d51d8d`. Independent artifact inspection reproduced the raw canonical SHA above and confirmed no `allocated_bytes` key is present.

`verify_multi_free_live_tail_evacuation_results.py` freezes the canonical raw SHA and claim-bearing control/topology/work/identity/crash/scaling values. The dedicated `v0.41 canonical-result verifier` workflow reruns and verifies that evidence on pull requests and on `main`.

Final exact-head full-CI and verifier anchors are intentionally recorded only after all documentation/workflow changes settle on one immutable pre-merge head.

## Deliberate boundary

v0.41 still requires the physical descriptor tail to be the logical retirement-queue tail. It still relocates into the current FREE-list head; arbitrary destination selection is not claimed. It repairs only the directly named new FREE head and performs no FREE-chain traversal. File-length truncation is not a claim about filesystem allocated-block reclamation. Process-`SIGKILL` evidence is not hardware power-loss or torn-write proof. Multi-writer correctness is not established. Descriptor page/incarnation authority remains finite-width.

The normalized SQLite design earned through v0.16 remains the production candidate. v0.41 remains an experimental storage-layer alternative.

## Next falsification target

The multiple-FREE destination obstruction is removed for a live physical tail that is also the logical queue tail. The next structural obstruction is a **live physical tail in the middle of the retirement queue**: such a descriptor has both an inbound queue predecessor and an outbound queue successor, neither of which may be represented by the existing tail-specific authority.

A v0.42 candidate should test whether bounded current-state authority for both adjacent queue edges can relocate a live middle-of-queue physical tail without queue traversal, while atomically repairing predecessor/successor references, preserving tagged identities and FREE topology, and retaining scan-free crash recovery.