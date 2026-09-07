# Durable Infinite Context — Minimum Falsifiable Prototype v0.21

This repository is a falsification-first research prototype for **Durable Infinite Context**: a system that can accumulate durable history without requiring lifetime history to fit inside the model context window.

The working target is:

\[
C_t = F(q_t,M_t), \qquad |C_t| \le B
\]

while durable memory may continue to grow. The project is not attempting to build an actually infinite context window. It is testing whether bounded task context can be reconstructed from indefinitely growing durable state with correct revision semantics and tractable operational cost.

## Governing discipline

**Observe → Diagnose → Derive → Hypothesize → Predict → Test → Revise → Engineer**

Architecture is treated as a surviving hypothesis, not as the goal. Negative results and failed mechanisms are retained because they constrain the next design.

## Current surviving architecture

The prototype now contains:

- canonical evidence and revisable assertions with lineage;
- correction/supersession and contested-state reconciliation;
- current/historical valid-time and knowledge-time queries;
- bounded context compilation;
- selective state materialization;
- multi-dimensional addressability and coverage-controlled retrieval;
- non-oracle deterministic query planning over oracle assertions;
- scalable indexed candidate generation;
- incremental addressability maintenance;
- dependency-aware invalidation, reconstruction, and local retirement;
- explicit derived lifecycle states (`fresh`, `invalid`, `rebuilding`);
- crash-safe SQLite WAL persistence with real `SIGKILL` failpoints;
- durable ordered logical intents with optimistic canonical versions;
- snapshot-consistent derived reads;
- promotion-time revalidation of topology-dependent impact metadata;
- local creation of missing required materializations;
- subject-wide profile semantics when profile identity is subject-only;
- transactional `(subject,predicate) -> current assertion` heads so current reconstruction does not rescan historical versions;
- compositional subject profiles whose evidence-bearing state lives in predicate facets;
- normalized indexed `(subject,predicate)` membership instead of a serialized all-predicate profile manifest;
- constant-size subject profile descriptors plus one-snapshot full/selective assembly;
- facet-local stale-read protection during active maintenance;
- machine-readable evidence ledgers with executable replay verifiers;
- a pluggable `AgenticRAGAdapter` seam for the still-required strong baseline comparison.

A useful current decomposition is:

\[
\boxed{
Evidence
+ Assertions
+ SelectiveState
+ Addressability
+ Coverage
+ BoundedCompilation
+ LocalMaintenance
+ DurableRecovery
+ OrderedIntentAdmission
+ TopologyRevalidation
+ MaterializationCompleteness
+ SemanticIdentityConsistency
+ CurrentHeadMaterialization
+ CompositionalProfileAssembly
+ NormalizedPredicateMembership
}
\]

This decomposition remains provisional.

## Deliberate non-claims

The included `evidence_recency_control` is a smoke control, **not** a strong agentic hybrid-RAG baseline. The repository deliberately does not fake an LLM agent.

Current evidence does **not** establish:

- production entity linking or semantic embeddings;
- real extraction accuracy (current maintenance experiments still use oracle assertions);
- distributed, replicated, or multi-database consistency;
- hardware power-loss guarantees beyond the tested SQLite/storage stack;
- arbitrary physical multi-writer database execution;
- production latency or dollar cost;
- cold/archive recovery;
- arbitrary ontology/schema migration;
- constant work as the true live semantic footprint of one subject grows;
- direct operating-system/storage-device page-read locality independent of global memory;
- constant B-tree traversal depth as global indexes grow;
- a crash-safe bounded-migration hash/direct-address replacement for the current B-tree;
- zero-failure bounded-work hash placement under arbitrary collision patterns;
- unlimited adversarial admission with fixed mutation, lookup, and space bounds;
- a durable hybrid overflow path after bounded placement escalation is exhausted;
- constant work for arbitrarily large individual facet values;
- a strong agentic-RAG superiority result.

Real extraction and a genuine strong agentic retrieval baseline remain mandatory before broad superiority claims.

## Milestone ledger

