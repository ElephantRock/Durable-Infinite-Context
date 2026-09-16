# v0.43 — One-Extra-Reverse-Hop Physical-Tail Authority

## Observe

v0.42 can relocate a live physical retirement-descriptor tail after it has become an interior queue node, but only in the first three-node geometry: the physical-tail predecessor is the logical queue head and the physical-tail successor is the logical queue tail.

That boundary matters after truncation. If the physical-tail predecessor `P` is not the queue head, then relocating physical tail `T` makes `P` the new physical tail. Correct current-state authority for the new physical tail then requires the tagged predecessor of `P`. v0.42 does not represent that second reverse hop.

## Diagnose

For queue geometry `[PP, P, T, S]`, where `T` is the physical tail and `S` is the logical queue tail, the relocation itself still changes only two descriptor records: destination `D` and predecessor `P`. However, the authoritative state published after truncation must identify `PP` as the predecessor of the new physical tail `P`.

Discovering `PP` by walking from the queue head would make maintenance depend on live queue depth. The missing fact is therefore one additional tagged current-state reverse edge.

## Derive

Maintain a second physical-tail reverse-hop authority:

- `retirement_physical_tail_predecessor = P`;
- `retirement_physical_tail_predecessor_predecessor = PP`.

When a new descriptor is appended at the physical frontier, the old logical tail becomes `P` and the already-authoritative logical-tail predecessor becomes `PP`. When a lower FREE descriptor is reused, the physical frontier does not move, so the existing two-hop window is preserved.

For the first deeper relocation geometry, read and validate `T`, `P`, `PP`, `S = T.next`, and sole FREE destination `D`; relocate `T` into `D`; rewrite `P.next`; publish `P` as the new physical tail with predecessor `PP`; then truncate the old physical pair as derived cleanup.

## Hypothesis

For exactly four live queue descriptors with `PP` equal to the queue head, `P` not equal to the queue head, `T` the live physical tail, `S` the logical queue tail, and one lower sole FREE destination, one additional tagged reverse-hop authority is sufficient for bounded relocation without queue/history traversal.

## Predictions

The candidate survives only if:

- the unchanged v0.42 mechanism refuses the four-node interior-tail state before descriptor I/O;
- a concrete queue `[4,6,8,2]` with sole FREE page `0` can become `[4,6,0,2]`;
- the arena shrinks `10 -> 8` pages and releases exactly `8,192` bytes;
- relocation performs bounded direct work, predicted as `10 descriptor preads / 2 descriptor pwrites / 0 descriptor-history scans / 1 live relocation` when `PP` is explicitly validated;
- the logical tail remains page `2`, its predecessor authority changes `8 -> 0`, and the new physical tail becomes page `6` with predecessor page `4`;
- old physical-tail and old FREE-destination identities are rejected while the successor identity and relocated payload are preserved;
- publication and preservation of the new second reverse-hop authority survive real process-`SIGKILL` tests with exact, scan-free, idempotent recovery;
- a deeper prefix outside the one-extra-hop geometry is refused rather than silently traversed or overclaimed.

## Planned discriminating fixture

Allocate descriptor pairs `[0,2,4,6,8]`, reclaim the first two queue heads to obtain live queue `[4,6,8]` and FREE chain `[2,0]`, then enqueue through FREE-head reuse to obtain `[4,6,8,2]` with sole FREE page `0`.

The expected current-state authority is:

- physical tail `T = 8`;
- physical-tail predecessor `P = 6`;
- physical-tail predecessor-predecessor `PP = 4`;
- logical queue tail / `T.next` successor `S = 2`;
- sole FREE relocation destination `D = 0`.

## Status

In progress. No v0.43 survival claim or canonical evidence hash exists yet.

The normalized SQLite design through v0.16 remains the production candidate. v0.43 is an experimental storage-layer falsification stacked on exact v0.42 head `5b835ff7ec1b3994e7bacb6975b6bd2d3e23c3da`.

## Deliberate boundary

This target does not propose an arbitrary reverse chain. It tests exactly one additional reverse hop. A longer prefix, repeated arbitrary relocation, composition with multiple-FREE destination repair, multi-writer concurrency, hardware power-loss/torn-write behavior, and filesystem/device block reclamation remain outside the planned claim.
