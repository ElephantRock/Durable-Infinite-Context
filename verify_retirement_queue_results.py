from __future__ import annotations

import hashlib
import json
from pathlib import Path

from run_retirement_queue_experiment import run

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "retirement_queue_results.json"
EXPECTED_SHA256 = "5441d47b882d4f16545e63c18c910885c509cf46aeb2a8b32860efd6d372a213"


def _assert_recovery_rows(matrix: dict) -> None:
    for row in matrix["rows"]:
        if not row["exact_committed_state_match"]:
            raise AssertionError("v0.34 crash matrix contains a non-exact state")
        for name in ("recovery_one", "recovery_two"):
            recovery = row[name]
            if int(recovery["logical_work"]) != 0:
                raise AssertionError("v0.34 recovery required logical redo")
            if int(recovery["generation_pages_scanned"]) != 0:
                raise AssertionError("v0.34 recovery scanned generation pages")
            if int(recovery["mapping_nodes_scanned"]) != 0:
                raise AssertionError("v0.34 recovery scanned radix nodes")
            if int(recovery["retirement_descriptors_scanned"]) != 0:
                raise AssertionError("v0.34 recovery scanned retirement descriptors")
        if int(row["recovery_two"]["physical_truncated_bytes"]) != 0:
            raise AssertionError("v0.34 second recovery is not physically idempotent")


