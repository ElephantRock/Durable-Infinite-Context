# v0.24 portability correction — filesystem allocated-byte observations

## Observe

The v0.34 post-merge `main` CI run #343 (`34567541004`) reached the historical v0.24 verifier after the unit suite and all experiments through v0.34 had passed. `verify_fixed_page_primary_results.py` then failed only its byte-exact artifact hash check. Its internal rerun produced raw SHA-256 `036de8f6e54e85ecc7ef2f247352e1a28f059db0018bcfe06b15e6970f898207` instead of the initial v0.24 artifact JSON SHA-256 `acb136b0da7dac9665bdc861492ab5366363d86404536a9211d0418057183812`.

All semantic guard, crash-matrix, direct-address lookup, migration-budget, and collision-rejection assertions had already passed.

## Diagnose

The raw v0.24 result includes `allocated_bytes = stat.st_blocks * 512` for sparse files. `st_blocks` is an observation of filesystem block allocation, not a deterministic part of the logical store image.

The failing runner reproduced the same deterministic v0.24 fields while differing in filesystem allocation at later checkpoints. For example:

| Membership rows | Initial anchored `allocated_bytes` | CI #343 `allocated_bytes` | Difference |
|---:|---:|---:|---:|
| 4,096 | 26,046,464 | 26,050,560 | +4,096 |
| 16,384 | 105,459,712 | 105,467,904 | +8,192 |

The logical file sizes at those checkpoints remained exactly 33,357,824 and 134,037,504 bytes respectively. The changed values therefore falsify the old assumption that raw `st_blocks` observations are byte-reproducible across otherwise equivalent CI runs; they do not falsify the v0.24 logical or user-space-I/O claims.

## First principle

A historical evidence verifier should distinguish immutable evidence identity from reproducible semantics. Host/filesystem observations may remain recorded as evidence, but they must not be promoted into a cross-run semantic identity constraint unless the experiment establishes that portability.

## Revision

The initial committed v0.24 result remains immutable and is still required to have SHA-256:

`acb136b0da7dac9665bdc861492ab5366363d86404536a9211d0418057183812`

The verifier now reads that committed `HEAD` ledger directly, so an earlier experiment step cannot replace the evidence anchor in the working tree. It then reruns the experiment and requires exact equality for every field except `allocated_bytes`.

For every `allocated_bytes` observation, the verifier still requires:

- the same observation location to exist in the rerun;
- a non-negative value;
- 512-byte `stat.st_blocks` alignment;
- `allocated_bytes <= file_size_bytes`.

A dedicated unit regression verifies that only `allocated_bytes` is normalized: changing any non-allocation semantic field remains a verification failure.

## Scope correction

The original v0.24 raw allocated-byte values remain valid observations from the initial evidence run. They are not portable exact expectations for another filesystem allocation episode. Sparse logical file length, arithmetic page addresses, exact crash snapshots, committed epochs, user-space `pread`/`pwrite` counts, migration bounds, and collision-rejection semantics remain exact verification obligations.

This correction does not strengthen v0.24 into a filesystem space-reclamation claim and does not weaken its crash-atomicity or locality falsification criteria.
