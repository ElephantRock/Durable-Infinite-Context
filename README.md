# Durable Infinite Context — Minimum Falsifiable Prototype v0.20

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
- a bounded escape path after local placement failure;
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

Detailed narratives and machine-readable evidence live in `RESULTS_V0.*.md` and `*_results.json`.

## Selected validated measurements

### v0.9 real process-crash recovery

Three mutation classes across eleven failpoints (**33 real SIGKILL cases**) pass. In the strongest fixed-region case, recovery remains **28 logical operations** from 100 through 50,000 entities while full reconstruction grows from 1,387 to 699,987.

### v0.10 durable multi-intent concurrency

For the fixed three-intent crash/recovery workload:

| Entities | Recovery work | Full rebuild |
|---:|---:|---:|
| 100 | **106** | 1,387 |
| 1,000 | **106** | 13,987 |
| 10,000 | **106** | 139,987 |
| 50,000 | **106** | 699,987 |

### v0.11 topology-dependent revalidation

Admission-time topology-derived impact can become stale after an earlier intent changes canonical topology. Revalidating that impact at promotion restores read protection. Fixed two-intent recovery stays **111** through 50k unrelated entities.

### v0.12 local topology growth

Canonical truth can move to a previously unmaterialized subject while every existing derived node remains fresh. Explicit missing-output obligations restore completeness. Corrected recovery work is **76** through 50k unrelated entities; the earlier 72 figure omitted four deterministic existence probes and is retained as an instrumentation failure.

### v0.13 semantic identity consistency

A subject-only profile cannot coherently mean “deadline profile.” The corrected semantics are:

\[
Profile(subject)=Aggregate(CurrentAssertions(subject))
\]

State/support/context remain predicate-specific. Predicate replacement recovery stays **70** through 50k unrelated entities with fixed local predicate/history size.

### v0.14 subject-local fan-out and history

Let:

\[
P=\text{live predicates represented by the subject profile}
\]

\[
H=\text{historical assertion depth per predicate}
\]

The v0.13 control rescans subject history. At `P=8`:

| H | v0.13 work | v0.14 work |
|---:|---:|---:|
| 1 | 87 | **95** |
| 2 | 95 | **95** |
| 4 | 111 | **95** |
| 8 | 143 | **95** |
| 16 | 207 | **95** |
| 32 | 335 | **95** |
| 64 | 591 | **95** |

v0.14 maintains a transactional current head per `(subject,predicate)` and reconstructs the profile from one current assertion per live predicate.

The small-history trade-off is preserved: at `H=1`, indexing costs 95 versus 87 for direct scanning. The index earns its complexity as history deepens or predictable current-state cost matters.

True live predicate fan-out remains visible:

| P | Recovery work |
|---:|---:|
| 1 | 39 |
| 2 | 47 |
| 4 | 63 |
| 8 | 95 |
| 16 | 159 |
| 32 | 287 |

With fixed `P=8,H=8`, unrelated global cardinality remains irrelevant to the logical recovery count:

| Entities | Recovery work | Full rebuild |
|---:|---:|---:|
| 100 | **95** | 1,659 |
| 1,000 | **95** | 14,259 |
| 10,000 | **95** | 140,259 |
| 50,000 | **95** | 700,259 |

### v0.15 compositional profile facets

Let:

\[
K=\text{changed/requested profile facets}, \qquad K\le P
\]

v0.15 persists a subject predicate manifest and reuses predicate-specific support materializations as evidence-bearing facets. The first CI run failed exact cross-version equivalence because the assembled Python representation used tuples where v0.14 persisted JSON used lists; that interface mismatch was corrected and regression-tested rather than normalized away.

At fixed `K=1,H=8,N=128`:

