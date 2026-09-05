# v0.3 Results — Selective Addressability and Coverage Control

## Scope

v0.3 removes oracle retrieval while deliberately retaining:

- oracle assertions/extraction;
- an oracle-resolved query plan containing entity ID, predicate, and requested valid/knowledge time.

This isolates the addressability mechanism. It does **not** test natural-language entity resolution or planner accuracy yet.

The text channels are deterministic offline baselines:

- `semantic_only`: concept-normalized TF-IDF intended only as a reproducible paraphrase-aware channel;
- `lexical_only`: ordinary lexical TF-IDF;
- `hybrid_text`: weighted union of the two;
- `planned_multi_address`: hard identity/predicate/time constraints followed by hybrid text ranking.

The concept channel is not claimed to represent a production embedding model.

All retrieval modes use a fixed result budget of 4 in the selective-addressability benchmark.

## 1. Semantic saturation

Each target has a unique visible alias. Distractors are semantic paraphrases about other aliases. The concept channel deliberately removes entity-like aliases, while lexical retrieval retains them.

Complete support recall:

| Distractors / target | Semantic only | Lexical only | Hybrid text | Planned multi-address |
|---:|---:|---:|---:|---:|
| 0 | 0.20 | 1.00 | 1.00 | 1.00 |
| 10 | 0.00 | 1.00 | 1.00 | 1.00 |
| 100 | 0.00 | 1.00 | 1.00 | 1.00 |
| 500 | 0.00 | 1.00 | 1.00 | 1.00 |
| 1,000 | 0.00 | 1.00 | 1.00 | 1.00 |

Interpretation: semantic/concept similarity alone loses the discriminating identity signal. A lexical channel is sufficient in this workload because the visible alias is unique. This supports **plural retrieval channels**, but does not yet demonstrate a need for structured identity.

At 1,000 distractors/target, the text-only modes compare against a 20,020-document region in this implementation; the planned path reduces the candidate region to one document before similarity scoring.

## 2. Identity collision

All entities deliberately share the same visible alias and near-identical text. Only the oracle-resolved structured entity ID distinguishes the target.

Complete support recall:

| Distractors / target | Semantic only | Lexical only | Hybrid text | Planned multi-address |
|---:|---:|---:|---:|---:|
| 0 | 0.20 | 0.20 | 0.20 | 1.00 |
| 10 | 0.00 | 0.00 | 0.00 | 1.00 |
| 100 | 0.00 | 0.00 | 0.00 | 1.00 |
| 500 | 0.00 | 0.00 | 0.00 | 1.00 |
| 1,000 | 0.00 | 0.00 | 0.00 | 1.00 |

Interpretation: once the visible text is genuinely ambiguous, adding more text-ranking sophistication cannot recover information that the text does not contain. Structured identity addressability succeeds **conditional on correct entity resolution by the planner**.

This is not evidence that entity resolution itself is solved. That remains a later falsification test.

## 3. Temporal disambiguation

Each entity has a timeline whose evidence payloads are intentionally textually indistinguishable with respect to time. Valid-time metadata is the discriminating address.

Complete support recall:

| History length | Semantic only | Lexical only | Hybrid text | Planned multi-address |
|---:|---:|---:|---:|---:|
| 4 | 0.05 | 0.05 | 0.05 | 1.00 |
| 16 | 0.00 | 0.00 | 0.00 | 1.00 |
| 64 | 0.00 | 0.00 | 0.00 | 1.00 |
| 256 | 0.00 | 0.00 | 0.00 | 1.00 |

At history length 256 over 20 entities, text-only retrieval compares against 5,120 documents, while the entity/predicate/valid-time constrained path reduces the active candidate region to one.

Interpretation: valid time behaves as an independent address dimension. Semantic or lexical similarity cannot recover a distinction intentionally absent from content.

## 4. Coverage control

The coverage test uses 400 queries where one retrieved item is structurally insufficient:

- correction/transition relation-classification tasks require both linked assertions;
- contested-state tasks require multiple incompatible assertions.

Results:

| Strategy | Coverage rate |
|---|---:|
| Fixed budget = 1 | 0.00 |
| Adaptive expansion, initial budget = 1 | 1.00 |

Adaptive search required:

- average rounds: **2.0**;
- average returned evidence records: **2.0**.

Thus the controlled premature-closure rate falls from 1.00 to 0.00 when the retrieval controller validates an explicit task obligation and expands once.

This test validates the controller mechanism only; the obligations are simple and deterministic.

## What v0.3 supports

The evidence now supports a narrower retrieval hypothesis:

1. **Relevance is multi-dimensional.** Content similarity, lexical identity, structured identity, and time can independently determine addressability.
2. **Hard constraints should precede soft ranking when trustworthy structured constraints are available.** In these workloads, they reduce the candidate region dramatically while preserving recall.
3. **Retrieval sufficiency is distinct from ranking quality.** A high-ranked single result can still be structurally insufficient for correction/conflict tasks.
4. **Adaptive expansion can prevent trivial premature closure** when coverage obligations are explicit.

## What v0.3 does not support yet

The following remain unproven:

- natural-language entity resolution;
- planner extraction of identity/predicate/time constraints;
- production embedding retrieval;
- approximate-nearest-neighbor behavior;
- retrieval under noisy or missing metadata;
- multi-hop relational discovery;
- learned coverage assessment;
- a genuine LLM-driven agentic hybrid-RAG comparison.

The strongest result in v0.3 depends on an oracle-resolved query plan. Therefore it establishes the **value of address dimensions once known**, not the ability to infer those dimensions reliably from user language.

## Architectural revision

The current evidence favors:

```text
Evidence -> Assertions
              |
              +-> selectively materialized state

Query
 -> resolve constraints
 -> apply hard identity / scope / time constraints
 -> use plural soft retrieval channels
 -> assess coverage
 -> expand if necessary
 -> compile bounded context
```

The next discriminating experiment should remove the oracle planner while keeping oracle assertions. That isolates **query understanding / entity resolution** before extraction noise is introduced.

A second branch should add multi-hop relational retrieval, because v0.3 only tests direct addressability.
