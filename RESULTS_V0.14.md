# Durable Infinite Context — v0.14 Subject-Local Fan-Out and History

## Question

v0.13 established subject-wide profile semantics:

```text
profile(subject) = aggregate(latest assertion for every live predicate of subject)
```

That repaired semantic identity, but its implementation obtained the current predicate set by scanning every assertion owned by the subject and then choosing the latest row for each predicate.

Let:

- `P` = number of live predicates represented in the subject profile;
- `H` = historical assertion depth per predicate;
- `N` = unrelated global entity cardinality.

The v0.13 profile path could therefore perform work proportional to `P * H`, even though only `P` current assertions can affect the current profile.

The v0.14 falsification target was:

```text
Can current-profile maintenance remain proportional to the true live subject schema P,
while becoming independent of irrelevant subject history H and unrelated global memory N?
```

## First principle

A subject-wide profile that explicitly contains `P` predicates has an output lower bound proportional to `P`. Therefore growth with `P` is not automatically a defect.

Historical versions that cannot affect the current profile are different. Current-state repair should not repeatedly rescan them.

The revised target is:

```text
CurrentProfileRepair = O(P), not O(P * H)
```

for the controlled current-profile workload.

## Control

The v0.13 `PredicateSchemaAwareStore` remains the control. It scans assertions for the affected subject through the `(subject,predicate,recorded_seq,id)` index, then selects latest-per-predicate.

At `P=8`, increasing `H` produced:

| H | v0.13 recovery work | Canonical rows read |
|---:|---:|---:|
| 1 | 87 | 27 |
| 2 | 95 | 35 |
| 4 | 111 | 51 |
| 8 | 143 | 83 |
| 16 | 207 | 147 |
| 32 | 335 | 275 |
| 64 | 591 | 531 |

This is the negative result v0.14 was designed to remove.

## Mechanism

v0.14 introduces a transactional current-head index:

```text
subject_predicate_heads(subject_id, predicate) -> assertion_id
```

Assertion mutation refreshes only the old/new `(subject,predicate)` keys atomically with the canonical mutation. Refresh uses the existing indexed assertion ordering to select the latest surviving assertion for that key.

Profile reconstruction then performs:

1. indexed lookup of the affected subject's live head rows;
2. one primary-key assertion read per live predicate;
3. evidence lookup for those current assertions only;
4. the existing subject-wide profile reconstruction.

The bootstrap/full-index rebuild path is intentionally excluded from measured local recovery; it is an initialization/recovery-oracle path, not the incremental mechanism under test.

## Source evidence

Provisional source run:

- GitHub Actions run: `34054249965`
- head: `e62d396b93f5d0002ea6ec066a28833b1ff47809`
- artifact: `v0.14-subject-fanout-results`
- artifact digest: `sha256:988b9637541e165bd75105741fe381bd03f3143b24cb5cd908f3e44699874f7f`

The artifact was committed as `subject_fanout_results.json`. `verify_subject_fanout_results.py` reruns the experiment, checks the semantic/index/scaling invariants, exact-compares the observed object with the committed ledger, and restores the committed file afterward.

## Discriminating case

At `N=128, P=8, H=16`:

| Path | Recovery work | Canonical rows read | Head work | Rebuild parity |
|---|---:|---:|---:|---|
| v0.13 control | 207 | 147 | 0 | true |
| v0.14 head index | **95** | **27** | 8 | true |

The corrected path reads exactly eight current head rows—one per live predicate.

## History-depth sweep

With `P=8` fixed:

| H | v0.13 work | v0.14 work | v0.14 canonical rows read | Full rebuild |
|---:|---:|---:|---:|---:|
| 1 | 87 | **95** | 27 | 1,883 |
| 2 | 95 | **95** | 27 | 1,907 |
| 4 | 111 | **95** | 27 | 1,955 |
| 8 | 143 | **95** | 27 | 2,051 |
| 16 | 207 | **95** | 27 | 2,243 |
| 32 | 335 | **95** | 27 | 2,627 |
| 64 | 591 | **95** | 27 | 3,395 |

This is the principal v0.14 result:

```text
for fixed live predicate set P, current-profile repair is independent of historical depth H
```

