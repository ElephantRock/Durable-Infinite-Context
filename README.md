# Durable Infinite Context — Minimum Falsifiable Prototype v0.22

This repository is a falsification-first research prototype for **Durable Infinite Context**: durable memory may grow without bound while task context remains bounded and reconstructed on demand.

\[
C_t = F(q_t,M_t), \qquad |C_t| \le B
\]

The project does not attempt to create an actually infinite model context window. It tests whether bounded task context can be reconstructed from growing durable state with correct revision semantics and tractable operational cost.

## Governing discipline

**Observe → Diagnose → Derive → Hypothesize → Predict → Test → Revise → Engineer**

Architecture is treated as a surviving hypothesis, not as the goal. Negative results are retained because they constrain the next design.

## Current surviving production candidate

The durable path currently contains:

- canonical evidence and revisable assertions with lineage;
- correction, supersession, and contested-state reconciliation;
- valid-time and knowledge-time queries;
- bounded context compilation;
- selective materialization and multi-dimensional addressability;
- deterministic non-oracle query planning over oracle assertions;
- scalable indexed candidate generation and incremental index maintenance;
- dependency-aware invalidation/reconstruction/retirement;
- explicit derived lifecycle states (`fresh`, `invalid`, `rebuilding`);
- SQLite WAL persistence with real `SIGKILL` recovery tests;
- durable ordered logical intents and optimistic canonical versions;
- snapshot-consistent derived reads;
- promotion-time topology revalidation;
- explicit missing-output obligations;
- subject-wide profile semantic identity;
- transactional `(subject,predicate)` current heads;
- compositional predicate facets;
- normalized indexed `(subject,predicate)` membership;
- constant-size subject profile descriptors;
- facet-local stale-read protection;
- machine-readable evidence ledgers with executable replay verifiers.

The v0.18–v0.22 hash/cuckoo/overflow structures are **experimental alternatives**, not yet replacements for the production membership B-tree.

## Deliberate non-claims

Current evidence does **not** establish:

- production entity linking, semantic embeddings, or extraction accuracy;
- distributed/replicated consistency;
- hardware power-loss guarantees beyond the tested SQLite/storage stack;
- arbitrary physical multi-writer execution;
- production latency or dollar cost;
- cold/archive recovery;
- arbitrary ontology migration;
- constant work for arbitrarily large live subject fan-out or facet values;
- direct OS/device page-read locality independent of global memory;
- constant comparison-tree depth as indexes grow;
- universally constant lookup under arbitrary collisions;
- a crash-safe hybrid hash/overflow replacement for the production B-tree;
- a strong agentic-RAG superiority result.

## Milestone ledger

