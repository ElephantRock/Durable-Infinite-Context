# v0.30 — Integrated segmented primary

## Observe

v0.29 removed the v0.28 capacity-scaled sparse-address mechanism in an arithmetic model, but the surviving extent map had not yet been placed on the real fixed-page storage path. The unresolved risk was integration: real `pread`/`pwrite`/`ftruncate`/`fsync` ordering, migration writes, transaction-local visibility, process death, and restart cleanup could still reintroduce a capacity-scaled tail or mixed committed state.

The first integrated v0.30 attempt also exposed a test-fixture defect rather than a storage failure. At `C=32`, the original target key landed in the same 16-page logical segment already materialized by the seed row, so the migration-start insert legitimately allocated zero new segments. The fixture was revised to deterministically choose a target whose first bucket crosses the old/new segment-overlap boundary. The storage assertions were not weakened.

## Diagnose

An integrated design has to satisfy two different visibility rules simultaneously:

- restart/external reads may only follow radix/data records whose epoch is at or below the committed superblock epoch;
- the single writer, while building one transaction, must read its own staged radix nodes and fresh segment mappings before the superblock commit.

Using committed-only radix traversal inside the writer loses transaction-local dependencies when more than one mapping mutation touches a staged path. Conversely, exposing pending nodes to restart readers would violate crash atomicity.

The correct mechanism is therefore a transaction-local mapping view layered over an unchanged committed-epoch restart view.

## First-principles requirement

A process-crash-safe append-local primary needs all of the following:

1. logical bucket identity must not determine a sparse physical offset;
2. every transaction's newly materialized physical state must be append-local to a committed frontier;
3. publication must be dependency-before-commit: data and mapping records durable before the alternate superblock is made durable;
4. pre-commit restart must expose exactly the prior committed image, while post-commit restart exposes exactly the new image;
5. restart cleanup must derive the truncation frontier from fixed committed metadata, not from a generation scan or logical redo;
6. operation-local bounds must depend on fixed placement/migration budgets and radix depth, not on numeric generation capacity.

## Hypothesis

> The v0.24 fixed-page logical primary can run over append-local 16-logical-page physical segments addressed by an eight-level dual-copy radix extent map while preserving bounded migration/lookup behavior and exact process-crash visibility. A committed physical frontier in the dual-copy superblock is sufficient for scan-free truncation of uncommitted append residue.

The experiment remains single-writer and uses the existing bounded cuckoo/stash placement and migration machinery.

## Prediction

For forced `C -> 2C` migration at

```text
C = {32, 2048, 131072, 4194304}
```

the new generation's logical page and segment ids should grow with `C`, but one fresh physical segment can reserve at most

\[
2\times16 + 2\times(8-1)=46
\]

pages, or

\[
\boxed{188{,}416\text{ bytes}}
\]

for the complete fresh data segment plus worst-case missing radix suffix.

Under the fixed eight-level radix namespace, a successful lookup should remain within the single-generation envelope and a miss during active migration within the two-generation envelope. A transaction uses exactly two durability barriers.

For real `SIGKILL` at allocation, dependency-write, dependency-fsync, and committed-superblock points:

- all pre-commit kills must reopen as the exact pre-insert fixture state;
- the committed kill must reopen as the exact post-insert state;
- the uncommitted tail must never exceed the deterministic transaction append;
- recovery must truncate to the committed physical frontier with zero generation-page scan, zero radix scan, and zero logical redo;
- a second recovery pass must perform zero physical truncation.

## Test

The capacity fixture stores one seed row and sets the load threshold so the second row starts migration. The target key is deterministically searched so its first bucket lies in a logical segment strictly beyond the final segment touched by any old-generation page. This guarantees that every capacity case actually exercises fresh-segment materialization rather than accidentally reusing the seed segment.

The storage path uses:

- 4096-byte CRC records;
- two superblock copies;
- 16 logical pages per physical segment;
- two physical data copies per logical page;
- eight radix levels, fanout 256, two physical copies per radix node;
- a committed `next_physical_page` frontier;
- fixed `max_kicks=32`;
- fixed migration budget `8`.

The crash matrix runs 16 real process-death cases: four capacities times four failpoints.

## Results

### Capacity sweep

| Old `C` | New logical base page | Target segment id | Fresh segments | Physical append | Radix node pwrites | Target lookup preads | Active-migration miss preads |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 32 | 9 | 1 | 1 | 131,072 B | 1 | 20 | 46 |
| 2,048 | 513 | 80 | 1 | 131,072 B | 1 | 20 | 98 |
| 131,072 | 32,769 | 4,618 | 1 | 139,264 B | 2 | 20 | 88 |
| 4,194,304 | 1,048,577 | 98,773 | 1 | 147,456 B | 3 | 20 | 84 |