under the controlled workload.

The small-`H` negative trade-off is preserved: at `H=1`, the head-index path costs 95 versus 87 for direct subject-history scanning. As with v0.5 indexing, materialization/indexing is not universally free; it earns its complexity when historical depth becomes nontrivial or latency predictability matters.

## Live-predicate fan-out sweep

With `H=8` fixed:

| P | Recovery work | Head rows read | Canonical rows read | Full rebuild |
|---:|---:|---:|---:|---:|
| 1 | 39 | 1 | 6 | 1,813 |
| 2 | 47 | 2 | 9 | 1,847 |
| 4 | 63 | 4 | 15 | 1,915 |
| 8 | 95 | 8 | 27 | 2,051 |
| 16 | 159 | 16 | 51 | 2,323 |
| 32 | 287 | 32 | 99 | 2,867 |

The work grows with `P`, as expected. This is not treated as a failure because the profile itself contains the live predicate set and associated evidence payloads.

The surviving scaling statement is therefore not `O(1)` subject repair. It is:

```text
RecoveryCost(subject) ~= f(live semantic footprint of subject),
not f(lifetime subject history or unrelated global memory)
```

## Unrelated global-cardinality sweep

With `P=8, H=8` fixed:

| N | Recovery work | Full rebuild |
|---:|---:|---:|
| 100 | **95** | 1,659 |
| 1,000 | **95** | 14,259 |
| 10,000 | **95** | 140,259 |
| 50,000 | **95** | 700,259 |

This preserves the earlier locality result with a richer subject-local workload.

## Head lifecycle / fallback

A separate mutation test validates that the head table is not merely a read cache.

The controlled sequence is:

1. create historical `deadline` assertions;
2. add another live predicate;
3. move the current `deadline` assertion to `renamed_deadline`;
4. require `deadline` to fall back to the latest historical assertion;
5. require `renamed_deadline` to point at the moved assertion;
6. delete that moved assertion;
7. require the renamed head to disappear while the historical `deadline` head survives.

Observed move:

- base recovery work: 89;
- head maintenance work: 9;
- total: 98;
- clean-rebuild parity: true;
- canonical head-index parity: true.

After deletion:

- `deadline` still points to the historical fallback;
- `renamed_deadline` is absent;
- profile predicates are `[deadline, facet_001]`;
- clean-rebuild parity: true;
- head-index parity: true.

Both head lookup and head refresh query plans are explicitly asserted to use indexes.

## Architectural revision

v0.14 sharpens the maintenance locality invariant:

```text
Maintenance locality should be measured against the minimal semantic footprint
required by the derived output, not merely against total database cardinality.
```

For subject-wide current profiles:

```text
TrueAffectedSemanticFootprint ~= live predicates P
```

while historical versions `H` are durable evidence/history but are not part of the current profile's minimal reconstruction set.

A corresponding mechanism is:

```text
Canonical assertion history
    -> transactional current-head materialization
    -> subject-wide current profile
```

This is another selective-materialization result: the current-head index earns write complexity by preventing repeated historical reconstruction on current-state reads/repairs.

## Limitations / non-claims

The evidence is deliberately scoped:

- one SQLite database;
- synthetic oracle assertions;
- current-profile semantics only;
- controlled predicate/evidence payload structure;
- head-index bootstrap/rebuild excluded from local-recovery measurements;
- no claim of constant work in live predicate fan-out `P`;
- no claim yet for extremely high evidence fan-out per predicate;
- no historical-profile query optimization claim;
- no distributed index-consistency claim;
- no production latency/dollar benchmark;
- no strong agentic-RAG superiority result.

## Next falsification target

v0.14 makes lifetime **history depth** irrelevant to current subject-profile repair, but the current profile still scales with the actual live semantic footprint.

The next useful question is whether that footprint itself can be reconstructed selectively for a task rather than always materializing/rebuilding the whole subject-wide profile:

```text
Does every subject mutation require rebuilding all P profile facets,
or can dependency-aware facet materialization make work proportional to the changed/requested subset K << P?
```

This would test whether `profile(subject)` should remain one monolithic materialization or become a compositional view over predicate/facet-level materializations.
