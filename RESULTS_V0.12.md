# v0.12 Results — Local Topology-Growth Materialization

## Problem

v0.11 fixed stale intent-impact metadata when an earlier queued mutation changed existing dependency topology. That result still assumed the derived outputs required by the new canonical topology already existed.

The next counterexample removes that assumption:

1. bootstrap a subject with a `deadline` assertion and its derived profile/state/support/context;
2. enqueue a topology move of that assertion to a brand-new subject;
3. let canonical mutation and local maintenance complete;
4. compare the persistent materialization with a clean reconstruction from canonical truth.

The v0.11 control moves canonical truth successfully, retires the old subject, and leaves every remaining derived row marked `fresh` — but the new subject has no derived rows at all.

Thus the failure is not stale lifecycle state. It is **missing required topology**:

\[
CanonicalTruthExists
\land
AllExistingDerivedNodesFresh
\land
RequiredDerivedOutputMissing
\]

## First principle

Invalidation can only repair materialization that exists. Canonical topology growth can create new output obligations that have no pre-existing derived node to invalidate.

Therefore:

\[
AffectedRegion
=
ExistingAffectedNodes
\cup
MissingRequiredOutputs
\]

For the controlled `deadline` path, a new subject/key requires the bounded output set:

\[
\{Profile, State, Support, Context\}
\]

Only outputs that are actually absent should become growth obligations. Ordinary object-only assertion updates must keep the narrower pre-v0.12 maintenance path.

## Mechanism

`GrowthAwareTopologyStore` extends v0.11 promotion/revalidation with local creation during invalidation:

1. derive candidate output obligations from a changed assertion key;
2. probe those output IDs directly in `derived_nodes`;
3. keep only missing outputs;
4. union them with the inherited indexed affected region;
5. insert missing outputs as `invalid` placeholders in the same invalidation transaction;
6. persist the complete local affected set in `affected_json`;
7. reuse the existing topological repair/retirement path.

The mechanism does not scan the global derived set to discover missing outputs. The controlled growth set contains four deterministic output IDs.

A separate regression verifies that the brand-new target read is blocked after canonical commit and before growth repair, while an unrelated read remains admitted.

## Differential falsification result

At 64 bootstrap entities:

### v0.11 control

- canonical assertion moved: **true**;
- target context present: **false**;
- target derived-node count: **0**;
- old subject retired: **true**;
- all remaining derived nodes fresh: **true**;
- clean-rebuild parity: **false**;
- recovery work: **35**;
- full-rebuild control: **885**.

This proves that `all derived rows are fresh` is not a sufficient completeness invariant.

### v0.12 growth-aware repair

- canonical assertion moved: **true**;
- target context present: **true**;
- target derived-node count: **4**;
- old subject retired: **true**;
- all derived nodes fresh: **true**;
- clean-rebuild parity: **true**;
- recovery work: **76**;
- full-rebuild control: **896**.

The four created outputs are the target subject's profile, state, support, and context materializations.

### Measurement correction

The first v0.12 experiment artifact reported **72** logical recovery operations. Audit found that the four deterministic primary-key probes used to determine which required outputs were missing were not included in `PersistentRecoveryTrace.logical_work`.

The mechanism was not changed. The instrumentation was corrected so each required-output existence probe counts as one derived lookup. The corrected result is therefore **76** logical operations for the fixed four-output obligation.

This correction is intentionally preserved in the result history rather than silently replacing the earlier measurement.

## Locality

The same one-move topology-growth workload was run while unrelated cardinality increased:

| Entities | Recovery work | Full rebuild |
|---:|---:|---:|
| 100 | **76** | 1,400 |
| 1,000 | **76** | 14,000 |
| 10,000 | **76** | 140,000 |
| 50,000 | **76** | 700,000 |

For this fixed output-obligation workload:

\[
RecoveryWork(1,N)=76
\]

through 50,000 entities under the benchmark's logical-work accounting.

The result supports the narrower prototype-level invariant:

\[
\boxed{
CanonicalTopologyGrowth
+
ExplicitMissingOutputObligations
+
LocalInvalidPlaceholderCreation
+
TopologicalRepair
\Rightarrow
ExactDerivedCompletenessForTheControlledMaterializationPath
}
\]

It does not support a universal O(1) mutation claim. Work may grow with the number of genuinely required outputs and dependency fan-out.

## Reproducibility

The machine-readable evidence is `growth_results.json`.

`verify_growth_results.py` reruns the v0.11 failure control, corrected v0.12 mechanism, and 100/1,000/10,000/50,000 locality sweep. It requires the control failure, corrected completeness, local recovery cheaper than full rebuild, constant fixed-obligation work across the sweep, and exact equality with the committed ledger.

## Architectural revision

v0.11's maintenance view was effectively:

\[
Repair = RevalidateImpact + InvalidateExisting + Rebuild/Retire
\]

v0.12 revises it to:

\[
\boxed{
Repair
=
RevalidateImpact
+
DiscoverExistingImpact
+
DeriveMissingOutputObligations
+
CreateMissingOutputs
+
Rebuild/Retire
}
\]

A derived-state system therefore needs both **invalidity detection** and **materialization completeness**.

## Limitations and next falsification target

This experiment remains deliberately narrow:

- one SQLite database;
- one ordered assertion topology move;
- one brand-new subject;
- the existing `deadline` materialization schema;
- synthetic oracle assertions;
- no distributed claim;
- no arbitrary new-predicate materialization claim.

The next high-value boundary is **predicate-schema growth**. The current persistent profile path was designed around the controlled `deadline` predicate, while state/support/context identifiers can encode arbitrary predicates. A canonical mutation that introduces a genuinely new predicate may therefore expose mismatched output-schema assumptions even if missing nodes can now be created locally.
