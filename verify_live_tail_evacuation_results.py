from __future__ import annotations

import hashlib
import json

from run_live_tail_evacuation_experiment import RESULTS_PATH, run

EXPECTED_SHA256 = "dc7081292cdb62928e24f3353ba78422a663afa7421777d8af11a58a58902094"
EXPECTED_FAILPOINTS = [
    "retirement_live_tail_destination_staged",
    "retirement_live_tail_predecessor_staged",
    "retirement_live_tail_dependencies_synced",
    "committed",
    "retirement_live_tail_relocation_committed",
    "retirement_arena_truncated",
    "retirement_live_tail_relocation_synced",
]
EXPECTED_SCALING_COUNTS = [3, 4, 5, 6]


def _pages(snapshot: dict) -> list[int]:
    return [int(row["descriptor_page"]) for row in snapshot["descriptors"]]


def _free_pages(snapshot: dict) -> list[int]:
    return [int(row["descriptor_page"]) for row in snapshot["free_descriptors"]]


def _require_scan_free(recovery: dict) -> None:
    if int(recovery["logical_work"]) != 0:
        raise AssertionError("v0.40 recovery required logical redo")
    if int(recovery["generation_pages_scanned"]) != 0:
        raise AssertionError("v0.40 recovery scanned generation pages")
    if int(recovery["mapping_nodes_scanned"]) != 0:
        raise AssertionError("v0.40 recovery scanned radix nodes")
    if int(recovery.get("retirement_descriptors_scanned", 0)) != 0:
        raise AssertionError("v0.40 recovery scanned retirement descriptors")


