# v0.43 — One-Extra-Reverse-Hop Physical-Tail Authority

## Observe

v0.42 can relocate a live physical retirement-descriptor tail after it becomes an interior queue node, but only when the physical-tail predecessor is the logical queue head and the physical-tail successor is the logical queue tail.

If the physical-tail predecessor `P` is not the queue head, relocating physical tail `T` makes `P` the new physical tail. Correct current-state authority for that new physical tail then requires the tagged predecessor of `P`. v0.42 does not represent that second reverse hop.

## Diagnose

For queue geometry `[PP, P, T, S]`, where `T` is the physical tail and `S` is the logical queue tail, relocation still changes only two descriptor records: destination `D` and predecessor `P`. But the authoritative state published after truncation must identify `PP` as the predecessor of the new physical tail `P`.

Discovering `PP` by walking from the queue head would make work depend on live queue depth. The missing fact is one additional tagged current-state reverse edge.

## Derive

v0.43 maintains:

- `retirement_physical_tail_predecessor = P`;
- `retirement_physical_tail_predecessor_predecessor = PP`.

The review-critical maintenance rule is that when a new descriptor is appended at the physical frontier, the new physical tail's predecessor is the **old logical tail**, so the new second reverse hop must come from the **old logical-tail predecessor authority**. It must not be copied from the prior physical-tail predecessor. When enqueue instead reuses a lower FREE descriptor, the physical frontier does not move and the existing two-hop physical-tail window is preserved.

For the bounded deeper geometry, relocation directly validates `T`, `P`, `PP`, `S = T.next`, and sole FREE destination `D`; copies `T` into `D` with a fresh incarnation; rewrites `P.next`; publishes `P` as the new physical tail with predecessor `PP`; then truncates the old physical pair as derived cleanup.

## Hypothesis

For exactly four live queue descriptors with `PP` equal to the queue head, `P` not equal to the queue head, `T` the live physical tail, `S` the logical queue tail, and one lower sole FREE destination, one additional tagged reverse-hop authority is sufficient for bounded relocation without queue/history traversal.

## Predictions

The candidate survives only if:

- unchanged v0.42 refuses the four-node interior-tail state before descriptor I/O;
- queue `[4,6,8,2]` with sole FREE page `0` becomes `[4,6,0,2]`;
- arena length shrinks `10 -> 8` pages and releases exactly `8,192` bytes;
- relocation performs `10 descriptor preads / 2 descriptor pwrites / 0 descriptor-history scans / 1 live relocation`;
- logical tail remains page `2`, tail-predecessor authority changes `8 -> 0`, and the new physical tail becomes page `6` with predecessor page `4`;
- old physical-tail and old FREE-destination tagged identities are rejected while the successor identity and relocated payload are preserved;
- append publication and FREE-reuse preservation of the second reverse hop survive real process-`SIGKILL` tests with exact, scan-free, idempotent recovery;
- a deeper prefix outside the one-extra-hop geometry is refused rather than traversed.

## Test

The discriminating fixture allocates descriptor pairs `[0,2,4,6,8]`, reclaims the first two queue heads to produce live queue `[4,6,8]` and FREE chain `[2,0]`, then enqueues through FREE-head reuse to produce:

- live queue `[4,6,8,2]`;
- sole FREE page `[0]`;
- physical tail `T = 8`;
- physical-tail predecessor `P = 6`;
- physical-tail predecessor-predecessor `PP = 4`;
- logical queue tail / `T.next` successor `S = 2`;
- relocation destination `D = 0`.

The experiment also executes three crash matrices:

- 7 process-`SIGKILL` points around relocation;
- 5 process-`SIGKILL` points around physical-frontier append and second-reverse-hop publication;
- 5 process-`SIGKILL` points around lower-FREE reuse and second-reverse-hop preservation.

That is **17 real process-`SIGKILL` cases** in the v0.43 claim-bearing experiment.

## Result

The hypothesis survived the stated geometry.