| P | v0.14 maintenance | v0.15 maintenance | Partial logical assembly | Full logical assembly | Manifest bytes |
|---:|---:|---:|---:|---:|---:|
| 1 | 39 | **27** | **3** | 3 | 66 |
| 2 | 47 | **27** | **3** | 4 | 78 |
| 4 | 63 | **27** | **3** | 6 | 102 |
| 8 | 95 | **27** | **3** | 10 | 150 |
| 16 | 159 | **27** | **3** | 18 | 246 |
| 32 | 287 | **27** | **3** | 34 | 438 |
| 64 | 543 | **27** | **3** | 66 | 822 |

At fixed `P=32`:

| K | v0.14 maintenance | v0.15 maintenance | Partial logical assembly | Full logical assembly |
|---:|---:|---:|---:|---:|
| 1 | 287 | **27** | 3 | 34 |
| 2 | 574 | **54** | 4 | 34 |
| 4 | 1,148 | **108** | 6 | 34 |
| 8 | 2,296 | **216** | 10 | 34 |
| 16 | 4,592 | **432** | 18 | 34 |

The evidence supports logical subset locality, but the manifest grows from 66 to 822 serialized bytes as `P` grows from 1 to 64. That failure motivates v0.16.

See `RESULTS_V0.15.md`, `compositional_profile_results.json`, and `verify_compositional_profile_results.py`.

### v0.16 normalized predicate membership

v0.16 replaces the serialized predicate manifest with a **40-byte subject descriptor** and normalized indexed membership rows.

At fixed `K=1,H=8,N=128`:

| P | v0.15 manifest bytes | v0.16 descriptor bytes | selective SQL payload bytes | selective VM steps | full SQL payload bytes |
|---:|---:|---:|---:|---:|---:|
| 1 | 66 | **40** | **270** | **60** | 270 |
| 2 | 78 | **40** | **270** | **60** | 474 |
| 4 | 102 | **40** | **270** | **60** | 882 |
| 8 | 150 | **40** | **270** | **60** | 1,698 |
| 16 | 246 | **40** | **270** | **60** | 3,330 |
| 32 | 438 | **40** | **270** | **60** | 6,594 |
| 64 | 822 | **40** | **270** | **60** | 13,122 |

The selective request reads exactly one membership row and one facet throughout. Full enumeration reads exactly `P` membership rows and grows with the real output size.

Predicate-presence mutation now avoids a P-sized profile rewrite:

| existing P | v0.15 add work | v0.16 add work | v0.15 delete work | v0.16 delete work | v0.16 membership bytes written per delta |
|---:|---:|---:|---:|---:|---:|
| 2 | 58 | **54** | 41 | **37** | **34** |
| 4 | 64 | **54** | 47 | **37** | **34** |
| 8 | 76 | **54** | 59 | **37** | **34** |
| 16 | 100 | **54** | 83 | **37** | **34** |
| 32 | 148 | **54** | 131 | **37** | **34** |
| 64 | 244 | **54** | 227 | **37** | **34** |

At fixed local work, `H={1,8,64}` gives maintenance **27**, selective payload **270 bytes**, and **60 VM steps** throughout. The same three measures remain fixed across unrelated `N={100,1000,10000,50000}`.

However, the `dbstat` membership-index B-tree height is **2, 2, 3, 3** across that N sweep. This matters: SQLite's VM progress callback counts a B-tree `Seek` as an instruction but does not count every internal page traversed by the seek.

Therefore the surviving statement is deliberately narrower:

\[
\boxed{
\begin{aligned}
&Maintenance_{evidence/value}=O(K)\text{ logical work},\\
&SelectiveSQLReturnedBytes\approx O(K)\text{ in the tested fixed-size facet fixture},\\
&TopologyDeltaSerializedWork\approx O(K),\\
&FullProfileWork=O(P).
\end{aligned}
}
\]

v0.16 removes the hidden serialized `O(P)` manifest but **does not** establish globally constant physical page I/O.

See `RESULTS_V0.16.md`, `normalized_membership_results.json`, and `verify_normalized_membership_results.py`.

### v0.17 B-tree page locality

v0.17 tested whether a fixed number of hash-partitioned B-tree indexes could convert the remaining page-level dependence into a constant bound. It could not.

