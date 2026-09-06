# v0.13 — Subject-Wide Predicate Schema

## Problem

v0.12 established that local maintenance must create missing derived outputs when canonical topology grows. It still operated under a controlled `deadline` schema.

A deeper identity inconsistency remained:

```text
profile_node(subject)
```

has subject-only identity, while the persistent profile rebuild path reconstructed that node by querying the hard-coded `deadline` predicate.

That allows a predicate replacement such as:

```text
deadline -> launch_date
```

to leave canonical truth and predicate-specific state/support/context correct while deleting the subject profile. Every surviving derived node can still be `fresh`, so neither lifecycle freshness nor missing-node completeness detects the semantic mismatch.

## First principle

A derived node's identity constrains the semantic scope that node may represent.

If predicate is absent from the identity:

```text
profile:<subject>
```

then the profile cannot coherently mean “the subject's deadline profile.” Its meaning must be subject-wide, or the node identity itself must change.

The tested hypothesis was therefore:

```text
Profile(subject)
  = deterministic aggregate of the latest assertion for every live predicate of subject
```

while state, support, and context remain predicate-specific.

This is also consistent with the existing scalable planner, which already treats a subject's predicate set as a plural subject-level property.

## Falsified v0.12 control

At N=64, replacing the controlled assertion predicate from `deadline` to `launch_date` under the v0.12 semantics produced:

- canonical predicate changed: **true**;
- new `launch_date` context present: **true**;
- old `deadline` context retired: **true**;
- subject profile present: **false**;
- surviving subject derived rows: **3**;
- all surviving derived nodes fresh: **true**;
- clean-rebuild parity: **false**.

The control trace reported **66 logical operations**.

This falsifies the stronger claim that:

```text
Freshness + MaterializationCompleteness
```

is sufficient for derived correctness.

## Mechanism

`PredicateSchemaAwareStore` keeps v0.12's local missing-output mechanism and changes only subject-profile semantics.

For one subject, profile reconstruction:

1. uses the existing `(subject_id, predicate, recorded_seq, id)` assertion index;
2. reads only that subject's canonical assertions;
3. selects the latest assertion per live predicate;
4. gathers their evidence payloads;
5. writes exactly one deterministic subject profile;
6. attaches the profile to all selected assertion/evidence dependencies.

State/support/context remain keyed by `(subject, predicate, scope)`.

The clean-rebuild oracle was correspondingly corrected to emit exactly one profile per subject plus one state/support/context triplet per live predicate.

## Evidence

The strengthened evidence source was GitHub Actions run `34046258763`, head `c955c6e9347af88cc5be9f51aff6e7c0c324efab`. Its v0.13 artifact digest was:

```text
sha256:72453299c4c462685467116e62e936ee5f00f8e6f3132465e11ba207505a3aa8
```

The artifact is committed as `predicate_schema_results.json` and replayed by `verify_predicate_schema_results.py`.

### Predicate replacement

At N=64:

- canonical predicate: `launch_date`;
- new context present: **true**;
- old context retired: **true**;
- profile predicates: `["launch_date"]`;
- subject derived rows: **4**;
- clean-rebuild parity: **true**;
- all derived nodes fresh: **true**;
- indexed subject-profile lookup: **true**;
- recovery work: **70**.

Trace:

| Component | Work |
|---|---:|
| journal reads | 1 |
| journal writes | 4 |
| canonical mutations | 1 |
| affected discovered | 7 |
| invalidated nodes | 7 |
| canonical rows read | 8 |
| derived rows read | 15 |
| derived rows written | 10 |
| edge mutations | 14 |
| retired nodes | 3 |
| **logical work** | **70** |

### Two simultaneously live predicates

The experiment adds a second `launch_date` assertion without removing the original `deadline` assertion.

Result:

- profile predicates: `["deadline", "launch_date"]`;
- one subject profile + two state/support/context triplets = **7 derived rows**;
- both predicate-specific contexts present;
- queue: 2 done, 0 conflicts;
- clean-rebuild parity: **true**;
- all derived nodes fresh: **true**;
- total two-intent recovery work: **59**.

This demonstrates that the subject profile is genuinely plural rather than merely substituting a different hard-coded predicate.

### Predicate removal symmetry

After creating the two-predicate subject, the experiment deletes the original `deadline` assertion.

Result:

- `deadline` assertion absent;
- `deadline` context absent;
- `launch_date` assertion/context remain present;
- profile predicates become `["launch_date"]`;
- subject derived rows return to **4**;
- clean-rebuild parity: **true**;
- all derived nodes fresh: **true**;
- removal recovery work: **41**.

This closes the add/remove lifecycle symmetry for the controlled two-predicate case.

## Locality sweep

For a fixed one-predicate replacement, unrelated entity cardinality was varied while the affected subject-local schema remained fixed:

| Entities | Recovery work | Full rebuild |
|---:|---:|---:|
| 100 | **70** | 1,400 |
| 1,000 | **70** | 14,000 |
| 10,000 | **70** | 140,000 |
| 50,000 | **70** | 700,000 |

The complete trace is identical across these locality rows.

Therefore the supported claim is:

```text
For fixed subject-local predicate/history size,
subject-wide profile repair is independent of unrelated total-memory cardinality.
```

It is **not** a claim that profile reconstruction is constant in subject-local predicate count or subject-local assertion history.

## Architectural revision

The surviving derived-correctness invariant is now:

```text
DerivedCorrectness
  = Freshness
  + MaterializationCompleteness
  + SemanticIdentityConsistency
```

with:

```text
NodeIdentity(x) -> StableSemanticScope(x)
```

For the current profile node:

```text
Identity = subject
Semantic scope = subject-wide aggregate
```

For state/support/context:

```text
Identity = subject + predicate + scope
Semantic scope = predicate-specific
```

## What this does not establish

v0.13 does not establish:

- arbitrary schema or ontology evolution;
- compatibility rules between heterogeneous predicate types;
- migrations that change node identity itself;
- bounded work as one subject accumulates many predicates;
- bounded work as one subject accumulates deep history per predicate;
- real extraction/schema induction;
- distributed or multi-database consistency;
- strong agentic-RAG superiority.

Assertions remain synthetic/oracle-provided in this experiment.

## Next falsification target

The next unresolved scaling dimension is now **subject-local fan-out**, not unrelated global memory.

A subject-wide aggregate necessarily touches some representation of the subject's live predicates. The next experiment should therefore vary:

```text
P = number of live predicates on one subject
H = assertion history depth per predicate
```

while holding unrelated total memory fixed and then varying it independently.

The question is:

```text
Can profile/state maintenance remain proportional to the true changed subject-local region,
without rescanning irrelevant history or requiring global work?
```

That is the natural v0.14 falsification boundary.
