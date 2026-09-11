from __future__ import annotations

import hashlib
import json
from pathlib import Path

from run_recyclable_retirement_descriptor_experiment import run

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "recyclable_retirement_descriptor_results.json"
EXPECTED_SHA256 = "f571c4d986c1c1a5b85cb9b2e3fa89583c1eb3fab2fbcdfc36cbdd869f86802f"

FRESH_EMPTY_FAILPOINTS = (
    "retirement_descriptor_written",
    "pages_written",
    "data_synced",
    "committed",
)
FRESH_NONEMPTY_FAILPOINTS = (
    "retirement_descriptor_written",
    "retirement_tail_linked",
    "pages_written",
    "data_synced",
    "committed",
)
REUSE_EMPTY_FAILPOINTS = (
    "retirement_descriptor_reused",
    "pages_written",
    "data_synced",
    "committed",
)
REUSE_NONEMPTY_FAILPOINTS = (
    "retirement_descriptor_reused",
    "retirement_tail_linked",
    "pages_written",
    "data_synced",
    "committed",
)
PARTIAL_RECLAIM_FAILPOINTS = (
    "mapping_unlinked",
    "free_header_written",
    "retirement_descriptor_updated",
    "dependencies_synced",
    "committed",
)
DEQUEUE_RECYCLE_FAILPOINTS = (
    "mapping_unlinked",
    "free_header_written",
    "retirement_descriptor_freed",
    "retirement_dequeued",
    "dependencies_synced",
    "committed",
)


def _assert_recovery_rows(matrix: dict) -> None:
    for row in matrix["rows"]:
        if not row["exact_committed_state_match"]:
            raise AssertionError("v0.35 crash matrix contains a non-exact state")
        for name in ("recovery_one", "recovery_two"):
            recovery = row[name]
            if int(recovery["logical_work"]) != 0:
                raise AssertionError("v0.35 recovery required logical redo")
            if int(recovery["generation_pages_scanned"]) != 0:
                raise AssertionError("v0.35 recovery scanned generation pages")
            if int(recovery["mapping_nodes_scanned"]) != 0:
                raise AssertionError("v0.35 recovery scanned radix nodes")
            if int(recovery["retirement_descriptors_scanned"]) != 0:
                raise AssertionError("v0.35 recovery scanned retirement descriptors")
        if int(row["recovery_two"]["physical_truncated_bytes"]) != 0:
            raise AssertionError("v0.35 second recovery is not physically idempotent")


def _assert_matrix(matrix: dict, failpoints: tuple[str, ...], case_count: int) -> None:
    if matrix["failpoints"] != list(failpoints):
        raise AssertionError("v0.35 crash failpoint set drifted")
    if int(matrix["case_count"]) != case_count:
        raise AssertionError("v0.35 crash case count drifted")
    if not matrix["all_exact_committed_state_match"]:
        raise AssertionError("v0.35 crash exactness flag is false")
    if not matrix["all_recovery_scan_free"]:
        raise AssertionError("v0.35 recovery scan-free flag is false")
    if not matrix["all_second_recovery_idempotent"]:
        raise AssertionError("v0.35 second-recovery idempotence flag is false")
    _assert_recovery_rows(matrix)