Logical identifiers grow by orders of magnitude, but the observed append remains between 32 and 36 pages. The largest observed append is 147,456 bytes, below the 188,416-byte per-fresh-segment bound. Each transaction materialized exactly one fresh segment in this fixture.

Target lookup remained 20 user-space `pread` calls in every row. The largest active-migration miss was 98 calls, below the fixed two-generation bound of 110.

### Real process-death matrix

All **16/16** crash cases matched the exact required committed state.

For `allocated`, `pages_written`, and `data_synced` kills, the target row remained invisible and the seed row remained visible. The process-visible uncommitted tails were exactly the deterministic control append:

- 131,072 B at `C=32`;
- 131,072 B at `C=2,048`;
- 139,264 B at `C=131,072`;
- 147,456 B at `C=4,194,304`.

For the `committed` kill, both rows were visible and the uncommitted tail was zero.

Every first recovery pass truncated only the uncommitted physical suffix. Every second pass truncated zero bytes. Across all cases:

```text
logical_work = 0
generation_pages_scanned = 0
mapping_nodes_scanned = 0
frontier_superblock_preads = 2
max tail after recovery = 0
```

The v0.16 semantic guard also remained exact for membership, materialization, head indexes, freshness, and full/partial assembly.

## Result

The integrated hypothesis **survived this process-crash falsification**.

\[
\boxed{
AppendLocalSegments
+ FixedDepthMapping
+ DependencyBeforeCommit
+ CommittedFrontier
\Rightarrow
BoundedProcessVisibleUncommittedTail
}
\]

within the fixed operation budgets and fixed 64-bit radix namespace tested here.

The important scaling result is not that physical work is literally constant for every possible transaction. It is that the numeric generation capacity no longer determines sparse physical address span. Physical append is instead a function of the operation-local number of fresh segments and the fixed radix suffix needed to publish them.

## Surviving claim

For the tested single-writer fixed-page primary, forced migration from `C` to `2C` across `C={32,2048,131072,4194304}` preserved exact pre/post process-crash visibility while keeping newly appended physical state operation-local rather than capacity-addressed. Restart recovery derived the committed frontier from two superblock reads and removed all uncommitted file-length residue without scanning generation pages, scanning mapping nodes, or replaying logical mutations.

## Evidence anchor

Initial successful full historical CI:

- run #280;
- run ID `34362998037`;
- head `2e8a0e3209c0d438033c611cd93547d57c382eb1`;
- artifact ID `10109302085`;
- artifact SHA-256 `719573b3cf6b4a5c61f179cd3547b19723a2558f430bd42975b9468c7ad8775f`;
- `integrated_segmented_primary_results.json` SHA-256 `448373cde685166dc1c3c10ac23a0ce4d5c7c650f3c6617ac581acc6db700dd8`.

The committed ledger is replayed by `verify_integrated_segmented_primary_results.py`.

## Non-claims

v0.30 does **not** establish:

- hardware power-loss, torn-sector, drive-cache, or filesystem-journal correctness;
- device-I/O bounds from user-space syscall counts;
- arbitrary multi-writer, distributed, or replicated correctness;
- production throughput, latency, or storage efficiency;
- mathematically unbounded identifiers; the tested radix namespace is fixed at 64 bits;
- constant total mapping metadata with materialized segment count;
- bounded filesystem allocated-block reclamation after `ftruncate`;
- dense radix-node correctness once a node's serialized JSON payload approaches the 4096-byte record limit.

## Next falsification target

The integrated design now survives sparse-path capacity scaling and real process-death ordering, but it still relies on an experiment-only encoding assumption: every radix node's JSON `entries` object fits in one 4096-byte record.

The next discriminating question is:

\[
\boxed{
Can the mapping layer preserve bounded lookup, mutation, publication, and crash recovery
when dense materialized prefixes force radix-node split or overflow behavior?
}
\]

A useful v0.31 experiment should deliberately fill one or more radix nodes to the one-page encoding limit, force the next mapping insertion to split or overflow, and inject process death across allocation, child publication, parent publication, and superblock commit. The test must expose exact pre/post committed maps, bound any extra append work by an explicit split budget, preserve fixed-depth lookup or explicitly revise that claim, and ensure recovery remains frontier-derived rather than scan-based. If node growth introduces an unbounded rewrite, recursive split cascade, capacity-scaled scan, or ambiguous crash image, the current v0.30 mapper is not yet a durable general storage structure.