| Version | Falsification target | Main surviving result |
|---|---|---|
| v0.1 | Is persistent state semantically necessary? | No. Assertions-on-demand and persistent state tied under oracle retrieval; state only earned a possible efficiency role. |
| v0.2 | Does materialized state earn write complexity? | Selective materialization reduces repeated current-state reconstruction when read savings justify maintenance. |
| v0.3 | Are similarity and one retrieval channel sufficient? | No. Identity/time are independent address dimensions; adaptive coverage prevents controlled premature closure. |
| v0.4 | Does addressability survive removal of the oracle plan? | In controlled language, 200/200 resolvable cases matched the oracle plan and 60/60 irreducibly ambiguous cases abstained. |
| v0.5 | Can query resolution avoid O(N) subject scans? | Yes after a noisy-alias failure forced fragment addressability; indexed resolution stayed accurate through 50k entities. |
| v0.6 | Can address indexes be maintained locally? | Fixed-local mutations stayed roughly constant while rebuild work grew with total memory; shared evidence scaled with true fan-out. |
| v0.7 | Can multi-layer invalidation/rebuild remain local? | Yes for tested DAGs; a later audit found and removed hidden whole-graph invalid-node discovery. |
| v0.8 | Can interrupted maintenance recover without stale reads? | Initial phase-only handling failed a torn-boundary test; idempotent redo repaired the protocol. |
| v0.9 | Do crash invariants survive real persistence/process death? | SQLite WAL + `synchronous=FULL` passed 33 real-`SIGKILL` cases; fixed-region recovery stayed local through 50k entities. |
| v0.10 | Do multiple durable logical intents preserve conflict/recovery semantics? | Concurrent admission and explicit same-key conflict passed; a snapshot race was found and fixed before merge. |
| v0.11 | Can admission-time impact metadata become stale after earlier topology changes? | Yes. Promotion-time topology revalidation closes the demonstrated stale-read leak. |
| v0.12 | Can canonical growth require derived outputs that do not yet exist? | Yes. Explicit missing-output obligations restore exact materialization completeness. |
| v0.13 | Can subject-only profiles remain correct when predicates change/coexist? | v0.12 loses the profile after `deadline -> launch_date`; subject-wide profile semantics restore exact parity. |
| v0.14 | Can current subject profiles avoid rescanning deep predicate history? | A transactional current-head index removes H-dependence while preserving legitimate P-dependence and global-N logical locality. |
| v0.15 | Can evidence/value maintenance and selective profile assembly scale with changed/requested subset `K` instead of all live predicates `P`? | In logical row/facet operations, yes: maintenance is proportional to `K`, selective facet reads are proportional to `K`, and full assembly remains proportional to `P`. The experiment exposed an `O(P)` serialized manifest. |
| v0.16 | Can selective returned work and predicate-topology deltas avoid touching/rebuilding that `O(P)` manifest? | Yes in the measured SQL-returned/serialized path: a 40-byte descriptor plus normalized indexed membership keeps `K=1` returned bytes fixed across `P=1..64`, and topology add/delete work is P-invariant. But `dbstat` shows the global membership B-tree height grows with N. |
| v0.17 | Can fixed B-tree partitioning make cold point-lookup index-page traversal independent of global `N`? | No. 64-way hash partitioning delays page-height transitions but its maximum shard height still grows at sufficiently large N. Fixed finite B-tree sharding remains `Theta(log_B N)` in page levels. |
| v0.18 | Is conventional bounded-load hashing sufficient to get both expected constant lookup pages and global-N-independent mutation work? | No. Successful lookup stayed at p95=1 page and max=2 in the tested model, but stop-the-world capacity doubling rehashed every prior live row, creating `Theta(N)` mutation spikes. |
| v0.19 | Can a fixed-budget two-generation rehash remove that single-mutation resize spike without unbounded migration fan-out? | Partly. Source migration is bounded at 8 slots/rows per insertion, all tested migrations complete, lookup touches at most two generations, and temporary capacity is 1.5x. But destination linear-probe work still develops an N-growing tail, so total mutation work is not established as globally bounded. |
| v0.20 | Can placement itself have a finite mutation-work cap under ordinary growth and controlled collisions? | Yes as a bounded-work contract: a 4-slot, two-choice cuckoo candidate with 32 kicks and an 8-entry stash never exceeds 200 modeled slot operations and stays failure-free in ordinary growth. But a concentrated two-bucket domain admits only 16 keys; the 17th fails explicitly, exposing the availability trade-off. |
| v0.21 | Can local placement failure escape through a fixed finite sequence of bounded domains without recreating unbounded locality? | Yes as bounded escalation: with `D` domains, concentrated capacity is `16D`, first failure is `16D+1`, mutation work is bounded by `200D`, and missing-key lookup by `3D` logical pages. But every fixed `D` still has finite admission capacity and pays proportional reserved space. |

Detailed narratives and machine-readable evidence live in `RESULTS_V0.*.md` and `*_results.json`.

## Selected validated measurements

### Durable recovery and topology correctness

The persistent path is already hardened independently of the experimental hash-placement work:

- v0.9 passed **33 real `SIGKILL` cases** under SQLite WAL + `synchronous=FULL`;
- fixed-region recovery stayed at **28 logical operations** from 100 through 50,000 entities while full reconstruction grew from 1,387 to 699,987;
- v0.10 preserved ordered multi-intent conflict/recovery semantics;
- v0.11 added promotion-time topology revalidation after exposing admission-time stale impact metadata;
- v0.12 made missing required outputs explicit rather than assuming materializations already exist;
- v0.13 corrected subject-profile semantic identity;
- v0.14 removed historical-depth dependence from current predicate reconstruction with transactional heads.