def _validate(payload: dict) -> None:
    if payload["experiment"] != "v0.40-live-tail-evacuation":
        raise AssertionError("unexpected v0.40 experiment id")
    if not bool(payload["survived"]):
        raise AssertionError("v0.40 candidate no longer survives")

    control = payload["v039_control"]
    if int(control["keys_inserted"]) != 65:
        raise AssertionError("v0.40 v0.39-control workload trigger drifted")
    if int(control["retained_arena_pages"]) != 6 or int(control["retained_arena_bytes"]) != 24576:
        raise AssertionError("v0.40 v0.39-control retained arena drifted")
    if _pages(control["queue_before"]) != [2, 4] or _free_pages(control["queue_before"]) != [0]:
        raise AssertionError("v0.40 v0.39-control fixture topology drifted")
    if control["queue_before"] != control["queue_after"]:
        raise AssertionError("v0.40 v0.39 control changed committed state")
    control_trace = control["trace"]
    if bool(control_trace["released"]):
        raise AssertionError("v0.40 v0.39 control unexpectedly released live tail")
    if bool(control_trace["tail_was_free"]):
        raise AssertionError("v0.40 v0.39 control no longer exercises a live tail")
    if int(control_trace["tail_page"]) != 4 or int(control_trace["tail_incarnation"]) != 3:
        raise AssertionError("v0.40 v0.39-control live-tail identity drifted")
    if int(control_trace["retirement_descriptor_preads"]) != 2:
        raise AssertionError("v0.40 v0.39 control read-count drifted")
    if int(control_trace["retirement_descriptor_pwrites"]) != 0:
        raise AssertionError("v0.40 v0.39 control wrote descriptor state")
    if int(control_trace["retirement_descriptors_scanned"]) != 0:
        raise AssertionError("v0.40 v0.39 control scanned descriptor history")

    candidate = payload["live_tail_evacuation"]
    if int(candidate["keys_inserted"]) != 65:
        raise AssertionError("v0.40 release workload trigger drifted")
    before = candidate["queue_before"]
    after = candidate["queue_after"]
    if _pages(before) != [2, 4] or _free_pages(before) != [0]:
        raise AssertionError("v0.40 release start topology drifted")
    if (int(before["queue_count"]), int(before["descriptor_free_count"]), int(before["descriptor_arena_pages"])) != (2, 1, 6):
        raise AssertionError("v0.40 release start counts drifted")
    if int(before["tail_page"]) != 4 or int(before["tail_predecessor_page"]) != 2:
        raise AssertionError("v0.40 release start tail authority drifted")
    if int(before["descriptor_free_head_page"]) != 0:
        raise AssertionError("v0.40 release destination is no longer free-head page 0")
    if _pages(after) != [2, 0] or _free_pages(after) != []:
        raise AssertionError("v0.40 relocated queue topology drifted")
    if (int(after["queue_count"]), int(after["descriptor_free_count"]), int(after["descriptor_arena_pages"])) != (2, 0, 4):
        raise AssertionError("v0.40 release end counts drifted")
    if int(after["tail_page"]) != 0 or int(after["tail_predecessor_page"]) != 2:
        raise AssertionError("v0.40 relocated tail authority drifted")
    if after["descriptor_free_head_page"] is not None:
        raise AssertionError("v0.40 relocation left a free-list head")
    if int(candidate["arena_before"]["arena_file_bytes"]) != 24576:
        raise AssertionError("v0.40 physical arena start length drifted")
    if int(candidate["arena_after"]["arena_file_bytes"]) != 16384:
        raise AssertionError("v0.40 physical arena end length drifted")
    if int(candidate["released_arena_pages"]) != 2 or int(candidate["released_arena_bytes"]) != 8192:
        raise AssertionError("v0.40 released amount drifted")
    for name in ("stale_tail_rejected", "stale_destination_rejected"):
        if not bool(candidate[name]):
            raise AssertionError(f"v0.40 tagged-identity invariant failed: {name}")

    trace = candidate["trace"]
    exact_trace = {
        "retirement_arena_pages_before": 6,
        "retirement_arena_pages_after": 4,
        "retirement_arena_pages_released": 2,
        "retirement_arena_bytes_released": 8192,
        "retirement_descriptor_free_count_before": 1,
        "retirement_descriptor_free_count_after": 0,
        "retirement_descriptor_preads": 6,
        "retirement_descriptor_pwrites": 2,
        "retirement_descriptors_scanned": 0,
        "live_descriptor_relocations": 1,
        "retirement_queue_count": 2,
        "tail_page": 4,
        "tail_incarnation": 3,
        "predecessor_page": 2,
        "predecessor_incarnation": 2,
        "destination_page": 0,
        "destination_old_incarnation": 1,
        "destination_new_incarnation": 4,
        "retirement_arena_fsyncs": 2,
    }
    for name, expected in exact_trace.items():
        if int(trace[name]) != expected:
            raise AssertionError(f"v0.40 release trace drifted: {name}")
    if not bool(trace["released"]) or not bool(trace["tail_was_queued"]) or not bool(trace["tail_was_queue_tail"]):
        raise AssertionError("v0.40 trace lost live physical queue-tail relocation")

    refusal = payload["multiple_free_refusal"]
    if int(refusal["keys_inserted"]) != 129:
        raise AssertionError("v0.40 multiple-free workload trigger drifted")
    refusal_before = refusal["queue_before"]
    if _pages(refusal_before) != [4, 6] or _free_pages(refusal_before) != [2, 0]:
        raise AssertionError("v0.40 multiple-free fixture topology drifted")
    if int(refusal_before["descriptor_free_count"]) != 2:
        raise AssertionError("v0.40 multiple-free fixture count drifted")
    if int(refusal_before["tail_page"]) != 6 or int(refusal_before["tail_predecessor_page"]) != 4:
        raise AssertionError("v0.40 multiple-free live-tail authority drifted")
    if refusal["queue_before"] != refusal["queue_after"] or refusal["arena_before"] != refusal["arena_after"]:
        raise AssertionError("v0.40 multiple-free refusal changed state")
    if not bool(refusal["exact_state_unchanged"]) or not bool(refusal["refused_before_descriptor_io"]):
        raise AssertionError("v0.40 multiple-free guard invariant failed")
    refusal_trace = refusal["trace"]
    for name in (
        "retirement_descriptor_preads",
        "retirement_descriptor_pwrites",
        "retirement_descriptors_scanned",
        "live_descriptor_relocations",
        "retirement_arena_pages_released",
        "retirement_arena_bytes_released",
    ):
        if int(refusal_trace[name]) != 0:
            raise AssertionError(f"v0.40 multiple-free refusal work drifted: {name}")
    if bool(refusal_trace["released"]):
        raise AssertionError("v0.40 multiple-free guard unexpectedly released the tail")

    crashes = payload["crash_matrix"]
    if list(crashes["failpoints"]) != EXPECTED_FAILPOINTS:
        raise AssertionError("v0.40 crash failpoint sequence drifted")
    if int(crashes["case_count"]) != len(EXPECTED_FAILPOINTS):
        raise AssertionError("v0.40 crash case count drifted")
    for name in (
        "all_exact_committed_state_match",
        "all_recovery_scan_free",
        "all_second_recovery_idempotent",
    ):
        if not bool(crashes[name]):
            raise AssertionError(f"v0.40 crash aggregate failed: {name}")
    rows = {row["failpoint"]: row for row in crashes["cases"]}
    for name in EXPECTED_FAILPOINTS[:3]:
        row = rows[name]
        if row["expected_state"] != "pre":
            raise AssertionError(f"v0.40 pre-commit failpoint misclassified: {name}")
        if int(row["first_recovery"]["retirement_descriptor_arena_truncated_bytes"]) != 0:
            raise AssertionError(f"v0.40 pre-commit recovery incorrectly truncated: {name}")
    for name in ("committed", "retirement_live_tail_relocation_committed"):
        row = rows[name]
        if row["expected_state"] != "post":
            raise AssertionError(f"v0.40 post-commit failpoint misclassified: {name}")
        before_recovery = row["arena_before_recovery"]
        if int(before_recovery["arena_file_bytes"]) != 24576:
            raise AssertionError(f"v0.40 post-commit residue physical length drifted: {name}")
        if int(before_recovery["committed_arena_bytes"]) != 16384:
            raise AssertionError(f"v0.40 post-commit target length drifted: {name}")
        if int(before_recovery["uncommitted_arena_tail_bytes"]) != 8192:
            raise AssertionError(f"v0.40 post-commit residue size drifted: {name}")
        if int(row["first_recovery"]["retirement_descriptor_arena_truncated_bytes"]) != 8192:
            raise AssertionError(f"v0.40 recovery did not remove one descriptor pair: {name}")
    for name in ("retirement_arena_truncated", "retirement_live_tail_relocation_synced"):
        row = rows[name]
        if row["expected_state"] != "post":
            raise AssertionError(f"v0.40 post-truncate failpoint misclassified: {name}")
        if int(row["arena_before_recovery"]["arena_file_bytes"]) != 16384:
            raise AssertionError(f"v0.40 post-truncate physical length drifted: {name}")
        if int(row["first_recovery"]["retirement_descriptor_arena_truncated_bytes"]) != 0:
            raise AssertionError(f"v0.40 post-truncate recovery changed arena length: {name}")
    for row in crashes["cases"]:
        if not bool(row["exact_committed_state_match"]):
            raise AssertionError(f"v0.40 crash state mismatch: {row['failpoint']}")
        _require_scan_free(row["first_recovery"])
        _require_scan_free(row["second_recovery"])
        if int(row["second_recovery"]["physical_truncated_bytes"]) != 0:
            raise AssertionError("v0.40 second recovery changed primary physical length")
        if int(row["second_recovery"]["retirement_descriptor_arena_truncated_bytes"]) != 0:
            raise AssertionError("v0.40 second recovery changed descriptor arena length")

    scaling = payload["queue_depth_scaling"]
    if list(scaling["target_descriptor_counts"]) != EXPECTED_SCALING_COUNTS:
        raise AssertionError("v0.40 scaling target counts drifted")
    if not bool(scaling["all_constant_relocation_work"]):
        raise AssertionError("v0.40 relocation work is no longer constant across tested queue depth")
    if len(scaling["rows"]) != len(EXPECTED_SCALING_COUNTS):
        raise AssertionError("v0.40 scaling row count drifted")
    for target, row in zip(EXPECTED_SCALING_COUNTS, scaling["rows"]):
        if int(row["target_descriptor_count"]) != target:
            raise AssertionError("v0.40 scaling row target drifted")
        if int(row["live_queue_depth_before"]) != target - 1:
            raise AssertionError("v0.40 scaling live queue depth drifted")
        if int(row["physical_tail_page"]) != 2 * (target - 1):
            raise AssertionError("v0.40 scaling physical tail drifted")
        if int(row["predecessor_page"]) != 2 * (target - 2):
            raise AssertionError("v0.40 scaling predecessor drifted")
        if int(row["destination_page"]) != 0:
            raise AssertionError("v0.40 scaling destination drifted")
        if int(row["arena_pages_before"]) != 2 * target or int(row["arena_pages_after"]) != 2 * (target - 1):
            raise AssertionError("v0.40 scaling frontier drifted")
        if int(row["retirement_descriptor_preads"]) != 6:
            raise AssertionError("v0.40 scaling read count depends on queue depth")
        if int(row["retirement_descriptor_pwrites"]) != 2:
            raise AssertionError("v0.40 scaling write count depends on queue depth")
        if int(row["retirement_descriptors_scanned"]) != 0:
            raise AssertionError("v0.40 scaling scanned queue/history")
        if int(row["live_descriptor_relocations"]) != 1:
            raise AssertionError("v0.40 scaling relocation count drifted")


def main() -> None:
    payload = run()
    _validate(payload)

    canonical = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    committed = RESULTS_PATH.read_bytes()
    if committed != canonical:
        raise AssertionError("v0.40 result file is not the canonical serialization")
    actual = hashlib.sha256(committed).hexdigest()
    if actual != EXPECTED_SHA256:
        raise AssertionError(f"v0.40 canonical SHA-256 drifted: {actual}")

    print(f"v0.40 canonical result verified: sha256:{actual}")


if __name__ == "__main__":
    main()
