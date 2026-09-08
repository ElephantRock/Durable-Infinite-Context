# v0.28 — Lazy generation allocation residue

## Observe

v0.27 established that restart must re-derive durable residue rather than trust process-visible cleanup state. The remaining fixed-page problem is the size of the stale generation range itself. Under the v0.24/v0.25 layout, migration start eagerly extends the file for every page in the doubled generation, producing:

\[
EagerResidue(C)=4096(C+2)=\Theta(C)
\]

for old capacity `C` with four slots per bucket and two physical copies per logical page.

## Diagnose

A tempting repair is to remove the eager `ftruncate` and let high-offset `pwrite` calls materialize generation pages only when touched. That reduces eager reservation, but the relevant crash-residue metric is not the count of pages written. A sparse write can extend the process-visible file length across a large hole.

The v0.24 direct address is:

\[
PhysicalPageIndex=2+2p+copy
\]

At migration start, the committed file frontier ends exactly at the new generation base. The first write to bucket page `b` uses copy 0 and therefore creates:

\[
LazyResidue(b)=(2b+1)\times4096
\]

bytes beyond that committed frontier, even though one logical page is materialized.

## Hypothesis

> Removing eager full-generation extension while retaining the v0.24 arithmetic dual-copy page addresses is sufficient to bound crash residue by a constant because a first lazy mutation materializes only one logical page.

## Prediction

For old capacity `C`, the doubled generation has `C/2` bucket pages. Therefore:

\[
\begin{aligned}
LazyFirstBucket(C)&=4096\\
LazyLastBucket(C)&=4096(C-1)\\
LazyStash(C)&=4096(C+1)\\
Eager(C)&=4096(C+2)
\end{aligned}
\]

The naive lazy candidate should therefore remain `Theta(C)` in worst-case file-length residue even though one logical page is written.

## Test

The experiment fixes:

- page size: 4096 bytes;
- bucket size: 4;
- old capacities `C={32,128,512,2048}`;
- doubled generation geometry from the v0.24 store;
- 4096 deterministic keys per capacity;
- the exact v0.24 keyed BLAKE2 first-bucket hash;
- eager extension as the control.

The measured byte quantities are **file-length range beyond the committed frontier**. They are not filesystem allocated blocks, device writes, or latency.

## Results

| Old capacity `C` | Eager extension | Lazy first bucket | Lazy last bucket | Lazy stash | Sample p50 | Sample p95 | Sample max |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 32 | 139,264 | 4,096 | 126,976 | 135,168 | 61,440 | 126,976 | 126,976 |
| 128 | 532,480 | 4,096 | 520,192 | 528,384 | 258,048 | 495,616 | 520,192 |
| 512 | 2,105,344 | 4,096 | 2,093,056 | 2,101,248 | 1,069,056 | 1,994,752 | 2,093,056 |
| 2,048 | 8,396,800 | 4,096 | 8,384,512 | 8,392,704 | 4,206,592 | 7,958,528 | 8,384,512 |

Every sampled first lazy write materialized exactly one logical page. Nevertheless, the deterministic hash sample reached the algebraic final bucket at every tested capacity, and p95 residue scaled with capacity.

## Result

The hypothesis is **falsified**.

\[
\boxed{OneLazyPageWritten\not\Rightarrow BoundedFileLengthResidue}
\]

Retaining the direct-address geometry leaves the last-bucket and stash cases at `Theta(C)`. Naive laziness changes when the range appears; it does not remove the capacity-scaled offset that causes it.

The stronger distinction is:

\[
\boxed{LazyPhysicalMaterialization\neq BoundedAddressSpan}
\]

## Surviving claim

Naive lazy generation allocation can reduce eager file-length reservation for low-address writes, but it cannot asymptotically bound stale file-length residue under the current arithmetic dual-copy layout. Bounding residue requires changing address placement or introducing a bounded mapping/segmentation mechanism; delaying the same high-offset writes is insufficient.

## Evidence anchor

Initial successful CI:

- run #263;
- run ID `34250517135`;
- head `94ca178eb653ae24f43257941f11ba9ff7740056`;
- artifact ID `10066354318`;
- artifact SHA-256 `f78e49dfa066122c3cd52e6ab793ed4fc6aed5ccd860b6b7170c0a34e8535430`;
- `lazy_generation_allocation_results.json` SHA-256 `bbf4b1285909fdaf429d59d7a63bbbd386b25b082f337188e083691f434eb405`.

The committed ledger is replayed by `verify_lazy_generation_allocation_results.py`.

## Non-claims

v0.28 does not measure sparse-file allocated blocks, filesystem journal behavior, device writes, latency, or hardware power-loss durability. It also does not establish that an extent map, segment table, or indirection layer is better; such a mechanism must be tested separately for lookup depth, metadata growth, mutation work, crash safety, and reclamation behavior.

## Next falsification target

v0.29 should test the smallest mapping mechanism capable of decoupling logical bucket identity from a capacity-scaled physical offset. A useful fixed candidate is a **bounded segment table**: arithmetic addressing within fixed-size segments plus a bounded number of segment descriptors on the common path.

The key question is whether bounded segment allocation can make crash residue proportional to the fixed segment size while preserving:

- constant or strictly bounded descriptor reads on ordinary lookup;
- bounded migration work;
- bounded per-mutation metadata writes;
- restart-safe allocation metadata;
- no hidden comparison-tree or unbounded extent-map traversal.

If segment descriptors must themselves grow or be searched with generation size, the non-locality has merely moved into the mapping layer and the candidate should be rejected.
