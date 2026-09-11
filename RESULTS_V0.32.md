# v0.32 — Retired-generation reclamation

## Observe

v0.31 removes representation-driven radix overflow, but committed mappings and their physical segments remain durable implementation history after they are no longer semantically live. Two distinct lifecycle hazards remained:

1. discovering retired segments by walking generation capacity or scanning the radix would reintroduce non-local work; and
2. reusing a reclaimed physical segment without invalidating its data pages can expose valid retired records through a new logical mapping.

The second hazard was not hypothetical. A real-file control in this experiment reuses the same physical extent under a different logical segment id without scrubbing. A seeded valid old page becomes visible through the new mapping.

## Diagnose

Retirement locality and physical reuse safety are separate invariants.

A global retired-segment manifest makes retirement metadata proportional to the number of materialized segments `K`. A capacity walk makes discovery proportional to logical generation capacity `C`. Neither is acceptable as foreground cleanup work.

An intrusive ownership chain removes that discovery problem, but mapping removal alone does not make an extent safe for another owner. Data-page validity is independent of the radix mapping. Old CRC-valid records remain readable if the same physical pages are later mapped elsewhere.

## First-principles requirement

For lifecycle cleanup to remain operationally local:

- ownership discovery must follow explicit links, not infer ownership by scanning capacity or the full map;
- each foreground reclamation step must have an explicit finite work budget;
- a retired mapping must become unreachable before its extent is reused;
- every byte that can become visible under a new owner must either carry ownership/version identity or be invalidated before publication; and
- process recovery must choose one committed lifecycle state without reconstructing it by scan or logical redo.

## Mechanism

Each owned physical segment has a dual-copy lifecycle header. Headers form an intrusive per-generation ownership chain. The superblock stores only scalar lifecycle state:

- current owner generation/head/count;
- retired generation/cursor/remaining count; and
- free-list head/count.

Retirement publishes the previous ownership head as a committed retirement cursor. `reclaim_step(B)` follows at most `B` lifecycle links, removes exactly those radix leaf mappings, writes the corresponding headers as `FREE`, advances the retirement cursor, and publishes the new scalar metadata.

The first reuse implementation stopped there. The experiment falsified that design: an old valid data record became visible after mapping the free extent to a new logical id.

The surviving reuse mechanism therefore scrubs the fixed data footprint before publication. A segment contains 16 logical pages with two physical copies each, so reuse invalidates exactly 32 data pages, then stages the new radix edge, writes the lifecycle header as `OWNED`, syncs dependencies, and publishes the superblock.

## Prediction

With cleanup budget `B=3`, per-step work should remain bounded across retirement backlogs `K={1,8,32,64}`:

- at most 3 reclaimed segments;
- at most 12 lifecycle-header `pread`s;
- at most 3 lifecycle-header `pwrite`s;
- at most 3 radix leaf `pwrite`s in the shared-leaf fixture;
- 0 appended physical pages; and
- 2 publication barriers.

The flat-manifest control should grow with `K`, while a naive capacity walk should grow with `C` and the candidate should still visit only `B` ownership entries per step.

For reuse, the unscrubbed control should expose the seeded retired record. The scrubbed candidate should reuse the exact same physical segment, perform 32 fixed data-page invalidation writes, expose no retired payload, append no pages in the shared-radix-path fixture, and retain the retired mapping as absent.

Under `SIGKILL`, pre-superblock kills should expose the exact prior committed lifecycle/mapping state; a kill after committed publication should expose the exact post-state. Recovery should require zero generation scan, zero radix scan, and zero logical redo.

## Evidence

Successful dedicated v0.32 gate:

- workflow: `v0.32 experiment`;
- run #2;
- run ID `34445408286`;
- head `205a3c33844195db59258bf39286b02177c32dcf`;
- artifact ID `10139460269`;
- artifact SHA-256 `cffe21ddee73e381beff5ea1cb0b1827e0ca82aaf60cc207e790e56ad4392c31`;
- `retired_generation_reclamation_results.json` SHA-256 `79b7f10eb91f4cc80aab8ec30f9155e8a3c49b362bbf3c1b2c0cb865b3ee4e22`.

The committed result ledger is replayed by `verify_retired_generation_reclamation_results.py`.

## Results — bounded cleanup

| Retired segments | Reclaim steps | Max reclaimed / step | Max lifecycle preads | Max lifecycle pwrites | Max radix pwrites | Max appended pages |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1 | 1 | 4 | 1 | 1 | 0 |
| 8 | 3 | 3 | 12 | 3 | 3 | 0 |
| 32 | 11 | 3 | 12 | 3 | 3 | 0 |
| 64 | 22 | 3 | 12 | 3 | 3 | 0 |