See the corresponding `RESULTS_V0.*.md` files and executable verifiers for exact workloads.

### v0.15–v0.16 selective profile locality

v0.15 decomposes the subject profile into predicate facets. At fixed `K=1,H=8,N=128`, maintenance stays **27 logical operations** while `P` grows from 1 to 64; full assembly still grows with legitimate live predicate fan-out. The experiment also exposed that the serialized predicate manifest grows from **66 to 822 bytes**.

v0.16 replaces that manifest with normalized membership and a **40-byte subject descriptor**. For fixed `K=1`, selective SQL payload remains **270 bytes** and measured VM steps remain **60** across `P=1..64`, while full returned payload grows with the actual output. Predicate topology add/delete work becomes P-invariant in the measured path.

The remaining caveat is physical lookup depth: `dbstat` shows the membership B-tree height grows with global N, so a single VM `Seek` is not evidence of constant physical page traversal.

### v0.17 B-tree page locality

With 4096-byte pages, global B-tree height grows from 2 to 3 over the tested N range. A fixed 64-way partition delays the transition but its maximum shard height also eventually grows:

| Membership rows `N` | Global height | 64-way max shard height |
|---:|---:|---:|
| 1,000 | 2 | 1 |
| 10,000 | 2 | 2 |
| 50,000 | 3 | 2 |
| 250,000 | 3 | 2 |
| 1,000,000 | 3 | 3 |

For fixed finite shard count `S`, the comparison-index class remains:

\[
\boxed{AddressLookupPages=\Theta(\log_B(N/S))=\Theta(\log_B N)}
\]

### v0.18–v0.19 hash resize locality

v0.18 shows that expected constant-page successful lookup is not enough. Conventional capacity doubling produces largest single resize migrations:

`512, 2,048, 8,192, 32,768, 131,072` rows

at checkpoints `N={1k,4k,16k,64k,256k}`.

v0.19 spreads that migration across insertions. Every insertion scans at most **8 source slots** and copies at most **8 old rows**, all 12 migrations complete, lookup touches at most two generations, and temporary capacity amplification is **1.5x**. But total mutation slot-work maxima still grow:

`26, 26, 35, 39, 50`

because destination linear probing retains an unbounded collision tail.

### v0.20 bounded placement locality

The fixed two-choice bucketized cuckoo candidate uses 4 slots per bucket, at most 32 relocations, and an 8-entry stash.

| Membership rows `N` | Linear max insert probes | Cuckoo failures | Cuckoo max mutation work | Cuckoo lookup page max |
|---:|---:|---:|---:|---:|
| 1,000 | 11 | 0 | 17 | 2 |
| 4,000 | 19 | 0 | 17 | 2 |
| 16,000 | 21 | 0 | 22 | 2 |
| 64,000 | 31 | 0 | 27 | 2 |
| 256,000 | 34 | 0 | 30 | 2 |

The modeled worst-case insertion contract is **200 slot operations**. Controlled collisions force every stress key into the same two buckets. The candidate admits 16 such keys and rejects the 17th at exactly the bound while preserving all prior admissions.

\[
\boxed{BoundedPlacementWork \neq GuaranteedInsertionAvailability}
\]

### v0.21 bounded placement escape

v0.21 composes a fixed finite sequence of independent v0.20 domains. Ordinary growth reserves four domains but never leaves the first domain in the tested workload: insertion failures remain zero, maximum attempted domains remain one, maximum lookup pages remain two, and observed mutation maxima remain `17,17,22,27,30` across `N={1k,4k,16k,64k,256k}`.

Concentrated collision stress reproduces the fixed trade-off exactly:

| Domains `D` | Concentrated capacity | First failure | Max mutation work | Missing lookup pages | Reserved capacity |
|---:|---:|---:|---:|---:|---:|
| 1 | 16 | 17 | 200 | 3 | 1x |
| 2 | 32 | 33 | 400 | 6 | 2x |
| 4 | 64 | 65 | 800 | 12 | 4x |
| 8 | 128 | 129 | 1,600 | 24 | 8x |

Every admitted key remains retrievable after later failed insertions.

For fixed finite `D`:

\[
\boxed{
\begin{aligned}
ConcentratedCapacity(D)&=16D,\\
MutationWorkCap(D)&=200D,\\
MissingLookupPageCap(D)&=3D,\\
ReservedSpaceAmplification(D)&=D.
\end{aligned}
}
\]

This is a real bounded escape mechanism, but not an unlimited-admission solution. Letting `D` grow without bound would simply move non-locality into mutation work, lookup fan-out, and reserved space.