With 4096-byte pages and identical small membership keys:

| Membership rows `N` | Global B-tree height | 64-way max shard rows | 64-way max shard height |
|---:|---:|---:|---:|
| 1,000 | 2 | 28 | 1 |
| 10,000 | 2 | 188 | 2 |
| 50,000 | 3 | 840 | 2 |
| 250,000 | 3 | 4,077 | 2 |
| 1,000,000 | 3 | 15,932 | 3 |

The partition is genuinely useful at intermediate scale: it delays a root-to-leaf height transition. But because any fixed shard count `S` leaves `Theta(N/S)` rows per shard, it does not change the comparison-index asymptotic:

\[
\boxed{
AddressLookupPages=\Theta(\log_B(N/S))=\Theta(\log_B N)
}
\]

for fixed finite `S`.

This is a negative result: **fixed B-tree sharding is not being merged as an asymptotic locality mechanism**. The current production candidate remains the simpler normalized global membership index, with logarithmic page-level addressability rather than a false constant-page claim.

See `RESULTS_V0.17.md`, `page_locality_results.json`, and `verify_page_locality_results.py`.

### v0.18 bounded-load hash resize envelope

v0.18 tested a deterministic open-addressed hash index at maximum load `0.50` with 64 logical slots per page. The lookup side is excellent in the controlled model:

| Membership rows `N` | Lookup page p50 | Lookup page p95 | Lookup page max |
|---:|---:|---:|---:|
| 1,000 | 1 | 1 | 2 |
| 4,000 | 1 | 1 | 2 |
| 16,000 | 1 | 1 | 2 |
| 64,000 | 1 | 1 | 2 |
| 256,000 | 1 | 1 | 2 |

But conventional capacity doubling moves the non-locality to mutation time:

| Membership rows `N` | Largest single resize migration |
|---:|---:|
| 1,000 | 512 rows |
| 4,000 | 2,048 rows |
| 16,000 | 8,192 rows |
| 64,000 | 32,768 rows |
| 256,000 | 131,072 rows |

Each resize rehashes every row that was live before the triggering insertion, so:

\[
\boxed{ResizeSpike(N)=\Theta(N)}
\]

The cumulative rehash work through 256k rows is **262,080 row migrations**, beyond the ordinary insertions. Therefore a stop-the-world resized hash table does **not** earn replacement of the simpler production B-tree: expected constant lookup alone is insufficient when growth can charge a global migration spike to one logical mutation.

See `RESULTS_V0.18.md`, `hash_resize_results.json`, and `verify_hash_resize_results.py`.

### v0.19 incremental hash migration

v0.19 replaces stop-the-world rehash with a two-generation migration model. Every insertion scans at most **8 source slots** and copies at most **8 old rows** while a resize is active.

| Membership rows `N` | v0.18 largest single resize rows | v0.19 max source slots scanned/insert | v0.19 max rows copied/insert | v0.19 interval max total mutation slot work |
|---:|---:|---:|---:|---:|
| 1,000 | 512 | **8** | **8** | 26 |
| 4,000 | 2,048 | **8** | **8** | 26 |
| 16,000 | 8,192 | **8** | **8** | 35 |
| 64,000 | 32,768 | **8** | **8** | 39 |
| 256,000 | 131,072 | **8** | **8** | 50 |

All **12** tested migrations complete under sustained insertion. Mid-migration lookup touches at most **2 generations**; the recorded migration snapshots have page p95 **2** and page max **3**. Temporary allocated slot capacity is exactly **1.5x** the target/current generation during migration.

The mechanism does not remove aggregate migration work. Through 256k rows it still copies **262,080 rows**, matching the v0.18 resize-row total, while scanning **524,160 source slots**. It changes the scheduling envelope: the source-side migration charge is bounded per mutation rather than concentrated into a single `Theta(N)` resize event.

But the experiment exposed a second locality failure. Total mutation slot-work maxima grow from **26** to **50** because destination placement still uses linear probing. A fixed source migration budget therefore does **not** establish worst-case constant total mutation work.

