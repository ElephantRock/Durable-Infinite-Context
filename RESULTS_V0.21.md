# v0.21 — Bounded Placement Escape

## Problem

v0.20 established an explicit finite placement-work cap for a two-choice bucketized cuckoo model, but concentrated demand into one two-bucket domain admitted only 16 keys before the 17th insertion failed. The remaining question was whether that local failure could escape through a finite number of independent bounded domains without recreating an unbounded probe chain, a global rehash, or an overflow scan.

## First-principles hypothesis

Let `D` be the number of independent bounded placement domains. Each domain retains the v0.20 fixture:

- 4 slots per bucket;
- 2 candidate buckets;
- maximum 32 relocations;
- stash capacity 8;
- explicit per-domain mutation-work bound 200 modeled slot operations.

Under the deliberately concentrated collision fixture, one domain can admit 16 keys: 8 bucket slots plus 8 stash entries. Therefore the fixed predictions were:

\[
\boxed{
\begin{aligned}
ConcentratedCapacity(D) &= 16D,\\
FirstFailure(D) &= 16D+1,\\
MutationWorkCap(D) &= 200D,\\
MissingLookupPageCap(D) &= 3D,\\
ReservedSpaceAmplification(D) &= D.
\end{aligned}
}
\]

The important falsification condition was not merely that more domains admit more keys. Every admitted key had to remain retrievable after later failed inserts, and the escape mechanism had to expose all costs explicitly.

## Fixed experiment

The experiment preserves the v0.16 semantic guard and compares the v0.21 finite-domain escape model against the historical v0.20 linear-probe control.

Ordinary growth uses four reserved bounded domains and sweeps:

`N={1,000,4,000,16,000,64,000,256,000}`.

Collision stress uses:

`D={1,2,4,8}`

and forces every stress key into the same two candidate buckets inside every domain. This is intentionally adversarial with respect to local capacity.

Measurement counts modeled bucket-slot inspections, relocations, stash writes, rollback writes, domains attempted, and logical lookup pages. It is not a persistence, crash-safety, OS-page, device-I/O, or production-latency measurement.

## Initial CI evidence

The first complete v0.21 CI execution was run **#206** on implementation head:

`77d50832833dbfc23410efd82a9b9b893edafba3`

All unit tests, historical experiments/verifiers, the new v0.21 runner, and evidence uploads succeeded.

Artifact digest:

`sha256:8ae2442a8374066821c6ee04ebc606954c16fa4d626f46197e633b0b189c5e3d`

Contained JSON SHA-256:

`1915a94b96dc58e9fdedc5cf26411fdf4fc34fdf2067dd3b1d21dc760a3d7578`

The committed machine-readable ledger is `bounded_escape_results.json`; `verify_bounded_escape_results.py` reruns the exact experiment and requires exact parsed-data equality.

## Ordinary growth result

The ordinary fixture never leaves domain 0 despite reserving four domains:

| N | Linear max insert probes | Escape failures | Max domains attempted | Escape max mutation work | Lookup page max |
|---:|---:|---:|---:|---:|---:|
| 1,000 | 11 | 0 | 1 | 17 | 2 |
| 4,000 | 19 | 0 | 1 | 17 | 2 |
| 16,000 | 21 | 0 | 1 | 22 | 2 |
| 64,000 | 31 | 0 | 1 | 27 | 2 |
| 256,000 | 34 | 0 | 1 | 30 | 2 |

No ordinary insertion consumed a stash. The common path therefore remains identical in observed locality to the single-domain v0.20 candidate, while the implementation reserves four times the single-domain slot capacity.

At this fixture the effective load across all reserved slots is only `0.1220703125`, making the space trade-off explicit rather than hiding it.

## Concentrated-collision result

The fixed predictions were reproduced exactly:

| D | Concentrated capacity | First failure | Max mutation work | Missing lookup pages | Reserved-space amplification |
|---:|---:|---:|---:|---:|---:|
| 1 | 16 | 17 | 200 | 3 | 1x |
| 2 | 32 | 33 | 400 | 6 | 2x |
| 4 | 64 | 65 | 800 | 12 | 4x |
| 8 | 128 | 129 | 1,600 | 24 | 8x |

Every admitted key remained retrievable after later failed insertions in every stress case.

Thus a finite sequence of bounded domains is a valid **bounded escalation** mechanism. It moves the concentrated failure threshold linearly with `D` without introducing an unbounded probe or hidden global rebuild.

## What survives

For fixed finite `D`:

\[
\boxed{
\begin{aligned}
MutationWork &\le 200D,\\
MissingLookupPages &\le 3D,\\
ConcentratedCapacity &= 16D,\\
ReservedSpaceAmplification &= D.
\end{aligned}
}
\]

The common ordinary path still stays on the first domain in the tested workload.

## What fails

The experiment falsifies the stronger interpretation that finite domain escalation provides unlimited admission under arbitrary concentrated collisions.

For every fixed finite `D`, capacity remains finite and failure appears at `16D+1`. Allowing `D` itself to grow without bound would merely move the non-locality into mutation work, missing-key lookup fan-out, and reserved capacity:

\[
D\uparrow \Rightarrow
MutationCap\uparrow,
LookupFanout\uparrow,
SpaceAmplification\uparrow.
\]

So the project must not claim both arbitrary collision admission and a fixed end-to-end locality bound from this mechanism.

The central distinction becomes:

> **Bounded escalation can raise a failure threshold, but unlimited admission requires either unbounded resources or an explicitly different exceptional path.**

## Revision

Do not replace the production B-tree with this candidate yet. The next falsification target should isolate exceptional overflow instead of pretending it disappeared.

A defensible v0.22 candidate is a hybrid:

- bounded common path using the surviving local placement mechanism;
- explicit rare overflow path backed by a comparison index or another durable structure;
- no unbounded scan;
- measure overflow incidence, common-path locality, overflow lookup/mutation cost, and whether exceptional state contaminates ordinary-path guarantees.

The project should accept a logarithmic exceptional path if that is the real cost of guaranteeing admission, rather than hiding it behind unbounded domain escalation.

Persistence, crash/restart correctness, and stale-read safety remain deferred until the admission policy itself survives this next falsification.
