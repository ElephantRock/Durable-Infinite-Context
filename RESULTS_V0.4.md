# MFP v0.4 — Non-Oracle Planner Results

## Question

Can the v0.3 multi-address retrieval advantage survive after removing the oracle-resolved query plan while retaining oracle assertions?

v0.4 replaces direct use of hidden `QueryCase.subject_id`, `predicate`, and time constraints with a deterministic planner that receives only the user-visible question plus memory-derived subject profiles.

## Validation

GitHub Actions completed successfully with **21/21 deterministic tests passing**, including five new planner tests.

The benchmark contains **260 cases**:

- 60 unique-identity cases;
- 60 shared-alias cases resolvable by contextual qualifiers;
- 60 irreducibly ambiguous shared-alias cases;
- 80 temporal-resolution cases.

Retrieval budget is fixed at 4.

## Results

### Unique identity

| Distractors per target | Exact inferred plan | Inferred complete support | Oracle complete support |
|---:|---:|---:|---:|
| 0 | 100% | 100% | 100% |
| 10 | 100% | 100% | 100% |
| 100 | 100% | 100% | 100% |

### Shared alias + contextual qualifier

| Distractors per target | Exact inferred plan | Inferred complete support | Oracle complete support |
|---:|---:|---:|---:|
| 1 | 100% | 100% | 100% |
| 10 | 100% | 100% | 100% |
| 100 | 100% | 100% | 100% |

### Irreducibly ambiguous identity

These questions intentionally do not contain enough information to choose among multiple entities sharing the same visible alias and description.

| Distractors per target | Correct abstention | Over-resolution |
|---:|---:|---:|
| 1 | 100% | 0% |
| 10 | 100% | 0% |
| 100 | 100% | 0% |

The correct behavior here is not target recall. The hidden target exists only for evaluation; the natural-language question is underdetermined. v0.4 therefore treats abstention as success rather than rewarding an oracle-only answer.

### Temporal resolution

| Relevant history depth | Exact inferred plan | Inferred complete support | Oracle complete support |
|---:|---:|---:|---:|
| 4 | 100% | 100% | 100% |
| 16 | 100% | 100% | 100% |
| 64 | 100% | 100% | 100% |
| 256 | 100% | 100% | 100% |

Predicate, valid-time, and intent accuracy were 100% on all resolvable benchmark cases.

## Interpretation

Within this controlled workload, the v0.3 addressability result does **not** depend on an oracle query plan. A planner using only question text plus memory-derived identity profiles can recover the required identity/predicate/time constraints and match oracle-planned retrieval when the question contains sufficient information.

The equally important result is the ambiguity behavior:

> When the question does not contain enough information to resolve identity, the planner must preserve that uncertainty rather than manufacture a hard constraint.

This supports the earlier invariant that retrieval uncertainty is distinct from absence and that hard identity constraints are safe only after identity has been resolved with sufficient evidence.

## What this does not prove

The 100% numbers should not be interpreted as production NLP performance. The benchmark is deliberately controlled.

v0.4 still assumes:

- oracle assertions linking evidence to canonical subject IDs;
- controlled predicate vocabulary and synonym rules;
- synthetic question forms and lexical entity descriptors;
- no pronouns, ellipsis, multi-turn references, spelling noise, multilingual aliases, or learned entity resolution;
- no LLM planner;
- no real extraction;
- no genuine agentic-RAG baseline.

There is also an important scalability limitation: the prototype planner currently scores the query against all subject profiles. This is suitable for semantic isolation but is not yet a durable-infinite-context retrieval mechanism at very large entity cardinality.

## Architectural revision

The read path can now be stated more precisely:

```text
Question
  -> infer information need
  -> resolve/abstain on identity
  -> resolve predicate/time
  -> apply trustworthy hard constraints
  -> semantic + lexical ranking
  -> coverage assessment
  -> bounded context
```

Hard constraints should therefore be **conditional outputs of query understanding**, not assumed oracle inputs.

## Next falsification targets

Two uncertainties now dominate:

1. **Planner robustness and scalability** — can identity resolution remain accurate under noisy language without scanning every subject profile?
2. **Multi-hop relational retrieval** — can the system discover task-relevant evidence when no direct identity/predicate lookup reaches the answer?

The current result earns removal of the oracle planner from the controlled direct-retrieval path, but not yet from arbitrary natural-language workloads.