| Version | Falsification target | Main surviving result |
|---|---|---|
| v0.1 | Is persistent state semantically necessary? | No under oracle retrieval; persistent state only earned a possible efficiency role. |
| v0.2 | Does materialized state earn write complexity? | Selective materialization reduces repeated current-state reconstruction when read savings justify maintenance. |
| v0.3 | Is one similarity channel sufficient? | No. Identity/time are independent address dimensions; adaptive coverage prevents premature closure. |
| v0.4 | Does addressability survive removal of the oracle plan? | Controlled-language planner matched resolvable oracle cases and abstained on irreducible ambiguity. |
| v0.5 | Can query resolution avoid O(N) subject scans? | Yes after fragment addressability; indexed resolution stayed accurate through 50k entities. |
| v0.6 | Can address indexes be maintained locally? | Fixed-local mutations stayed roughly constant while rebuild work grew with total memory. |
| v0.7 | Can multi-layer invalidation/rebuild remain local? | Yes for tested DAGs after hidden whole-graph invalid discovery was removed. |
| v0.8 | Can interrupted maintenance recover without stale reads? | Initial phase handling failed; idempotent redo repaired the protocol. |
| v0.9 | Do crash invariants survive real process death? | SQLite WAL + `synchronous=FULL` passed 33 real `SIGKILL` cases. |
| v0.10 | Do multiple durable intents preserve conflict/recovery semantics? | Yes after a snapshot race was found and fixed. |
| v0.11 | Can admission-time impact metadata become stale? | Yes. Promotion-time topology revalidation closes the demonstrated leak. |
| v0.12 | Can canonical growth require absent derived outputs? | Yes. Explicit missing-output obligations restore completeness. |
| v0.13 | Can subject-only profiles remain correct across predicate change? | Subject-wide profile semantics restore exact parity. |
| v0.14 | Can current reconstruction avoid historical-depth scans? | Transactional current heads remove H-dependence while preserving true P-dependence. |
| v0.15 | Can selective maintenance scale with K instead of P? | Yes in logical facet work; exposed an O(P) serialized manifest. |
| v0.16 | Can the serialized manifest be removed? | Yes: normalized membership plus a 40-byte descriptor keeps measured selective returned work K-local, but B-tree depth still grows with N. |
| v0.17 | Can fixed B-tree sharding make lookup depth constant? | No. Fixed sharding changes thresholds, not `Theta(log_B N)`. |
| v0.18 | Does conventional hashing solve lookup locality without new spikes? | No. Expected constant lookup coexists with `Theta(N)` stop-the-world resize spikes. |
| v0.19 | Can incremental migration bound resize work per mutation? | Source migration is bounded at 8 slots/rows, but linear placement retains an unbounded tail. |
| v0.20 | Can placement itself have a finite work cap? | Yes with bounded bucketized cuckoo placement, but concentrated capacity is finite: 16 keys. |
| v0.21 | Can finite bounded domains provide an escape path? | Yes: capacity `16D`, mutation cap `200D`, miss pages `3D`; fixed D still has finite admission and proportional space. |
| v0.22 | Can a bounded common path coexist with explicit rare overflow? | Yes in the tested model: ordinary primary hits remain isolated; overflow guarantees admission but honestly inherits logarithmic B-tree depth. |

Detailed narratives and machine-readable evidence live in `RESULTS_V0.*.md` and `*_results.json`.

## Selected validated measurements

### v0.16 normalized membership

At fixed `K=1,H=8,N=128`, the subject descriptor stays **40 bytes** and selective SQL payload stays **270 bytes** across `P=1..64`; full profile payload grows with the real output. Predicate topology deltas avoid rewriting a P-sized manifest.

The remaining caveat is physical lookup depth: the membership B-tree height grows with global N, so one SQLite VM `Seek` is not proof of constant physical locality.

### v0.17 comparison-tree page locality

With 4096-byte pages:

| Membership rows `N` | Global height | 64-way max shard height |
|---:|---:|---:|
| 1,000 | 2 | 1 |
| 10,000 | 2 | 2 |
| 50,000 | 3 | 2 |
| 250,000 | 3 | 2 |
| 1,000,000 | 3 | 3 |

For fixed finite shard count `S`:

\[
\boxed{AddressLookupPages=\Theta(\log_B(N/S))=\Theta(\log_B N)}
\]

### v0.18–v0.19 resize locality

v0.18 stop-the-world resize produces largest single migrations of:

`512, 2,048, 8,192, 32,768, 131,072` rows

at `N={1k,4k,16k,64k,256k}`.

v0.19 limits source migration to **8 source slots / 8 copied rows per insertion**, but total mutation slot-work maxima still grow `26,26,35,39,50` because destination placement uses linear probing.

### v0.20 bounded placement

The fixed two-choice bucketized cuckoo candidate uses 4 slots per bucket, 32 relocation attempts, and an 8-entry stash. Ordinary growth through 256k entries has zero failures and observed mutation maxima `17,17,22,27,30`. The modeled insertion cap is **200 slot operations**.

Concentrated collision stress admits 16 keys and rejects the 17th at the explicit bound:

\[
\boxed{BoundedPlacementWork \neq GuaranteedInsertionAvailability}
\]

### v0.21 bounded finite escalation

For `D={1,2,4,8}` concentrated domains:

| D | Capacity | First failure | Mutation cap | Missing lookup pages | Reserved space |
|---:|---:|---:|---:|---:|---:|
| 1 | 16 | 17 | 200 | 3 | 1x |
| 2 | 32 | 33 | 400 | 6 | 2x |
| 4 | 64 | 65 | 800 | 12 | 4x |
| 8 | 128 | 129 | 1,600 | 24 | 8x |

Finite escalation is coherent, but unlimited escalation would simply move non-locality into mutation work, lookup fan-out, and reserved capacity.

