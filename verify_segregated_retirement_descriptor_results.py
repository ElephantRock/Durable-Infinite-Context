from __future__ import annotations

import hashlib
import json

from run_segregated_retirement_descriptor_experiment import RESULTS_PATH, run

EXPECTED_SHA256 = "15b5beb9b9065e4231ab7455066729bb0ba431ea8689d25049c120e7a2ad6135"
EXPECTED_HISTORY_TRIGGERS = [16, 32, 64, 128, 256]
EXPECTED_FAILPOINTS = {
    "fresh_empty_queue": [
        "retirement_arena_descriptor_written",
        "retirement_arena_synced",
        "pages_written",
        "data_synced",
        "committed",
    ],
    "fresh_nonempty_queue": [
        "retirement_arena_descriptor_written",
        "retirement_tail_linked",
        "retirement_arena_synced",
        "pages_written",
        "data_synced",
        "committed",
    ],
    "partial_head": [
        "mapping_unlinked",
        "free_header_written",
        "retirement_descriptor_updated",
        "retirement_arena_synced",
        "dependencies_synced",
        "committed",
    ],
    "final_arena_reset": [
        "mapping_unlinked",
        "free_header_written",
        "retirement_arena_reset_staged",
        "retirement_dequeued",
        "dependencies_synced",
        "committed",
        "retirement_arena_reset_committed",
        "retirement_arena_truncated",
        "retirement_arena_reset_synced",
    ],
}


def _require_scan_free(recovery: dict) -> None:
    if int(recovery["logical_work"]) != 0:
        raise AssertionError("v0.37 recovery required logical redo")
    if int(recovery["generation_pages_scanned"]) != 0:
        raise AssertionError("v0.37 recovery scanned generation pages")
    if int(recovery["mapping_nodes_scanned"]) != 0:
        raise AssertionError("v0.37 recovery scanned radix nodes")
    if int(recovery.get("retirement_descriptors_scanned", 0)) != 0:
        raise AssertionError("v0.37 recovery scanned retirement descriptors")