Unchanged v0.42 refuses `[4,6,8,2]` with **0 descriptor preads / 0 pwrites / 0 scans / 0 relocations** and exact state preservation.

v0.43 relocates:

- queue `[4,6,8,2] -> [4,6,0,2]`;
- FREE list `[0] -> []`;
- arena `10 -> 8` pages;
- physical tail `8 -> 6`;
- physical-tail predecessor `6 -> 4`;
- logical-tail predecessor `8 -> 0`.

Measured claim-bearing relocation work is exactly:

- `10` descriptor preads;
- `2` descriptor pwrites;
- `0` retirement-descriptor scans;
- `1` live relocation;
- `8,192` bytes of committed sidecar length released.

Tagged identities are explicit in the frozen result: physical tail `(8,5)`, predecessor `(6,4)`, predecessor-predecessor `(4,3)`, successor `(2,6)`, destination `(0,1) -> (0,7)`, new physical tail `(6,4)`, and its predecessor `(4,3)`. The old physical-tail and destination identities are rejected; successor identity and relocated payload are preserved.

All 17 crash cases recover the exact expected committed state, perform zero generation/radix/retirement-descriptor scans and zero logical redo, and have a physically idempotent second recovery. In relocation crashes after authoritative commit but before truncation, recovery removes exactly `8,192` bytes of derived arena residue. Precommit append crashes recover the uncommitted two-page descriptor append as derived arena residue. FREE-reuse crashes require no descriptor-arena truncation because the committed frontier does not change.

The deeper five-node state `[4,6,8,10,2]` with physical-tail predecessor-predecessor `6` is refused before descriptor I/O: **0R / 0W / 0 scans / 0 relocations**, exact state unchanged.

## Canonical evidence

Canonical `deep_middle_live_tail_evacuation_results.json` SHA-256:

`6ced2a53b5c8fd61524ce005645b6d7303f87d0baf6b3cdcf3e844a4f0f710a4`

The minimized canonical payload was first reproduced successfully by focused experiment run `35040316444` on head `c93f2d67e938eb58be1cf53d2063ea0de7139275`; artifact `10425285906`, archive digest `sha256:91e4557a5748fe79dfbc183d8677fe395f976bda33504dad29ee7b93f6fd175d`.

The repository now contains the frozen canonical JSON and `verify_deep_middle_live_tail_evacuation_results.py`, which reruns the real experiment, requires byte-exact reproduction and the frozen SHA-256, and independently asserts the control boundary, exact topology/work, tagged identities, all three crash classifications, recovery cleanup, and deeper-prefix refusal.

Final exact-head CI/verifier evidence is intentionally recorded on PR #43 after this result note is committed, so documentation does not mutate the validated head afterward.

## Evidence interpretation

The result supports a narrow first-principles conclusion: bounded relocation can be extended one live-prefix node deeper by carrying one additional authoritative reverse hop. It also exposes the architectural pressure immediately: each additional unsupported prefix depth would require another finite reverse hop if this representation were extended literally. That is evidence for testing a more general bidirectional/current-neighbor authority next, not evidence that an unbounded reverse window is desirable.

## Nonclaims

v0.43 does **not** establish arbitrary interior queue relocation, repeated relocation at arbitrary depth, or queue-depth-independent reverse maintenance for an unbounded live prefix. It does not compose this deeper geometry with the v0.41 multiple-FREE destination repair. It does not establish multi-writer concurrency, hardware power-loss/torn-write behavior, or filesystem/device allocated-block reclamation. File-length truncation is not proof of underlying block deallocation.

The normalized SQLite design earned through v0.16 remains the production candidate. v0.43 remains an experimental storage-layer falsification stacked on exact v0.42 head `5b835ff7ec1b3994e7bacb6975b6bd2d3e23c3da`.

## Next falsification target

The next target should test whether a **general tagged predecessor edge on QUEUED descriptors** can replace the growing finite reverse-window metadata and permit bounded physical-tail relocation when the live prefix is deeper than one extra hop, without queue traversal and without maintaining an unbounded superblock reverse chain.
