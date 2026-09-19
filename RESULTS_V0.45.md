# v0.45 — Multiple-FREE + Tagged QUEUED-Predecessor Composition

## Observe

v0.44 removes the growing finite reverse-window problem for deeper live queue prefixes by storing one tagged predecessor identity per QUEUED descriptor. Its surviving geometry still requires the relocation destination to be the sole FREE descriptor.

v0.41 independently shows that consuming the head of a non-singleton FREE chain is bounded when the directly named FREE successor has its predecessor cleared before publication.

The next unresolved composition is therefore a deeper interior live physical tail plus a multiple-FREE destination chain in the same relocation transaction.

## Diagnose

The queue-side and FREE-side repairs touch different current-neighbor edges. If composition is truly local, adding the FREE successor should add only one directly addressed descriptor read/rewrite and should not reintroduce queue traversal, free-chain traversal, history scanning, or a queue-sized metadata map.

## First principle

Topology locality should be represented where the topology lives.

For QUEUED topology, v0.44 supplies tagged `prev` authority. For FREE topology, v0.39/v0.41 supply tagged predecessor authority in the FREE descriptor representation. A composed relocation should repair exactly the directly affected neighbors before one authoritative superblock publication.

## Hypothesis

A deeper interior live physical tail whose successor is the logical queue tail can be relocated into the current head of a non-singleton FREE chain with bounded direct work independent of both tested live-prefix depth and FREE-chain depth.

## Predictions

The candidate survives only if:

- unchanged v0.44 refuses the discriminating multiple-FREE state without mutating it;
- the v0.45 candidate relocates the live physical tail and consumes exactly one FREE head;
- the new FREE head retains its incarnation and publishes a null FREE predecessor;
- QUEUED predecessor authority remains exactly consistent after relocation;
- old physical-tail and consumed-destination descriptor/predecessor identities are rejected;
- real process-`SIGKILL` recovery around the added FREE-successor staging point is exact, scan-free, and second-recovery idempotent;
- direct work is constant across tested live-prefix depths and FREE-chain lengths;
- canonical evidence contains no environment-sensitive allocated-block observation.

## Status

In progress. No v0.45 survival claim or canonical result hash exists yet.

The normalized SQLite design through v0.16 remains the production candidate. v0.45 is an experimental storage-layer falsification stacked on exact v0.44 candidate head `e16a9f878d99920033e8c6b70dfae124039094a0`.

## Deliberate boundary

This target composes multiple-FREE repair with the v0.44 queue geometry only. It does not yet claim an arbitrary live successor chain, arbitrary relocation destination selection, multi-writer correctness, hardware power-loss/torn-write safety, or filesystem/device allocated-block reclamation.