The defensible surviving statement is:

\[
\boxed{
\begin{aligned}
&SourceMigrationScanPerMutation\le 8,\\
&RowsCopiedPerMutation\le 8,\\
&LookupGenerationFanout\le 2,\\
&TemporaryCapacityAmplification=1.5,\\
&TotalMutationPlacementWork\text{ remains unbounded by this evidence.}
\end{aligned}
}
\]

The hash candidate is still an algorithmic experiment, not the production address index. Persistence/crash safety has deliberately not been claimed yet.

See `RESULTS_V0.19.md`, `incremental_hash_results.json`, and `verify_incremental_hash_results.py`.

### v0.20 bounded placement locality

v0.20 replaces the unbounded linear-probe placement tail with a finite two-choice bucketized cuckoo contract: 4 slots per bucket, at most 32 relocations, and an 8-entry stash.

| Membership rows `N` | Linear max insert probes | Cuckoo failures | Cuckoo max mutation slot work | Cuckoo lookup page max |
|---:|---:|---:|---:|---:|
| 1,000 | 11 | **0** | **17** | **2** |
| 4,000 | 19 | **0** | **17** | **2** |
| 16,000 | 21 | **0** | **22** | **2** |
| 64,000 | 31 | **0** | **27** | **2** |
| 256,000 | 34 | **0** | **30** | **2** |

The modeled worst-case insertion contract is **200 slot operations**. Controlled collision stress forces every stress key into the same two cuckoo buckets. The candidate admits 16 such keys—8 bucket entries plus 8 stash entries—and the 17th key fails at exactly the 200-operation cap. Wider collision sets continue to fail within the same cap, while rollback preserves all 16 previously admitted keys.

Thus the surviving distinction is:

\[
\boxed{BoundedPlacementWork \neq GuaranteedInsertionAvailability}
\]

The bounded candidate improves the placement locality contract but is still not the production index because it lacks a bounded escape path after local admission failure and has not been integrated with persistent migration.

See `RESULTS_V0.20.md`, `bounded_placement_results.json`, and `verify_bounded_placement_results.py`.

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

Nine distinctions are now central:

> Memory is durable state. Context is a bounded compiled artifact reconstructed for a task.

> Derived correctness requires freshness, completeness, and consistency between node identity and semantic scope.

> Maintenance locality should be judged against the true semantic footprint required by the output, not against total database size or lifetime history.

> Counting one row as one operation is not enough to prove locality when that row's serialized size grows with semantic fan-out.

> Counting one indexed `Seek` as one VM operation is not enough to prove physical locality when the underlying B-tree height grows with global memory.

> Fixed partitioning of a comparison index changes constants and thresholds, not the `Theta(log_B N)` lookup class.

> Expected constant lookup does not establish system locality if resize can charge `Theta(N)` migration work to one logical mutation.

> Bounding migration scheduling does not bound total mutation work when the placement primitive itself has an unbounded collision/probe tail.

> Bounding placement work does not guarantee insertion availability; finite local capacity requires an explicit bounded failure/escape policy.

## Next falsification target — bounded placement escape

v0.20 establishes an explicit placement-work cap in the algorithmic model, but concentrated demand exhausts two candidate buckets plus the finite stash. Rejecting the 17th concentrated key preserves locality and correctness, yet an address index that simply rejects durable membership is incomplete.

The next question is therefore:

\[
\boxed{
Can local placement failure obtain a bounded escape path
without reintroducing unbounded lookup, global rehash, or hidden overflow scans?
}
\]

A v0.21 experiment should compare a small fixed number of independent bounded placement domains or another explicitly bounded escalation mechanism. It must measure the end-to-end mutation bound including escalation, maximum lookup domain/page fan-out, total reserved-space amplification, failure threshold under concentrated collisions, and preservation of admitted keys. Only after admission availability and placement locality survive together should the project integrate the mechanism with v0.19-style incremental migration and pay for durable crash/restart and stale-read testing.
