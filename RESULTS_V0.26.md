# v0.26 — Recovery interruption

## Observe

v0.25 established that completed cross-store cleanup is idempotent, but it did not kill the cleanup procedure itself. The remaining durability question was whether recovery can be interrupted while deleting hidden future-epoch overflow rows or while truncating an uncommitted fixed-page suffix and still converge correctly on restart.

## Diagnose

Completed idempotence is weaker than restart-convergence. A cleanup step can be individually safe when allowed to finish yet still expose a bad intermediate state when the process dies inside that step.

The two independent cleanup mechanisms are:

1. SQLite cleanup of overflow rows with `epoch > committed_primary_epoch`.
2. Fixed-page truncation to the committed monotonic `next_page_id` frontier.

A failure in either mechanism could resurrect abandoned membership, lose committed membership, require logical redo, or leave residue that never converges.

## Derive

The fixed recovery contract is:

\[
\boxed{
InterruptedRecovery
\rightarrow
ExactCommittedSnapshot
\rightarrow
RepeatedRestartConverges
}
\]

with all of the following required:

- no torn committed logical snapshot;
- no loss of pre-existing keys;
- no visibility of abandoned future rows;
- zero application logical redo;
- eventual zero hidden future rows;
- eventual zero stale file-length tail;
- second completed recovery is a cleanup no-op;
- later coordinator-epoch advancement cannot resurrect an abandoned overflow row.

## Hypothesis

The v0.25 future-row deletion and fixed-tail truncation mechanisms are restart-convergent under real process `SIGKILL` at their internal transaction/durability boundaries.

## Fixed experiment

The matrix contains eight cases:

| Residue class | Failpoints | Cases |
|---|---|---:|
| Natural future-overflow residue | DELETE before SQLite commit; DELETE after SQLite commit | 2 |
| Natural fixed-tail residue | after `ftruncate`; after fixed-file `fsync` | 2 |
| Synthetic combined-residue control | all four failpoints | 4 |

The combined-residue fixture is explicitly a control. It exists to test cleanup ordering when both residue classes are present in one recovery pass; it is not represented as a naturally demonstrated v0.25 crash image.

## Evidence anchor

Initial successful CI:

- run: **#248**
- run id: `34232646662`
- implementation head: `eb06b1388577ee285e64a449ee199a6d13e3a49b`
- artifact id: `10059119712`
- artifact digest: `sha256:978e2954fd73cc982922eb895e19f7b7e976a93141b4841a66bc9f92089672b3`
- contained `recovery_interruption_results.json` digest: `sha256:01bf1acc4593bd3573d3df4799de072760c38e6c163eb7fddae68880c7fbc89f`

The complete historical experiment/verifier chain also passed in that run.

## Results

All **8/8** interrupted-recovery cases preserved the exact committed logical snapshot immediately after `SIGKILL`. All retained pre-existing membership, kept abandoned membership invisible, converged after restart to zero hidden future rows and zero stale tail, and ended with a valid membership audit.

Every first and second retry required:

\[
\boxed{ApplicationLogicalRedo=0}
\]

and the second completed retry performed no further future-row deletion or tail reclamation.

### SQLite future-row cleanup

For both the natural future-row fixture and the combined control:

- killing after the DELETE but **before SQLite commit** left one hidden future row, and the next recovery deleted it;
- killing **after SQLite commit** left zero future rows, so the next recovery had nothing to delete.

This is the expected transactional boundary and is the basis for the surviving restart-convergence claim.

### Fixed-tail cleanup

The natural stale-tail fixture began with **139,264 bytes** beyond the committed frontier.

For both `tail_truncated` and `tail_synced`, the process-SIGKILL observation after restart reported zero remaining tail bytes. The combined control behaved the same way after its tail-truncation boundaries.

This is a deliberately narrow result. It establishes the observed file-size state in the tested process-crash environment. It does **not** establish that a truncate performed before explicit `fsync` would survive arbitrary hardware power loss.

### Resurrection control

Every case containing an abandoned future overflow row advanced the coordinator epoch after cleanup. In all such cases:

- the abandoned row remained invisible;
- the replacement admission became visible;
- no abandoned row resurrected.

## Surviving claim

In the tested single-writer process-SIGKILL model, the v0.25 cleanup protocol is restart-convergent at the exercised SQLite DELETE and fixed-file truncation boundaries:

\[
\boxed{
ExactCommittedState
\land
ZeroLogicalRedo
\land
EventualZeroResidue
\land
NoResurrection
}
\]

This strengthens v0.25 from "completed cleanup is idempotent" to "the tested interrupted cleanup paths converge on restart."

## Deliberate non-claims

v0.26 does not establish:

- arbitrary hardware power-loss or torn-write durability;
- persistence of pre-fsync `ftruncate` across power loss;
- SQLite internal WAL-frame or fsync counts;
- filesystem allocated-block or storage-device reclamation cost;
- constant device work from constant syscall counts;
- multi-writer recovery;
- distributed consistency;
- production latency, throughput, or cost.

## Revision

The next storage-layer uncertainty is no longer ordinary process interruption of cleanup. The stronger unresolved boundary is **hardware-style durability ordering and physical write/reclamation accounting**. Any v0.27 experiment should avoid inferring device guarantees from userspace call counts and should either obtain a storage stack that can expose persistence ordering/fault injection or explicitly remain at the filesystem/process abstraction level.