def main() -> None:
    committed = RESULTS.read_bytes()
    digest = hashlib.sha256(committed).hexdigest()
    if digest != EXPECTED_SHA256:
        raise AssertionError(f"v0.35 result hash drifted: {digest}")
    payload = json.loads(committed)

    if payload["experiment"] != "v0.35_recyclable_retirement_descriptors":
        raise AssertionError("v0.35 experiment identity drifted")
    guard = payload["semantic_guard"]
    if not all(bool(value) for value in guard.values()):
        raise AssertionError("v0.35 semantic guard is not fully true")

    controls = payload["storage_controls"]
    if controls["completed_generation_counts"] != [1, 4, 16, 64, 256]:
        raise AssertionError("v0.35 storage-control sizes drifted")
    if controls["candidate_reference_shape"] != "(base_page, incarnation)":
        raise AssertionError("v0.35 descriptor identity shape drifted")
    if int(controls["candidate_reuse_incarnation_delta"]) != 1:
        raise AssertionError("v0.35 descriptor incarnation delta drifted")
    if int(controls["candidate_history_walks"]) != 0:
        raise AssertionError("v0.35 candidate history-walk count drifted")
    appended = [int(row["append_only_descriptor_pages"]) for row in controls["rows"]]
    serial_peak = [
        int(row["recyclable_serial_peak_descriptor_pages"]) for row in controls["rows"]
    ]
    history_walks = [
        int(row["recyclable_enqueue_history_walks"]) for row in controls["rows"]
    ]
    if appended != [2, 8, 32, 128, 512]:
        raise AssertionError("v0.35 append-only storage control drifted")
    if serial_peak != [2, 2, 2, 2, 2]:
        raise AssertionError("v0.35 recyclable serial peak drifted")
    if history_walks != [0, 0, 0, 0, 0]:
        raise AssertionError("v0.35 recyclable history-walk control drifted")

    cycles = payload["real_recycling_cycles"]
    if int(cycles["initial_descriptor_pages_appended"]) != 6:
        raise AssertionError("v0.35 initial descriptor-pool allocation drifted")
    if int(cycles["descriptor_pages_appended_after_pool_established"]) != 0:
        raise AssertionError("v0.35 descriptor pool resumed append after reuse capacity existed")
    if int(cycles["completed_generations_represented"]) != 5:
        raise AssertionError("v0.35 completed-generation fixture drifted")
    if int(cycles["descriptor_pool_count_after_five_generations"]) != 3:
        raise AssertionError("v0.35 descriptor pool grew with generation history")
    if not cycles["all_258_keys_visible"]:
        raise AssertionError("v0.35 real recycling cycle lost live keys")
    if int(cycles["first_reuse_index"]) != 128:
        raise AssertionError("v0.35 first descriptor reuse trigger drifted")
    if int(cycles["second_reuse_index"]) != 257:
        raise AssertionError("v0.35 bounded migration completion trigger drifted")

    initial = cycles["initial_queue"]
    if int(initial["queue_count"]) != 3 or int(initial["descriptor_pool_count"]) != 3:
        raise AssertionError("v0.35 initial descriptor pool depth drifted")
    generations = [int(row["generation"]) for row in initial["descriptors"]]
    remaining = [int(row["remaining_segments"]) for row in initial["descriptors"]]
    incarnations = [int(row["descriptor_incarnation"]) for row in initial["descriptors"]]
    if generations != [0, 1, 2] or remaining != [1, 1, 2]:
        raise AssertionError("v0.35 initial queue order/counts drifted")
    if incarnations != [1, 1, 1]:
        raise AssertionError("v0.35 initial descriptor incarnation drifted")

    drained = cycles["drained_queue"]
    if int(drained["queue_count"]) != 0:
        raise AssertionError("v0.35 drained queue is not empty")
    if int(drained["descriptor_free_count"]) != 3:
        raise AssertionError("v0.35 drained descriptors did not enter free pool")
    if int(drained["descriptor_pool_count"]) != 3:
        raise AssertionError("v0.35 descriptor pool depth changed during drain")

    after_first = cycles["queue_after_first_reuse"]
    if int(after_first["queue_count"]) != 1 or int(after_first["descriptor_free_count"]) != 2:
        raise AssertionError("v0.35 first reuse queue/free transition drifted")
    first = cycles["first_reuse_trace"]
    if int(first["retirement_descriptor_reuses"]) != 1:
        raise AssertionError("v0.35 first reuse count drifted")
    if int(first["retirement_descriptor_pages_appended"]) != 0:
        raise AssertionError("v0.35 first reuse appended descriptor pages")
    if int(first["retirement_descriptor_preads"]) != 2:
        raise AssertionError("v0.35 first reuse read bound drifted")
    if int(first["retirement_descriptor_pwrites"]) != 1:
        raise AssertionError("v0.35 first reuse write bound drifted")

    after_second = cycles["queue_after_second_reuse"]
    if int(after_second["queue_count"]) != 2 or int(after_second["descriptor_free_count"]) != 1:
        raise AssertionError("v0.35 second reuse queue/free transition drifted")
    if [int(row["generation"]) for row in after_second["descriptors"]] != [3, 4]:
        raise AssertionError("v0.35 recycled queue order drifted")
    second = cycles["second_reuse_trace"]
    if int(second["retirement_descriptor_reuses"]) != 1:
        raise AssertionError("v0.35 second reuse count drifted")
    if int(second["retirement_descriptor_pages_appended"]) != 0:
        raise AssertionError("v0.35 second reuse appended descriptor pages")
    if int(second["retirement_descriptor_preads"]) != 4:
        raise AssertionError("v0.35 non-empty reuse read bound drifted")
    if int(second["retirement_descriptor_pwrites"]) != 2:
        raise AssertionError("v0.35 non-empty reuse write bound drifted")

    stale = cycles["stale_reference"]
    if not stale["stale_reference_rejected"]:
        raise AssertionError("v0.35 stale tagged reference was accepted")
    if int(stale["recycled_incarnation"]) != int(stale["descriptor_incarnation"]) + 1:
        raise AssertionError("v0.35 descriptor incarnation did not advance exactly once")
    if int(stale["recycled_generation"]) != 3:
        raise AssertionError("v0.35 recycled descriptor resolved to wrong generation")
    if "incarnation mismatch" not in str(stale["stale_reference_error"]):
        raise AssertionError("v0.35 stale-reference rejection reason drifted")

    crashes = payload["crash_matrices"]
    if int(crashes["case_count"]) != 29:
        raise AssertionError("v0.35 total crash case count drifted")
    matrix_specs = (
        ("fresh_empty_queue", FRESH_EMPTY_FAILPOINTS, 4),
        ("fresh_nonempty_queue", FRESH_NONEMPTY_FAILPOINTS, 5),
        ("empty_queue_reuse", REUSE_EMPTY_FAILPOINTS, 4),
        ("nonempty_queue_reuse", REUSE_NONEMPTY_FAILPOINTS, 5),
        ("partial_head", PARTIAL_RECLAIM_FAILPOINTS, 5),
        ("dequeue_to_free", DEQUEUE_RECYCLE_FAILPOINTS, 6),
    )
    for name, failpoints, count in matrix_specs:
        _assert_matrix(crashes[name], failpoints, count)

    fresh_empty = crashes["fresh_empty_queue"]["clean_trace"]
    if int(fresh_empty["retirement_descriptor_reuses"]) != 0:
        raise AssertionError("v0.35 fresh empty enqueue unexpectedly reused a descriptor")
    if int(fresh_empty["retirement_descriptor_pages_appended"]) != 2:
        raise AssertionError("v0.35 fresh empty enqueue append count drifted")
    if int(fresh_empty["retirement_descriptor_pwrites"]) != 1:
        raise AssertionError("v0.35 fresh empty enqueue write count drifted")

    fresh_nonempty = crashes["fresh_nonempty_queue"]["clean_trace"]
    if int(fresh_nonempty["retirement_descriptor_reuses"]) != 0:
        raise AssertionError("v0.35 fresh non-empty enqueue unexpectedly reused a descriptor")
    if int(fresh_nonempty["retirement_descriptor_pages_appended"]) != 2:
        raise AssertionError("v0.35 fresh non-empty enqueue append count drifted")
    if int(fresh_nonempty["retirement_descriptor_preads"]) != 2:
        raise AssertionError("v0.35 fresh non-empty enqueue read count drifted")
    if int(fresh_nonempty["retirement_descriptor_pwrites"]) != 2:
        raise AssertionError("v0.35 fresh non-empty enqueue write count drifted")

    reuse_empty = crashes["empty_queue_reuse"]["clean_trace"]
    if int(reuse_empty["retirement_descriptor_reuses"]) != 1:
        raise AssertionError("v0.35 empty-queue reuse count drifted")
    if int(reuse_empty["retirement_descriptor_pages_appended"]) != 0:
        raise AssertionError("v0.35 empty-queue reuse appended descriptor pages")
    if int(reuse_empty["retirement_descriptor_preads"]) != 2:
        raise AssertionError("v0.35 empty-queue reuse read count drifted")
    if int(reuse_empty["retirement_descriptor_pwrites"]) != 1:
        raise AssertionError("v0.35 empty-queue reuse write count drifted")

    reuse_nonempty = crashes["nonempty_queue_reuse"]["clean_trace"]
    if int(reuse_nonempty["retirement_descriptor_reuses"]) != 1:
        raise AssertionError("v0.35 non-empty reuse count drifted")
    if int(reuse_nonempty["retirement_descriptor_pages_appended"]) != 0:
        raise AssertionError("v0.35 non-empty reuse appended descriptor pages")
    if int(reuse_nonempty["retirement_descriptor_preads"]) != 4:
        raise AssertionError("v0.35 non-empty reuse read count drifted")
    if int(reuse_nonempty["retirement_descriptor_pwrites"]) != 2:
        raise AssertionError("v0.35 non-empty reuse write count drifted")

    partial = crashes["partial_head"]
    if not partial["all_live_keys_visible"]:
        raise AssertionError("v0.35 partial reclaim crash hid live keys")
    partial_trace = partial["clean_trace"]
    if int(partial_trace["retirement_descriptor_pwrites"]) != 1:
        raise AssertionError("v0.35 partial reclaim descriptor update drifted")
    if int(partial_trace["retirement_descriptor_pages_recycled"]) != 0:
        raise AssertionError("v0.35 partial reclaim recycled a live descriptor")
    if int(partial_trace["remaining_segments_in_head"]) != 1:
        raise AssertionError("v0.35 partial reclaim remaining count drifted")

    dequeue = crashes["dequeue_to_free"]
    if not dequeue["all_live_keys_visible"]:
        raise AssertionError("v0.35 dequeue crash hid live keys")
    dequeue_trace = dequeue["clean_trace"]
    if int(dequeue_trace["retirement_descriptor_pwrites"]) != 1:
        raise AssertionError("v0.35 dequeue did not write FREE descriptor state")
    if int(dequeue_trace["retirement_descriptor_pages_recycled"]) != 2:
        raise AssertionError("v0.35 dequeue recycle page count drifted")
    if int(dequeue_trace["remaining_segments_in_head"]) != 0:
        raise AssertionError("v0.35 dequeue left retirement work in the head")

    reproduced = run()
    reproduced_bytes = (json.dumps(reproduced, indent=2, sort_keys=True) + "\n").encode("utf-8")
    reproduced_digest = hashlib.sha256(reproduced_bytes).hexdigest()
    if reproduced_digest != EXPECTED_SHA256:
        raise AssertionError(
            f"v0.35 executable reproduction drifted: {reproduced_digest}"
        )
    if reproduced_bytes != committed:
        raise AssertionError("v0.35 executable reproduction is not byte-exact")

    print(
        "verified v0.35 recyclable retirement descriptors: "
        f"sha256={EXPECTED_SHA256}, crash_cases=29, descriptor_pool=3"
    )


if __name__ == "__main__":
    main()
