from pathlib import Path

path = Path("README.md")
text = path.read_text()


def once(old: str, new: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one README match, found {count}: {old[:80]!r}")
    text = text.replace(old, new, 1)


once(
    "# Durable Infinite Context — Minimum Falsifiable Prototype v0.33",
    "# Durable Infinite Context — Minimum Falsifiable Prototype v0.34",
)
once(
    "The v0.18–v0.33 hash/cuckoo/overflow/fixed-page structures remain **experimental alternatives**, not replacements for that production B-tree. They have progressively earned bounded migration scheduling, bounded modeled placement work, explicit rare overflow, crash-atomic hybrid admission, arithmetic-addressed fixed pages, cross-store visibility gating, interrupted-recovery convergence, an explicit volatile/durable persistence-ordering model, a modeled bounded-address segment map, an integrated append-local segmented primary, fixed-width radix nodes, fixed-budget mapping-lifecycle reclamation with explicit stale-payload isolation on reuse, and now segment-aligned real generation ownership.",
    "The v0.18–v0.34 hash/cuckoo/overflow/fixed-page structures remain **experimental alternatives**, not replacements for that production B-tree. They have progressively earned bounded migration scheduling, bounded modeled placement work, explicit rare overflow, crash-atomic hybrid admission, arithmetic-addressed fixed pages, cross-store visibility gating, interrupted-recovery convergence, an explicit volatile/durable persistence-ordering model, a modeled bounded-address segment map, an integrated append-local segmented primary, fixed-width radix nodes, fixed-budget mapping-lifecycle reclamation with explicit stale-payload isolation on reuse, segment-aligned real generation ownership, and now bounded FIFO publication of multiple retired-generation backlogs.",
)
once(
    "Two real growth cycles preserve all keys, safely reuse a reclaimed extent after the fixed 32-page scrub, and pass 12/12 process-crash cases with scan-free recovery.",
    "Two real growth cycles preserve all keys, safely reuse a reclaimed extent after the fixed 32-page scrub, and pass 12/12 process-crash cases with scan-free recovery. v0.34 removes the resulting one-backlog admission restriction with fixed dual-copy retirement descriptors rooted by superblock head/tail/count scalars. Three real retired generations accumulate without cleanup; enqueue stays at one two-page descriptor plus at most one tail rewrite, fixed-budget reclaim touches only the FIFO head, and 19/19 queue publication/reclaim crash cases expose exact committed state with retirement-queue-scan-free recovery.",
)
once(
    "- hardware power-loss or torn-write safety from the v0.30–v0.33 process-crash results;",
    "- hardware power-loss or torn-write safety from the v0.30–v0.34 process-crash results;",
)
once(
    "- nonblocking progress when more than one completed generation is awaiting reclamation; v0.33 intentionally permits one retirement backlog at a time;",
    "- constant total retirement-descriptor storage; v0.34 descriptors are append-only and grow with completed-generation history;\n- a real-primary retirement queue depth beyond three simultaneously queued generations; larger v0.34 depths are deterministic controls;",
)
once(
    "| v0.33 | Can real primary-generation retirement use whole-segment ownership safely? | Back-to-back generations share a 16-page mapping segment; segment-aligning new bases costs at most 15 logical ids, removes the overlap, preserves two real growth cycles and reuse, and passes 12/12 exact process-crash cases. |",
    "| v0.33 | Can real primary-generation retirement use whole-segment ownership safely? | Back-to-back generations share a 16-page mapping segment; segment-aligning new bases costs at most 15 logical ids, removes the overlap, preserves two real growth cycles and reuse, and passes 12/12 exact process-crash cases. |\n| v0.34 | Can multiple completed generations queue for reclamation without blocking later migration? | A dual-copy FIFO descriptor queue accumulates three real retired generations without cleanup; enqueue uses one two-page descriptor plus at most one tail rewrite, reclaim stays head-local under `B=2`, and 19/19 queue crash cases are exact with queue-scan-free recovery. |",
)

section = r'''### v0.34 — bounded FIFO retirement publication

v0.34 removes v0.33's rule that one retired generation must be fully reclaimed before another migration can start. Each completed generation now receives one fixed retirement descriptor containing its generation id, intrusive ownership cursor, remaining segment count, and next-descriptor pointer. Descriptors have two committed-epoch copies; the superblock stores only queue head, tail, and count.

The deterministic enqueue controls compare a flat serialized manifest and a head-to-tail linked enqueue against the candidate:

| Queued generations | Flat manifest bytes | Naive tail-walk visits | Candidate descriptor pages | Candidate max descriptor preads | Candidate max descriptor pwrites |
|---:|---:|---:|---:|---:|---:|
| 1 | 70 | 1 | 2 | 2 | 2 |
| 4 | 205 | 4 | 2 | 2 | 2 |
| 16 | 758 | 16 | 2 | 2 | 2 |
| 64 | 3,014 | 64 | 2 | 2 | 2 |
| 256 | 12,351 | 256 | 2 | 2 | 2 |

A real primary run reaches three simultaneously retired generations `[0,1,2]` with materialized-segment counts `[1,1,2]` before any cleanup. Enqueue performs zero history walks and appends exactly two descriptor pages per completed generation. With `B=2`, reclaim follows only the FIFO head and at most two ownership links, appends zero pages, performs at most two descriptor `pread`s and at most one descriptor `pwrite`, and drains generations in order.

After cleanup, the next real migration reuses four reclaimed extents only after **128 scrub writes** — exactly 32 data pages per extent — and all 129 live keys remain visible. The crash evidence covers **19/19 exact cases** across empty-queue enqueue, non-empty enqueue with tail linking, partial-head cursor update, and head dequeue. Recovery performs zero generation-page scan, zero radix-node scan, zero retirement-descriptor scan, and zero logical redo, and a second recovery performs zero additional truncation.

The result is a bounded-foreground-work claim, not a constant-storage claim. Descriptor pages are append-only in v0.34, and the real-primary queue depth exercised is three; the larger queue counts above are arithmetic/control cases.

'''
once("## Reproducing the hardened path", section + "## Reproducing the hardened path")
once(
    "python run_generation_boundary_reclamation_experiment.py\npython verify_scanfree_cascade_results.py",
    "python run_generation_boundary_reclamation_experiment.py\npython run_retirement_queue_experiment.py\npython verify_scanfree_cascade_results.py",
)
once(
    "python verify_generation_boundary_reclamation_results.py\n```",
    "python verify_generation_boundary_reclamation_results.py\npython verify_retirement_queue_results.py\n```",
)
once(
    "  -> real migration completion publishes retired ownership for bounded cleanup\n```",
    "  -> real migration completion publishes retired ownership for bounded cleanup\n  -> dual-copy FIFO retirement descriptors decouple later migrations from earlier cleanup\n  -> superblock queue roots keep enqueue head/tail-local and recovery queue-scan-free\n```",
)

marker = "## Next falsification target — multiple retirement backlogs"
if marker not in text:
    raise SystemExit("v0.33 next-target section not found")
text = text.split(marker, 1)[0] + r'''## Next falsification target — descriptor recycling and identity safety

v0.34 removes cleanup lag from later migration admission, but its retirement descriptors are append-only. Completed and dequeued descriptors become historical residue even after all of their owned segments have been reclaimed.

The next question is:

\[
\boxed{
Can retirement descriptors themselves be recycled under a fixed foreground budget
without ABA-style identity ambiguity, queue-history scans, or crash resurrection?
}
\]

v0.35 should compare append-only descriptor history against a bounded descriptor free-list or generation-tagged recycling scheme. It must force descriptor reuse after dequeue, crash before and after recycled identity publication, and verify that stale queue pointers cannot be interpreted as a newer descriptor incarnation. Reject the mechanism if recycling requires a history walk, if descriptor identity can alias after crash, if enqueue/reclaim work grows with descriptor history, or if recovery must scan retired descriptors to determine which incarnation is authoritative.
'''

path.write_text(text)
