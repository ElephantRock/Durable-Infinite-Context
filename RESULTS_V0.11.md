# v0.11 Results — Topology-Dependent Intent Revalidation

## Problem

v0.10 made durable logical intent admission explicit and protected same-record writes with optimistic canonical versions. It also persisted `read_keys_json` at admission so stale derived reads could be blocked locally while an intent was active.

That is insufficient when an earlier queued intent changes dependency topology after a later intent has already captured its derived-impact metadata.

Controlled counterexample:

1. queue an assertion move from subject A to subject B;
2. before the move executes, queue an evidence update for evidence used by that assertion;
3. the evidence intent captures subject A as its admission-time read key;
4. execute the assertion move;
5. promote the evidence update and advance it to `CANONICAL_APPLIED`.

The unchanged v0.10 control retained subject A as the active read key. Subject B could therefore be stale while `read_context(subject B)` was admitted.

## First principle

Not all intent metadata has the same durability semantics.

Canonical mutation preconditions can remain admission-time facts when they are protected by the same canonical write-key version:

\[
AdmissionStable(x) \Leftarrow VersionGuardedBySameWriteKey(x)
\]

Topology-derived impact metadata can depend on other canonical records and therefore must be recomputed after all earlier intents have completed:

\[
PromotionRevalidated(x) \Leftarrow DependsOnMutableCrossRecordTopology(x)
\]

For the current operation set:

- `base_version` remains an admission-time canonical conflict precondition;
- `previous_json` is safe only because the corresponding canonical row is protected by that same write-key version;
- `read_keys_json` is provisional because it can depend on assertion/evidence topology changed by other write keys.

A regression now explicitly queues two moves against the same assertion from one base version. After the first move executes, the second promotion conflicts rather than using its stale `previous_json` snapshot.

## Mechanism

`PromotionRevalidatedTopologyStore` recomputes topology-dependent read keys inside the same `BEGIN IMMEDIATE` transaction that promotes an intent into the active maintenance journal.

Conceptually:

\[
IntentAdmission
\rightarrow DurableCanonicalPreconditions
\rightarrow WaitForEarlierIntents
\rightarrow RevalidateDerivedImpact(Topology_{promotion})
\rightarrow Activate
\]

The current evidence-update revalidation path follows the indexed `assertion_evidence(evidence_id, assertion_id)` lookup and then resolves the current assertion subject/predicate.

## Differential falsification result

The v0.10 control reproduced the predicted leak:

- admission read key: `cascade-subject-0000040|deadline`;
- promotion read key: unchanged;
- stale read admitted: **true**;
- stale pre-update value visible: **true**;
- unrelated read admitted: **true**;
- final clean-rebuild parity: **true**.

The v0.11 mechanism changed the same evidence intent at promotion:

- admission read key: `cascade-subject-0000040|deadline`;
- promotion read key: `cascade-subject-0000005|deadline`;
- read keys changed: **true**;
- newly affected stale read blocked: **true**;
- unrelated read admitted: **true**;
- revalidation lookup uses index: **true**;
- final clean-rebuild parity: **true**.

This is important because the control still converges correctly after maintenance. The defect is specifically a read-safety interval while canonical state is ahead of derived state.

## Locality

The fixed two-intent topology workload was run while unrelated entity cardinality increased:

| Entities | First recovery | Second recovery | Total recovery | Full rebuild |
|---:|---:|---:|---:|---:|
| 100 | 75 | 36 | **111** | 1,389 |
| 1,000 | 75 | 36 | **111** | 13,989 |
| 10,000 | 75 | 36 | **111** | 139,989 |
| 50,000 | 75 | 36 | **111** | 699,989 |

Thus, for this controlled fixed-topology-change workload:

\[
RecoveryWork(2,N)=111
\]

through 50,000 entities, while the reconstruction control grows linearly with total materialized state.

The evidence supports the narrower invariant:

\[
\boxed{
CanonicalVersionGuards
+
PromotionTimeTopologyRevalidation
+
SnapshotConsistentReads
\Rightarrow
NoStaleReadForRevalidatedImpactRegion
}
\]

It does not establish a universal O(1) topology-update claim.

## Reproducibility

The compact machine-readable evidence is `topology_results.json`.

`verify_topology_results.py` reruns the differential control, corrected mechanism, and locality sweep; checks the stale-read control failure, corrected read blocking, clean-rebuild parity, and indexed lookup; then exact-compares stable observed fields against the committed ledger.

## Limitations

This result is intentionally narrow:

- one SQLite database;
- ordered logical intents and one active maintenance intent;
- assertion-subject topology movement followed by an evidence update;
- synthetic oracle assertions;
- read protection expressed at the current subject/predicate context-key level;
- no distributed/multi-database claim;
- no claim yet that new canonical topology can create entirely missing derived nodes.

The last limitation is the next high-value falsification target. Moving or adding an assertion to a subject/predicate for which no derived materialization exists may require **local derived-node creation**, not merely invalidation/rebuild of existing nodes.
