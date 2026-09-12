# v0.36 — Descriptor tail-release falsification

## Observe

v0.35 stops retirement-descriptor storage from following completed-generation history once reusable descriptor capacity exists, but it retains every allocated descriptor pair after a backlog peak. The remaining question is physical retention: after the retirement queue drains, can an excess free descriptor pair be returned by bounded file-tail truncation without relocation, placement changes, descriptor-history scans, or a broader allocator?

## Diagnose

File-tail truncation can release a free object in place only when that object occupies the committed physical suffix. A free-list identity does not imply tail residency.

The first positive v0.36 attempt assumed the current descriptor free-list head would become the file tail after drain. Exact CI falsified that fixture assumption. The positive release/crash machinery was removed rather than promoted around the counterexample.

## Derive

The surviving operational candidate is deliberately strict:

1. inspect only the committed descriptor free-list head;
2. release one two-page descriptor pair only if `free_head_page + 2 == next_physical_page`;
3. otherwise stop without scanning deeper free/history state.

A separate full-free-chain snapshot is diagnostic only. It characterizes placement but is not candidate foreground work and is not used to claim locality.

## Hypothesis

After real retirement work drains, the current free-list head may also be the committed append tail, allowing one descriptor pair to be returned with zero history walks and zero relocation work.

## Predictions

The candidate survives only if at least one tested real drained fixture has a tail-resident current free head. It is falsified for the tested interleaved placement if committed pages remain above the free head in both fixtures. The diagnostic snapshot additionally records whether any currently free descriptor pair, not merely the head, occupies the tail.

## Test

The experiment uses the real v0.35 `RecyclableRetirementDescriptorPrimaryStore`, capacity 32, `max_load=0.50`, migration-slot budget 4096, and fixed reclamation budget `B=2`. It preserves the v0.16 semantic guard.

Two real fixtures are drained completely:

| Fixture | Inserted keys | Descriptor pool | Free descriptor pages after drain | Committed frontier | Smallest committed suffix |
|---|---:|---:|---|---:|---:|
| one descriptor | 17 | 1 | `52` | `88` | 34 pages |
| three-descriptor peak | 65 | 3 | `158, 88, 52` | `296` | 136 pages |

The one-descriptor pair occupies pages `[52,54)`, leaving 34 committed pages above it.

For the three-descriptor peak, the diagnostic free-list layout is:

```text
pair [158,160) -> 136 committed suffix pages
pair [ 88, 90) -> 206 committed suffix pages
pair [ 52, 54) -> 242 committed suffix pages
```

Every observed free descriptor is therefore buried below the committed physical frontier. The bounded candidate performs:

```text
candidate history walks           = 0
candidate relocations             = 0
candidate physical pages released = 0
```

Because the surviving candidate performs no mutation, v0.36 adds no new crash-publication protocol or crash matrix. Crash testing belongs to the next positive mechanism that actually changes placement or ownership.

## Result

**The v0.36 tail-truncation candidate is falsified under the current interleaved append-local placement.** The current free-list head is not the physical file tail in either real fixture, and the diagnostic full free chain contains no tail-resident descriptor pair. Head-only truncation correctly releases zero pages without introducing descriptor-history work.

This is a negative result, not a failure to complete the experiment. It constrains the next design: physical descriptor-capacity reduction must change or escape the current placement, for example through descriptor segregation, relocation, or a broader allocator able to reuse buried free pairs.

The result does **not** prove every bounded descriptor-pool reduction scheme impossible.

## Evidence anchor

The strengthened diagnostic experiment first passed the complete historical CI chain on the exact head:

```text
workflow: CI
run number: 380
run id: 34683103991
job id: 103525241535
head: 035b2b1588795d39d79469175d3b8c8f96c2c092
artifact id: 10295091421
artifact digest: sha256:e34a7556275e2177e5665cb72d9f66c2f9565623b5245a13d1c9306620b88f53
descriptor_tail_release_results.json sha256: 76f17b72a44e59d193a7e0cf785f57b782935b64c2809929fc6dbc78ecdd0bb5
```

v0.36 freezes the canonical **raw-result SHA-256** in `verify_descriptor_tail_release_results.py`. The verifier reruns the real experiment, checks the claim-bearing fixture values and locality/non-locality distinctions, requires canonical byte serialization, and requires the generated result bytes to reproduce that exact hash.

The first verifier workflow pass was:

```text
workflow: v0.36 canonical-result verifier
run number: 1
run id: 34684439912
job id: 103528845030
head: 1b52ce51df782718e06cfd7349d668d6a73ae024
result verifier: success
```

## Deliberate non-claims

- v0.36 does not prove all bounded descriptor-pool reduction mechanisms impossible.
- It does not test descriptor segregation, relocation, compaction, hole punching, or a generalized free-space allocator.
- It does not establish filesystem block deallocation; the candidate performs no truncation.
- The diagnostic full-free-chain traversal is measurement only and is not part of candidate foreground work.
- Zero candidate history walks do not imply constant work for a future relocation or allocator mechanism.
- No new crash-safety claim is added because the surviving negative candidate mutates no state.
- v0.35 remains the surviving mechanism for safe internal descriptor recycling and retains its finite uint64-incarnation and single-writer process-crash boundaries.
- The normalized SQLite design earned through v0.16 remains the production candidate; v0.36 is an experimental storage-layer falsification.

## Next falsification target

The next candidate should change placement rather than add more tail metadata. The strongest direct follow-up is a **segregated reclaimable descriptor arena**: can retirement descriptors be placed so excess post-peak capacity can be returned or transferred with bounded metadata work, without descriptor-history scans, ABA aliasing, crash resurrection, or foreground work proportional to the retained pool?

Any positive reuse/release mechanism must preserve incarnation-aware identity across physical reuse and must earn its own publication/recovery crash evidence.
