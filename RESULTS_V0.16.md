# v0.16 — Normalized Predicate Membership

## Falsification target

v0.15 removed subject-wide evidence reconstruction from the common selective-read and evidence/value-maintenance path, but the persisted subject profile was still one serialized predicate manifest:

\[
Manifest(subject)=[p_1,\ldots,p_P]
\]

That made the logical row accounting too weak for a physical-locality claim. A `K=1` selective read counted one manifest row even though that row grew with all `P` live predicates, and a predicate-presence delta rewrote the same `O(P)` value.

The v0.16 question was therefore:

\[
\boxed{
Can selective returned work and predicate-topology delta work avoid touching an O(P)
serialized object while full enumeration remains honestly O(P)?
}
\]

## Candidate mechanism

v0.16 replaces the serialized predicate list with two physical pieces:

1. a constant-size `profile:<subject>` descriptor containing only `subject_id`;
2. normalized indexed rows:

```text
profile_predicate_membership(subject_id, predicate)
PRIMARY KEY(subject_id, predicate)
```

The logical profile is unchanged:

\[
Profile(subject,Q)
=
Descriptor(subject)
+
Membership(subject,Q)
+
\sum_{p\in Q}Facet(subject,p)
\]

Selective reads validate each requested predicate through an indexed membership probe. Full reads enumerate all membership rows for the subject. Canonical assertion mutation synchronizes only affected membership keys after the current-head update.

The persisted descriptor deliberately has no `P` predicate dependencies, so topology add/remove no longer requires reconstructing an all-predicate object.

## Measurement discipline

The experiment adds measurements that cannot hide an arbitrarily large row behind a single logical read:

- exact bytes returned by the SQL rows used for descriptor, membership, and facet assembly;
- returned-row page-size units;
- SQLite VM instruction callbacks for the read path;
- `EXPLAIN QUERY PLAN` checks for indexed membership lookup/enumeration;
- `dbstat` B-tree height for the normalized membership index when available;
- explicit membership rows/bytes written during topology mutation.

These are **not** claimed to be direct operating-system or storage-device page-read counts. In particular, one SQLite `Seek` VM instruction can traverse multiple B-tree pages internally.

## Exact semantic and safety constraints

The candidate was required to preserve:

- exact v0.15 full logical profile output;
- independent canonical-oracle parity for full and partial profiles;
- exact derived clean-rebuild parity;
- exact `(subject,predicate)` membership parity with transactional current heads;
- H/global-N logical locality from the previous milestones;
- predicate add/remove lifecycle correctness;
- one-snapshot stale-read protection, including unrelated-facet availability.

All passed in the fixed CI experiment.

## P sweep — remove the hidden serialized O(P) read

At fixed `K=1`, `H=8`, `N=128`:

| P | v0.15 manifest bytes | v0.16 descriptor bytes | selective SQL payload bytes | selective VM steps | full SQL payload bytes | full membership rows |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 66 | **40** | **270** | **60** | 270 | 1 |
| 2 | 78 | **40** | **270** | **60** | 474 | 2 |
| 4 | 102 | **40** | **270** | **60** | 882 | 4 |
| 8 | 150 | **40** | **270** | **60** | 1,698 | 8 |
| 16 | 246 | **40** | **270** | **60** | 3,330 | 16 |
| 32 | 438 | **40** | **270** | **60** | 6,594 | 32 |
| 64 | 822 | **40** | **270** | **60** | 13,122 | 64 |

The fixed selective request reads exactly one membership row and one facet throughout the sweep. Its returned membership bytes remain 8 and its facet bytes remain 222. Full enumeration scales with the real output obligation instead of being hidden behind one manifest read.

Thus the v0.15 manifest counterexample is removed for the measured SQL-returned payload path:

\[
\boxed{
SelectiveReturnedBytes(K=1)\not\propto P
}
\]

while:

\[
\boxed{
FullReturnedBytes\propto P
}
\]

as required.

## K sweep — selective physical payload tracks the requested subset

At fixed `P=32`, `H=8`, `N=128`:

| K | maintenance work | membership rows | selective payload bytes | selective VM steps |
|---:|---:|---:|---:|---:|
| 1 | 27 | 1 | 270 | 60 |
| 2 | 54 | 2 | 501 | 93 |
| 4 | 108 | 4 | 963 | 159 |
| 8 | 216 | 8 | 1,887 | 291 |
| 16 | 432 | 16 | 3,741 | 555 |