def main() -> None:
    committed = RESULTS.read_bytes()
    digest = hashlib.sha256(committed).hexdigest()
    if digest != EXPECTED_SHA256:
        raise AssertionError(f"v0.34 result hash drifted: {digest}")
    payload = json.loads(committed)

    guard = payload["semantic_guard"]
    if not all(bool(value) for value in guard.values()):
        raise AssertionError("v0.34 semantic guard is not fully true")

    controls = payload["enqueue_scaling_controls"]
    if controls["queued_generation_counts"] != [1, 4, 16, 64, 256]:
        raise AssertionError("v0.34 queue-control sizes drifted")
    flat = [int(row["flat_manifest_bytes"]) for row in controls["rows"]]
    walks = [int(row["naive_tail_walk_descriptor_visits"]) for row in controls["rows"]]
    if flat != [70, 205, 758, 3014, 12351]:
        raise AssertionError("v0.34 flat-manifest control drifted")
    if walks != [1, 4, 16, 64, 256]:
        raise AssertionError("v0.34 tail-walk control drifted")
    if int(controls["candidate_enqueue_descriptor_pages"]) != 2:
        raise AssertionError("v0.34 descriptor page bound drifted")
    if int(controls["candidate_enqueue_max_descriptor_preads"]) != 2:
        raise AssertionError("v0.34 enqueue pread bound drifted")
    if int(controls["candidate_enqueue_max_descriptor_pwrites"]) != 2:
        raise AssertionError("v0.34 enqueue pwrite bound drifted")
    if int(controls["candidate_enqueue_history_walks"]) != 0:
        raise AssertionError("v0.34 enqueue history-walk count drifted")

    cycles = payload["real_queue_cycles"]
    queued = cycles["queue_before_cleanup"]
    if int(queued["queue_count"]) != 3:
        raise AssertionError("v0.34 real queue depth drifted")
    generations = [int(row["generation"]) for row in queued["descriptors"]]
    remaining = [int(row["remaining_segments"]) for row in queued["descriptors"]]
    if generations != [0, 1, 2] or remaining != [1, 1, 2]:
        raise AssertionError("v0.34 real queue order/counts drifted")
    if int(cycles["max_enqueue_descriptor_preads"]) != 2:
        raise AssertionError("v0.34 observed enqueue preads drifted")
    if int(cycles["max_enqueue_descriptor_pwrites"]) != 2:
        raise AssertionError("v0.34 observed enqueue pwrites drifted")
    if int(cycles["max_enqueue_descriptor_pages"]) != 2:
        raise AssertionError("v0.34 observed descriptor append drifted")
    if int(cycles["max_reclaim_segments_per_step"]) != 2:
        raise AssertionError("v0.34 observed reclaim budget drifted")
    if int(cycles["max_reclaim_descriptor_preads"]) != 2:
        raise AssertionError("v0.34 observed reclaim preads drifted")
    if int(cycles["max_reclaim_descriptor_pwrites"]) > 1:
        raise AssertionError("v0.34 reclaim rewrote multiple descriptors")
    if int(cycles["max_reclaim_pages_appended"]) != 0:
        raise AssertionError("v0.34 reclaim append bound drifted")
    if not cycles["all_129_keys_visible"]:
        raise AssertionError("v0.34 real queue lost live keys")
    reuse = cycles["reuse_traces"]
    if len(reuse) != 1:
        raise AssertionError("v0.34 reuse fixture count drifted")
    if int(reuse[0]["reused_free_extents"]) != 4:
        raise AssertionError("v0.34 reused-extent count drifted")
    if int(reuse[0]["data_page_scrub_pwrites"]) != 128:
        raise AssertionError("v0.34 scrub work drifted")

    enqueue = payload["enqueue_crash_matrices"]
    reclaim = payload["reclaim_crash_matrices"]
    if int(enqueue["empty_queue"]["case_count"]) != 4:
        raise AssertionError("v0.34 empty enqueue crash count drifted")
    if int(enqueue["nonempty_queue"]["case_count"]) != 5:
        raise AssertionError("v0.34 nonempty enqueue crash count drifted")
    if int(reclaim["partial_head"]["case_count"]) != 5:
        raise AssertionError("v0.34 partial reclaim crash count drifted")
    if int(reclaim["dequeue_head"]["case_count"]) != 5:
        raise AssertionError("v0.34 dequeue crash count drifted")
    if int(enqueue["empty_queue"]["clean_trace"]["retirement_descriptor_pwrites"]) != 1:
        raise AssertionError("v0.34 empty enqueue write count drifted")
    nonempty_trace = enqueue["nonempty_queue"]["clean_trace"]
    if int(nonempty_trace["retirement_descriptor_preads"]) != 2:
        raise AssertionError("v0.34 nonempty enqueue read count drifted")
    if int(nonempty_trace["retirement_descriptor_pwrites"]) != 2:
        raise AssertionError("v0.34 nonempty enqueue write count drifted")
    partial_trace = reclaim["partial_head"]["clean_trace"]
    if int(partial_trace["retirement_descriptor_pwrites"]) != 1:
        raise AssertionError("v0.34 partial reclaim descriptor update drifted")
    if int(partial_trace["remaining_segments_in_head"]) != 1:
        raise AssertionError("v0.34 partial reclaim remaining count drifted")
    if int(reclaim["dequeue_head"]["clean_trace"]["retirement_descriptor_pwrites"]) != 0:
        raise AssertionError("v0.34 dequeue unexpectedly rewrote a descriptor")

    for matrix in (
        enqueue["empty_queue"],
        enqueue["nonempty_queue"],
        reclaim["partial_head"],
        reclaim["dequeue_head"],
    ):
        if not matrix["all_exact_committed_state_match"]:
            raise AssertionError("v0.34 crash exactness flag is false")
        if not matrix["all_recovery_scan_free"]:
            raise AssertionError("v0.34 recovery scan-free flag is false")
        if not matrix["all_second_recovery_idempotent"]:
            raise AssertionError("v0.34 second-recovery idempotence flag is false")
        _assert_recovery_rows(matrix)

    reproduced = run()
    reproduced_bytes = (json.dumps(reproduced, indent=2, sort_keys=True) + "\n").encode("utf-8")
    reproduced_digest = hashlib.sha256(reproduced_bytes).hexdigest()
    if reproduced_digest != EXPECTED_SHA256:
        raise AssertionError(
            f"v0.34 executable reproduction drifted: {reproduced_digest}"
        )
    if reproduced_bytes != committed:
        raise AssertionError("v0.34 executable reproduction is not byte-exact")

    print(
        "verified v0.34 retirement queue: "
        f"sha256={EXPECTED_SHA256}, crash_cases=19, queue_depth=3"
    )


if __name__ == "__main__":
    main()