### v0.22 explicit rare overflow

v0.22 keeps **one bounded v0.20 primary domain** and sends only exhausted placements to an exact-key SQLite `WITHOUT ROWID` B-tree overflow.

Ordinary `N={1k,4k,16k,64k,256k}` remains entirely on the primary path:

| N | Overflow insertions | Primary max mutation work | Primary lookup page max | Primary-hit overflow checks |
|---:|---:|---:|---:|---:|
| 1,000 | 0 | 17 | 2 | 0 |
| 4,000 | 0 | 17 | 2 | 0 |
| 16,000 | 0 | 22 | 2 | 0 |
| 64,000 | 0 | 27 | 2 | 0 |
| 256,000 | 0 | 30 | 2 | 0 |

After deliberately saturating the primary's 16-key concentrated capacity, every later key is admitted to overflow. Overflow depth grows explicitly:

| Overflow rows `O` | B-tree height | Overflow-hit modeled pages | Missing-key modeled pages |
|---:|---:|---:|---:|
| 1 | 1 | 4 | 4 |
| 16 | 1 | 4 | 4 |
| 64 | 1 | 4 | 4 |
| 256 | 2 | 5 | 5 |
| 1,024 | 2 | 5 | 5 |
| 4,096 | 2 | 5 | 5 |
| 16,384 | 3 | 6 | 6 |

The saturated primary miss contributes 3 modeled pages, so:

\[
\boxed{ExceptionalLookupPages(O)=3+Height_{BTree}(O)}
\]

The defensible result is therefore **common/exceptional-path separation**, not universal constant lookup. See `RESULTS_V0.22.md`, `rare_overflow_results.json`, and `verify_rare_overflow_results.py`.

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
python run_rare_overflow_experiment.py
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
python verify_rare_overflow_results.py
```

CI runs this chain on pull requests and uploads the hardened evidence ledgers as artifacts.

## Current architectural hypothesis

```text
Canonical evidence
  -> revisable assertion history
  -> transactional current heads
  -> normalized current predicate membership
  -> predicate-specific evidence-bearing facets
  -> constant-size subject descriptor / compositional profile
  -> selectively materialized state / derived views

Question
  -> ambiguity-aware resolution
  -> indexed candidate generation
  -> justified constraints + coverage control
  -> requested semantic subset
  -> indexed membership validation
  -> one-snapshot facet assembly
  -> bounded context compilation

Canonical mutation
  -> durable ordered intent
  -> conflict validation
  -> promotion-time topology revalidation
  -> local invalidation / missing-output derivation
  -> local reconstruction / current-head update
  -> affected membership/facet repair
  -> selective retirement
  -> crash-safe completion
```

Eleven distinctions are now central:

> Memory is durable state. Context is a bounded compiled artifact reconstructed for a task.

> Correct derived state requires freshness, completeness, and semantic-identity consistency.

> Locality must be judged against the true semantic footprint, not lifetime database size.

> Logical row counts can hide serialized fan-out.

> One indexed VM `Seek` can hide growing B-tree page depth.

> Fixed comparison-index sharding changes constants, not asymptotic depth.

> Expected constant lookup does not imply bounded mutation if resize is global.

> Bounded migration scheduling does not bound an unbounded placement primitive.

> Bounded placement work does not imply guaranteed admission.

> Finite bounded escalation raises admission only by proportional work/lookup/space.

> Guaranteed admission can coexist with a bounded common path only by making exceptional cost explicit; exceptional lookup is not thereby constant.

## Next falsification target — durable hybrid admission

v0.22 earns an algorithmic common/exceptional split. It does **not** yet earn a production replacement for the B-tree because the hybrid is not persistent and has not been integrated with incremental migration.

The next question is:

\[
\boxed{
Can bounded primary migration and explicit overflow admission remain transactionally correct
across crashes without contaminating common-path locality?
}
\]

A v0.23 experiment should integrate v0.19-style incremental generation migration with the v0.22 bounded-primary/overflow policy and inject crashes around every move/admission boundary. It must test duplicate/lost membership, idempotent recovery, stale/missing reads during active migration, common-path isolation after persistence metadata, overflow write amplification, and recovery work.