The result is consistent with subset-proportional returned work under this fixture. Full output remains P-sized.

## Predicate topology delta — the discriminating control

The strongest v0.16 result is on predicate presence changes. The v0.15 control must rebuild the P-sized manifest. The normalized candidate updates one membership row and rewrites only the constant descriptor when the inherited profile lifecycle requires it.

| existing P | v0.15 add work | v0.16 add work | v0.15 delete work | v0.16 delete work | v0.15 profile bytes after add | v0.16 descriptor bytes |
|---:|---:|---:|---:|---:|---:|---:|
| 2 | 58 | **54** | 41 | **37** | 92 | **40** |
| 4 | 64 | **54** | 47 | **37** | 116 | **40** |
| 8 | 76 | **54** | 59 | **37** | 164 | **40** |
| 16 | 100 | **54** | 83 | **37** | 260 | **40** |
| 32 | 148 | **54** | 131 | **37** | 452 | **40** |
| 64 | 244 | **54** | 227 | **37** | 836 | **40** |

Each v0.16 add/delete writes exactly **one membership row**. The measured key payload is **34 bytes** for both insertion and deletion throughout the P sweep.

This supports:

\[
\boxed{
TopologyDeltaReturned/SerializedWork=O(K)
}
\]

for the tested one-row predicate-presence delta, rather than `O(P)` manifest rewrite work.

## History and unrelated global cardinality

For `H={1,8,64}` at fixed local profile size:

```text
maintenance work       = 27, 27, 27
selective payload bytes= 270, 270, 270
selective VM steps     = 60, 60, 60
```

For unrelated `N={100,1000,10000,50000}`:

| N | maintenance work | selective payload bytes | selective VM steps | membership-index B-tree height | full rebuild work |
|---:|---:|---:|---:|---:|---:|
| 100 | 27 | 270 | 60 | 2 | 1,701 |
| 1,000 | 27 | 270 | 60 | 2 | 12,501 |
| 10,000 | 27 | 270 | 60 | **3** | 120,501 |
| 50,000 | 27 | 270 | 60 | **3** | 600,501 |

The logical recovery work, SQL-returned bytes, and SQLite VM instruction count remain fixed. However, `dbstat` exposes an important physical counterexample: the global membership B-tree grows from height 2 to height 3 as unrelated cardinality grows.

That means v0.16 **does not establish constant storage-page work independent of global memory**. The VM progress callback counts `Seek` as an instruction; it does not count every internal B-tree page traversed by that seek. The normalized representation removes the hidden `O(P)` serialized value, but it reveals the next lower-level scaling surface:

\[
IndexedProbePhysicalCost \supseteq f(BTreeHeight)
\]

which can grow with the global index.

## Surviving result

The evidence supports the narrower statement:

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

It also falsifies a stronger interpretation that normalized indexed rows by themselves prove globally constant physical I/O.

## Deliberate non-claims

v0.16 does **not** establish:

- direct OS/storage-device page-read counts;
- constant B-tree traversal depth as global memory grows;
- production wall-clock latency or cache-miss behavior;
- constant work for arbitrarily large individual predicate/facet payloads;
- distributed or partitioned storage locality;
- any superiority result over a strong agentic-RAG baseline.

## Evidence

The first fixed CI execution passed the full historical replay chain, the new v0.16 tests, and the normalized-membership runner on head:

```text
b90bd998f7eedeeb413475f2a8431b1ec9b654f4
```

CI run: `34059545804` (run #167).

Artifact `v0.16-normalized-membership-results` had digest:

```text
sha256:9b11ab4385e79ff7a1af17c9d1dda94ca164fed5afa00bfbd2ab1cff2b37fd22
```

The evidence is anchored in:

- `normalized_membership_results.json`
- `verify_normalized_membership_results.py`
- `run_normalized_membership_experiment.py`

## Next falsification target

v0.16 has removed the serialized-manifest scaling leak, but the membership index itself is a global B-tree whose height increased in the N sweep.

The next question should therefore move below row/VM accounting:

\[
\boxed{
Can task-local lookup page working set remain bounded as global durable memory grows,
without sacrificing exact semantics or honest full enumeration?
}
\]

A v0.17 experiment should measure actual/upper-bounded lookup page traversal and compare global B-tree indexing against an explicitly partitioned or otherwise page-local address structure. Any claim of physical global-memory independence must survive that test rather than treating an indexed `Seek` as constant by definition.