Total reclamation still grows with the number of retired materialized segments. The surviving claim is only that each foreground step is capped by the explicit cleanup budget and does not grow with backlog or logical generation capacity.

The control comparison makes that distinction explicit. A JSON flat retired-segment manifest grows from **40 bytes at K=1** to **1,174 bytes at K=64**. The capacity-walk control grows from `1,024` to `1,099,511,627,776` logical slots, while the candidate remains at three ownership visits per cleanup step in the fixed experiment.

## Results — stale-payload falsification and repair

The mapping-only reuse control is rejected.

A valid retired page was seeded into a segment, the mapping was reclaimed, and the same physical extent was then mapped to a different logical segment id. Without scrubbing:

- the same physical extent was reused; and
- the retired payload became visible through the new logical mapping.

With the fixed-footprint scrub:

| Metric | Observed |
|---|---:|
| Same physical extent reused | yes |
| Retired mapping visible | no |
| Retired payload visible | no |
| Data-page scrub writes | 32 |
| Radix-node writes | 1 |
| Physical pages appended | 0 |
| Publication `fsync`s | 2 |

Thus v0.32 contains both a negative result and a repaired candidate:

\[
\boxed{Unmap + Reuse \not\Rightarrow PayloadIsolation}
\]

but, within the fixed segment geometry tested here,

\[
\boxed{Unmap + FixedFootprintInvalidation + Publish \Rightarrow NoObservedPayloadResurrection}
\]

## Results — process crash

The reclaim crash matrix covers:

- `mapping_unlinked`;
- `free_header_written`;
- `dependencies_synced`; and
- `committed`.

All **4/4** cases matched the exact expected committed lifecycle/mapping state.

The reuse crash matrix covers:

- `data_scrubbed`;
- `mapping_written`;
- `owner_header_written`;
- `dependencies_synced`; and
- `committed`.

All **5/5** cases matched the exact expected committed lifecycle/mapping state. Every pre-commit case recovered and then retried reuse successfully on the same reclaimed extent without exposing the seeded retired payload.

Across both matrices, recovery used the committed superblock frontier and required:

- zero generation-page scans;
- zero mapping-node scans;
- zero logical redo; and
- zero additional truncation on the second recovery pass.

## Revision

The original v0.32 mechanism — intrusive ownership plus mapping removal/free-list reuse — was insufficient. The experiment found a cross-owner payload leak in the real fixed-page representation. The causal model is therefore revised: lifecycle safety requires both address-space unreachability and data-footprint isolation.

The surviving v0.32 candidate adds fixed-footprint invalidation before reuse. The additional cost is 32 page writes per reused segment, which is constant only because the segment geometry is fixed in this experiment.

## Surviving claim

Within the fixed 16-logical-page, dual-copy segment model and single-writer process-crash model, intrusive dual-copy lifecycle headers allow retired mappings to be reclaimed with a fixed per-step budget independent of retirement backlog and logical generation capacity. Reclaim appends no physical pages in the tested shared-path fixture. Reuse of a free extent is safe against the demonstrated stale-record resurrection only after invalidating all 32 fixed data pages before publication. The tested reclaim and reuse crash matrices preserve one committed lifecycle interpretation with scan-free, redo-free recovery.

## Non-claims

v0.32 does **not** establish:

- constant total cleanup work; draining `K` retired materialized segments still requires `Theta(K)` ownership visits;
- filesystem allocated-block deallocation or hole punching;
- reclamation/pruning of now-empty radix metadata nodes;
- automatic ownership integration with every primary generation transition;
- correctness when one physical 16-page mapping segment straddles two logical primary generations;
- mathematically unbounded logical or physical identifiers;
- hardware power-loss, torn-sector, drive-cache, or filesystem-journal correctness;
- arbitrary multi-writer, distributed, or replicated consistency;
- device-I/O bounds from user-space read/write counts;
- production latency, throughput, or a production-ready replacement for the SQLite membership B-tree.

## Next falsification target — generation-boundary integration

The remaining lifecycle blocker is ownership attribution at real primary-generation boundaries. Existing logical generation bases are not guaranteed to align to 16-page mapping segments. A physical mapping segment can therefore potentially contain pages belonging to different primary generations.

v0.33 should test whether generation allocation can be made segment-aligned, or whether segment ownership must become finer-grained/reference-counted, without reintroducing capacity-sized padding, global scans, or unbounded migration work. The experiment should force multiple growth transitions, retire actual primary generations, inject process death across ownership transfer/reclamation, and reject any scheme that can free a segment while still-live logical pages share it.