See `RESULTS_V0.21.md`, `bounded_escape_results.json`, and `verify_bounded_escape_results.py`.

## Reproducing the hardened path

```bash
python -m unittest discover -s tests -v
python run_planner_experiment.py
python run_scalable_planner_experiment.py
python run_maintenance_experiment.py
python run_compositional_profile_experiment.py
python run_normalized_membership_experiment.py
python run_page_locality_experiment.py
python run_hash_resize_experiment.py
python run_incremental_hash_experiment.py
python run_bounded_placement_experiment.py
python run_bounded_escape_experiment.py
python verify_scanfree_cascade_results.py
python verify_recovery_results.py
python verify_process_recovery_results.py
python verify_multi_intent_results.py
python verify_topology_results.py
python verify_growth_results.py
python verify_predicate_schema_results.py
python verify_subject_fanout_results.py
python verify_compositional_profile_results.py
python verify_normalized_membership_results.py
python verify_page_locality_results.py
python verify_hash_resize_results.py
python verify_incremental_hash_results.py
python verify_bounded_placement_results.py
python verify_bounded_escape_results.py
```

CI runs this chain on pull requests and uploads the hardened evidence ledgers as artifacts.

## Current architectural hypothesis

```text
Canonical evidence
  -> revisable assertion history
  -> transactional current heads where repeated current reconstruction earns them
  -> normalized current predicate membership
  -> predicate-specific evidence-bearing facets
  -> constant-size subject profile descriptor / compositional logical profile
  -> selectively materialized state / derived views

Question
  -> ambiguity-aware resolution
  -> indexed candidate generation
  -> justified hard constraints
  -> coverage-controlled retrieval
  -> requested semantic subset
  -> indexed membership validation with logarithmic B-tree page depth
  -> one-snapshot facet assembly
  -> bounded context compilation

Canonical mutation
  -> durable ordered intent
  -> write-key conflict validation
  -> promotion-time topology-impact revalidation
  -> local dependency invalidation
  -> derive missing-output obligations
  -> create required absent materializations
  -> reconstruct according to stable semantic identity
  -> update/reuse current-head materializations
  -> synchronize only affected membership rows
  -> repair only affected evidence-bearing facets when topology is unchanged
  -> maintain constant-size profile descriptor lifecycle
  -> selective retirement
  -> crash-safe completion
```

The v0.18–v0.21 hash/cuckoo structures remain **experimental alternatives**, not replacements for the production candidate above.

Ten distinctions are now central:

> Memory is durable state. Context is a bounded compiled artifact reconstructed for a task.

> Derived correctness requires freshness, completeness, and consistency between node identity and semantic scope.

> Maintenance locality should be judged against the true semantic footprint required by the output, not against total database size or lifetime history.

> Counting one row as one operation is not enough to prove locality when that row's serialized size grows with semantic fan-out.

> Counting one indexed `Seek` as one VM operation is not enough to prove physical locality when the underlying B-tree height grows with global memory.

> Fixed partitioning of a comparison index changes constants and thresholds, not the `Theta(log_B N)` lookup class.

> Expected constant lookup does not establish system locality if resize can charge `Theta(N)` migration work to one logical mutation.

> Bounding migration scheduling does not bound total mutation work when the placement primitive itself has an unbounded collision/probe tail.

> Bounding placement work does not guarantee insertion availability; finite local capacity requires an explicit failure/escape policy.

> Finite bounded escalation raises the admission threshold only by paying proportional mutation, lookup, and space bounds; unlimited admission requires an explicit exceptional path.

## Next falsification target — explicit rare overflow

v0.21 shows that fixed finite domain escalation is internally coherent but cannot provide unlimited adversarial admission without abandoning its fixed locality contract.

The next question is therefore:

\[
\boxed{
Can a bounded common placement path coexist with an explicit rare overflow path
without contaminating common-path locality or hiding unbounded work?
}
\]

A v0.22 experiment should keep the bounded common path fixed and route only exhausted placements to an explicit comparison-index or similarly durable exceptional structure. It must measure:

- overflow incidence under ordinary and controlled collision workloads;
- common-path mutation and lookup locality with overflow present;
- overflow mutation and lookup cost as overflow cardinality grows;
- missing-key lookup fan-out;
- whether overflow state can be identified directly without an unbounded scan;
- total space amplification;
- exact preservation of admitted membership semantics.

A logarithmic exceptional path is acceptable evidence if it is explicit, rare under the tested common workload, and isolated from the bounded common path. The project should prefer that honest hybrid over a false claim of universally constant work.

Only after the admission policy survives this test should the experimental hash path be integrated with incremental migration and subjected to persistent crash/restart and stale-read falsification.
