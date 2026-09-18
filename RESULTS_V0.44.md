# v0.44 — Tagged QUEUED-Predecessor Authority

## Observe

v0.43 survives one additional live-prefix hop by publishing a second reverse-hop identity in superblock metadata. That proves the next bounded case, but it also exposes the representation pressure directly: extending the same mechanism to deeper live prefixes would require another special-purpose superblock reverse-hop field for each supported depth.

The discriminating v0.44 state is a live queue `[PPP, PP, P, T, S]` with one lower sole FREE destination `D`, where `T` is the physical retirement-descriptor tail, `S = T.next` is the logical queue tail, `P = T.prev`, and `PP = P.prev`. The additional live prefix node `PPP` proves that `PP` is not the queue head.

## Diagnose

A growing finite reverse window does not remove queue-depth dependence; it moves that dependence into metadata width. The missing primitive is a tagged current-neighbor predecessor edge attached to every QUEUED descriptor.

## First principle

Topology locality should be represented where the topology lives.

For a live queue edge `A -> B`, v0.44 tests bidirectional tagged current-state authority:

- `A.next = (B.page, B.incarnation)`;
- `B.prev = (A.page, A.incarnation)`.

The predecessor authority is stored in a dual-copy, page-addressed sidecar keyed by descriptor base page. It is finite-width per descriptor and is validated against the descriptor incarnation. The claim-bearing relocation path does not use v0.43's second superblock reverse-hop field to discover `PP`.

## Surviving result

The concrete five-node fixture is:

- before queue: `[4, 6, 8, 10, 2]`;
- before FREE list: `[0]`;
- physical tail: page `10`, incarnation `6`;
- direct predecessor: page `8`, incarnation `5`;
- predecessor-predecessor: page `6`, incarnation `4`;
- logical tail / physical-tail successor: page `2`, incarnation `7`;
- FREE destination: page `0`, incarnation `1`.

v0.43 refuses that deeper state before descriptor I/O. v0.44 relocates page `10` into page `0` at fresh incarnation `8`, producing queue `[4, 6, 8, 0, 2]` and shortening the descriptor arena from `12 -> 10` pages.

The positive relocation performs exactly:

- `10` retirement-descriptor preads;
- `2` retirement-descriptor pwrites;
- `8` queued-predecessor preads;
- `2` queued-predecessor pwrites;
- `0` retirement-descriptor history scans;
- `0` predecessor-chain scans;
- `1` live descriptor relocation;
- `8,192` bytes of logical arena length released.

The relocated payload and successor identity are preserved. Old physical-tail descriptor/predecessor identities and old FREE-destination descriptor/predecessor identities are all rejected after publication.

## Prefix-depth scaling

The same operation was tested at live queue depths `5, 6, 7, 8`. In every case direct work remains exactly `10 descriptor reads / 2 descriptor writes / 8 predecessor reads / 2 predecessor writes / 0 scans / 1 relocation`, while the physical-tail page moves from `10 -> 12 -> 14 -> 16`.

This supports the bounded-locality claim for the tested geometry: direct relocation work does not grow with the number of live prefix descriptors.

## Maintenance and crash evidence

The predecessor authority is maintained on all claim-relevant topology transitions:

- append: the new queue tail publishes `prev = old_tail`;
- FREE-head reuse: the reused descriptor publishes a fresh-incarnation predecessor record;
- queue-head reclaim: the new head publishes a null predecessor;
- live physical-tail relocation: destination and successor predecessor records are staged and fsynced before primary publication.

The experiment executes **28 real process-`SIGKILL` cases**:

- 6 append-authority failpoints;
- 6 FREE-reuse-authority failpoints;
- 6 queue-head-reclaim-authority failpoints;
- 10 relocation failpoints.

All 28 recover to the exact expected committed pre/post state. Recovery performs no generation-page, mapping-node, retirement-descriptor, or predecessor-history scan and no logical redo. A second recovery is physically idempotent in every tested case.

A malformed current predecessor edge is also rejected without mutating the primary file, descriptor arena, or predecessor sidecar.

## Canonical evidence

Canonical `bidirectional_queued_retirement_descriptor_results.json` SHA-256:

`1b387911d187559352eaf2eea6b275047911e89c154197d2585dab22c2ff0d3e`

The first exact-head surviving experiment was push workflow run `35043119013` on head `d36599faf7a3000c061f91b5f8df87efdb7dcdf3`:

- workflow: `v0.44 QUEUED predecessor experiment` #11;
- conclusion: **success**;
- artifact: `10426780308`;
- artifact ZIP digest: `sha256:42ed579604e0ebaa4a1dabd825d29a7da63fbcc2c85a814d6ad192e832079fb4`;
- canonical JSON SHA-256: `1b387911d187559352eaf2eea6b275047911e89c154197d2585dab22c2ff0d3e`.

The canonical payload intentionally contains no filesystem `allocated_bytes` observation.

A dedicated canonical verifier is added after this result note. Final candidate-head evidence is recorded only after that verifier and the full historical regression suite succeed on the resulting exact head.

## Review findings and boundary

Independent review found one diagnostic-accounting nuance that is deliberately excluded from the claim: internal late-refusal branches outside the supported geometry may already have performed direct validation reads before returning a generic no-release trace. v0.44 therefore makes **no zero-I/O claim for unsupported v0.44 refusal geometries**. The positive path's exact direct-work accounting, the unchanged v0.43 zero-read control, and the malformed-predecessor read-only rejection are the evidence-bearing cases.

The tested v0.44 geometry still requires:

- single-writer retirement topology;
- a directly addressed live physical tail;
- the physical-tail successor to be the logical queue tail;
- exactly one lower FREE destination.

It does not establish arbitrary multiple-FREE destination repair composition, arbitrary live-successor geometry, multi-writer concurrency, hardware power-loss/torn-write correctness, or filesystem/device block reclamation. File-length truncation is not proof of underlying block deallocation.

The normalized SQLite design through v0.16 remains the production candidate. v0.44 remains an experimental storage-layer alternative stacked on v0.43 and does not bypass the upstream governance gate on PR #39.

## Next falsification target

The next target should compose per-QUEUED tagged predecessor authority with the v0.41 multiple-FREE destination topology, testing whether both current-neighbor structures can be maintained and relocated together without introducing a traversal or an unbounded metadata map.