def _validate(payload: dict) -> None:
    if payload["experiment"] != "v0.37_segregated_retirement_descriptor_arena":
        raise AssertionError("unexpected v0.37 experiment id")
    semantic = payload["semantic_guard"]
    for name in (
        "membership_equal",
        "materialization_equal",
        "head_index_equal",
        "all_derived_fresh",
        "full_assembly_equal",
        "partial_assembly_equal",
    ):
        if not bool(semantic[name]):
            raise AssertionError(f"v0.37 semantic guard failed: {name}")

    candidate = payload["segregated_arena"]
    if not bool(candidate["survived"]):
        raise AssertionError("v0.37 candidate no longer survives")
    if int(candidate["candidate_descriptor_history_walks"]) != 0:
        raise AssertionError("v0.37 candidate history-walk count drifted")
    if int(candidate["candidate_relocations"]) != 0:
        raise AssertionError("v0.37 candidate relocation count drifted")

    peak = candidate["peak_release"]
    expected_peak = {
        "peak_descriptor_pairs": 3,
        "peak_arena_pages": 6,
        "peak_arena_bytes": 24576,
        "released_arena_pages": 6,
        "released_arena_bytes": 24576,
        "candidate_descriptor_history_walks": 0,
        "candidate_relocations": 0,
    }
    for name, expected in expected_peak.items():
        if int(peak[name]) != expected:
            raise AssertionError(f"v0.37 peak field drifted: {name}")
    if int(peak["queue_before_drain"]["descriptor_pool_count"]) != 3:
        raise AssertionError("v0.37 peak descriptor pool drifted")
    if int(peak["queue_before_drain"]["descriptor_arena_pages"]) != 6:
        raise AssertionError("v0.37 peak committed arena length drifted")
    if int(peak["queue_after_drain"]["queue_count"]) != 0:
        raise AssertionError("v0.37 queue did not drain")
    if int(peak["queue_after_drain"]["descriptor_pool_count"]) != 0:
        raise AssertionError("v0.37 descriptor identities survived full drain")
    if int(peak["queue_after_drain"]["descriptor_arena_pages"]) != 0:
        raise AssertionError("v0.37 committed arena pages survived full drain")
    if int(peak["arena_after_drain"]["arena_file_bytes"]) != 0:
        raise AssertionError("v0.37 sidecar file length survived full drain")
    for row in peak["reclaim_steps"]:
        if int(row["retirement_descriptors_scanned"]) != 0:
            raise AssertionError("v0.37 reclaim trace scanned descriptor history")
        if int(row["reclaimed_segments"]) > 2:
            raise AssertionError("v0.37 reclaim exceeded fixed segment budget")

    identity = candidate["identity_after_reset"]
    exact_identity = {
        "first_page": 0,
        "first_incarnation": 1,
        "second_page": 0,
        "second_incarnation": 2,
        "second_trigger_key_index": 32,
        "current_identity_generation": 1,
    }
    for name, expected in exact_identity.items():
        if int(identity[name]) != expected:
            raise AssertionError(f"v0.37 identity field drifted: {name}")
    for name in (
        "same_arena_page_reused",
        "incarnation_advanced",
        "stale_identity_rejected",
    ):
        if not bool(identity[name]):
            raise AssertionError(f"v0.37 identity invariant failed: {name}")

    history = candidate["history_cycles"]
    if int(history["cycle_count"]) != 5:
        raise AssertionError("v0.37 history cycle count drifted")
    triggers = [int(row["trigger_index"]) for row in history["cycles"]]
    if triggers != EXPECTED_HISTORY_TRIGGERS:
        raise AssertionError("v0.37 history trigger sequence drifted")
    for row in history["cycles"]:
        if int(row["arena_pages_before_drain"]) != 2:
            raise AssertionError("v0.37 serial cycle did not allocate one descriptor pair")
        if int(row["arena_pages_after_drain"]) != 0:
            raise AssertionError("v0.37 serial cycle retained committed arena pages")
        if int(row["arena_file_bytes_after_drain"]) != 0:
            raise AssertionError("v0.37 serial cycle retained sidecar file length")
    if not bool(history["all_cycles_return_to_zero_committed_arena_pages"]):
        raise AssertionError("v0.37 history committed-arena reset invariant failed")
    if not bool(history["all_cycles_return_to_zero_arena_file_bytes"]):
        raise AssertionError("v0.37 history file-length reset invariant failed")

    crashes = candidate["crash_matrices"]
    if int(crashes["case_count"]) != 26:
        raise AssertionError("v0.37 crash case count drifted")
    for name, failpoints in EXPECTED_FAILPOINTS.items():
        case = crashes[name]
        if list(case["failpoints"]) != failpoints:
            raise AssertionError(f"v0.37 failpoint list drifted: {name}")
        if int(case["case_count"]) != len(failpoints):
            raise AssertionError(f"v0.37 crash case count drifted: {name}")
        if not bool(case["all_exact_committed_state_match"]):
            raise AssertionError(f"v0.37 crash committed-state mismatch: {name}")
        if not bool(case["all_recovery_scan_free"]):
            raise AssertionError(f"v0.37 crash recovery scan regression: {name}")
        if not bool(case["all_second_recovery_idempotent"]):
            raise AssertionError(f"v0.37 crash recovery idempotence regression: {name}")
        for row in case["rows"]:
            _require_scan_free(row["recovery_one"])
            _require_scan_free(row["recovery_two"])
            if int(row["recovery_two"]["physical_truncated_bytes"]) != 0:
                raise AssertionError("v0.37 second recovery changed primary file length")
            if int(row["recovery_two"]["retirement_descriptor_arena_truncated_bytes"]) != 0:
                raise AssertionError("v0.37 second recovery changed sidecar file length")

    reset_rows = {
        row["failpoint"]: row for row in crashes["final_arena_reset"]["rows"]
    }
    for name in (
        "mapping_unlinked",
        "free_header_written",
        "retirement_arena_reset_staged",
        "retirement_dequeued",
        "dependencies_synced",
    ):
        row = reset_rows[name]
        if bool(row["expected_committed"]):
            raise AssertionError(f"v0.37 reset pre-commit failpoint misclassified: {name}")
        if int(row["arena_before_recovery"]["committed_arena_bytes"]) != 24576:
            raise AssertionError(f"v0.37 pre-commit reset target drifted: {name}")
        if int(row["recovery_one"]["retirement_descriptor_arena_truncated_bytes"]) != 0:
            raise AssertionError(f"v0.37 pre-commit reset incorrectly truncated arena: {name}")

    for name in ("committed", "retirement_arena_reset_committed"):
        row = reset_rows[name]
        if not bool(row["expected_committed"]):
            raise AssertionError(f"v0.37 reset post-commit failpoint misclassified: {name}")
        if int(row["arena_before_recovery"]["arena_file_bytes"]) != 24576:
            raise AssertionError(f"v0.37 reset residue size drifted: {name}")
        if int(row["arena_before_recovery"]["committed_arena_bytes"]) != 0:
            raise AssertionError(f"v0.37 reset committed target is not zero: {name}")
        if int(row["recovery_one"]["retirement_descriptor_arena_truncated_bytes"]) != 24576:
            raise AssertionError(f"v0.37 recovery did not remove committed reset residue: {name}")

    for name in ("retirement_arena_truncated", "retirement_arena_reset_synced"):
        row = reset_rows[name]
        if not bool(row["expected_committed"]):
            raise AssertionError(f"v0.37 post-truncate failpoint misclassified: {name}")
        if int(row["arena_before_recovery"]["arena_file_bytes"]) != 0:
            raise AssertionError(f"v0.37 post-truncate arena is not zero length: {name}")
        if int(row["recovery_one"]["retirement_descriptor_arena_truncated_bytes"]) != 0:
            raise AssertionError(f"v0.37 post-truncate recovery changed arena length: {name}")


def main() -> None:
    payload = run()
    _validate(payload)
    canonical = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    raw = canonical.encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    if digest != EXPECTED_SHA256:
        raise AssertionError(f"v0.37 canonical SHA-256 drifted: {digest}")
    generated = RESULTS_PATH.read_bytes()
    if generated != raw:
        raise AssertionError("v0.37 generated result bytes are not canonical")
    if hashlib.sha256(generated).hexdigest() != EXPECTED_SHA256:
        raise AssertionError("v0.37 generated result SHA-256 drifted")


if __name__ == "__main__":
    main()
