# v0.5 — Scalable Query Resolution Results

## Research question

v0.4 removed the oracle query planner, but its identity resolver still scored every subject profile on every query:

\[
Cost_{planner}(N_{entities}) \approx O(N_{entities})
\]

v0.5 tests whether query-time identity resolution can preserve planner accuracy and ambiguity calibration while reducing candidate-generation/scoring work to a bounded or strongly sublinear path.

## Mechanism under test

The v0.5 resolver treats query-resolution indexes as rebuildable materializations over canonical evidence/assertions. It uses:

1. exact token postings;
2. separator-fragment postings for stable subparts of compound aliases/descriptors;
3. four-character n-gram postings for typo/noise rescue;
4. a maximum of 32 scored subject candidates;
5. a broad-posting threshold of 128, above which postings are treated as ambiguity signals rather than expanded linearly.

Broad aliases with no narrower discriminating evidence cause abstention rather than arbitrary candidate truncation.

## Benchmark

Entity cardinality:

- 100
- 1,000
- 10,000
- 50,000

Workloads:

- unique exact alias;
- unique alias with a transposition typo;
- shared alias with exact contextual qualifier;
- shared alias with a noisy contextual qualifier;
- irreducibly ambiguous shared alias.

Each cardinality/workload uses 20 deterministic query cases. Assertions remain oracle-provided so the experiment isolates query-resolution/addressability cost.

## Initial result: partial falsification

The first indexed resolver passed exact aliases and ambiguity tests, but the noisy workloads plateaued at approximately:

- 90% exact-plan accuracy;
- 95% candidate recall for N >= 1,000.

The miss was traced to a weakness in four-character n-gram rescue: a transposed compound alias could destroy its rare lexical grams, while repetitive numeric suffix grams could be too broad to identify the target inside the 32-candidate budget.

This result rejected the initial mechanism as sufficient.

## Revision

A separator-fragment posting channel was added. For compound identifiers such as hyphenated/underscored aliases and descriptors, stable subparts are indexed independently and can survive corruption in another fragment.

The benchmark itself was not relaxed.

## Revised result

After the revision, all resolvable workloads reached:

- 100% exact-plan accuracy;
- 100% target candidate recall.

Irreducible ambiguity retained:

- 100% correct abstention;
- 0% over-resolution.

### N = 50,000

| Workload | Exact plan | Candidate recall / abstention | Avg profiles scored | Avg logical work | Work / full scan |
|---|---:|---:|---:|---:|---:|
| Unique exact alias | 100% | 100% recall | 1.00 | 11.0 | 0.00022 |
| Unique noisy alias | 100% | 100% recall | 30.45 | 184.3 | 0.003686 |
| Contextual exact | 100% | 100% recall | 1.00 | 12.0 | 0.00024 |
| Contextual noisy descriptor | 100% | 100% recall | 30.45 | 187.3 | 0.003746 |
| Irreducible ambiguity | 100% plan outcome | 100% abstention | 0.00 | 9.0 | 0.00018 |

The indexed path therefore examined/scored a vanishing fraction of the full subject population as cardinality increased.

### Cost trend

For exact identity/contextual cases, logical query work remained approximately constant from 1,000 through 50,000 entities:

\[
Work_{exact} \approx 11\text{–}12
\]

For noisy aliases/descriptors, it remained bounded around:

\[
Work_{noisy} \approx 169\text{–}187
\]

while full-scan work grows with N.

This supports the intended operational property:

\[
\frac{Cost_{indexed}(q,N)}{N} \rightarrow 0
\]

for the controlled fixed-complexity workloads.

## Important negative result: small-N overhead

At only 100 entities, indexed/fuzzy resolution can cost more logical operations than a direct full-profile scan:

- noisy alias: 364.2 logical operations versus 100 profile scans;
- exact contextual collision: 143 versus 100;
- noisy contextual descriptor: 567.2 versus 100;
- irreducible ambiguity: 141 versus 100.

Therefore the evidence does **not** support using indexed candidate generation universally.

A better architecture is adaptive:

\[
Resolve(q) =
\begin{cases}
FullScanProfiles, & \text{when the candidate universe is small} \\
IndexedCandidateGeneration, & \text{when the candidate universe is large}
\end{cases}
\]

The switching threshold is workload- and implementation-dependent and should be measured rather than hard-coded from this synthetic benchmark.

## Interpretation

v0.5 supports three narrower conclusions:

1. query-time identity resolution does not inherently require a linear scan over lifetime entity cardinality;
2. robust candidate generation benefits from multiple lexical address granularities (token, fragment, fuzzy n-gram), not one representation;
3. ambiguity handling must remain explicit—broad candidate sets should not be silently truncated into a false answer.

It does **not** yet establish production entity linking, production latency, incremental index-maintenance locality, or robustness to arbitrary natural-language noise.

## Next falsification target

The indexed read path now scales in the controlled workload, but its maintenance path is untested.

The next question should therefore be:

> Can the query-resolution materializations be updated incrementally when evidence/assertions change, without rebuilding work proportional to total memory?

That is the natural v0.6 maintenance-locality experiment before layering additional relational retrieval complexity on top of these indexes.
