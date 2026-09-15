from __future__ import annotations

import hashlib
import json
from typing import Any

from run_multi_free_live_tail_evacuation_experiment import RESULTS_PATH, run

EXPECTED_SHA256 = "2dbbeb08c35c7fd913d992305fe7e0a4a4e0ef4fdebf8234536463aca6a3371f"
EXPECTED_FAILPOINTS = [
    "retirement_live_tail_destination_staged",
    "retirement_live_tail_predecessor_staged",
    "retirement_live_tail_free_successor_staged",
    "retirement_live_tail_dependencies_synced",
    "committed",
    "retirement_live_tail_relocation_committed",
    "retirement_arena_truncated",
    "retirement_live_tail_relocation_synced",
]
EXPECTED_SCALE_COUNTS = [4, 5, 6, 7]


def _pages(snapshot: dict[str, Any]) -> list[int]:
    return [int(row["descriptor_page"]) for row in snapshot["descriptors"]]


def _free_pages(snapshot: dict[str, Any]) -> list[int]:
    return [int(row["descriptor_page"]) for row in snapshot["free_descriptors"]]


def _require_no_key(value: Any, forbidden: str) -> None:
    if isinstance(value, dict):
        if forbidden in value:
            raise AssertionError(f"v0.41 canonical evidence retained forbidden key: {forbidden}")
        for nested in value.values():
            _require_no_key(nested, forbidden)
    elif isinstance(value, list):
        for nested in value:
            _require_no_key(nested, forbidden)


def _require_scan_free(recovery: dict[str, Any]) -> None:
    if int(recovery["logical_work"]) != 0:
        raise AssertionError("v0.41 recovery required logical redo")
    if int(recovery["generation_pages_scanned"]) != 0:
        raise AssertionError("v0.41 recovery scanned generation pages")
    if int(recovery["mapping_nodes_scanned"]) != 0:
        raise AssertionError("v0.41 recovery scanned radix nodes")
    if int(recovery.get("retirement_descriptors_scanned", 0)) != 0:
        raise AssertionError("v0.41 recovery scanned retirement descriptors")


def _validate_control(control: dict[str, Any]) -> None:
    if int(control["keys_inserted"]) != 129:
        raise AssertionError("v0.41 v0.40-control trigger drifted")
    before = control["queue_before"]
    after = control["queue_after"]
    if _pages(before) != [4, 6] or _free_pages(before) != [2, 0]:
        raise AssertionError("v0.41 v0.40-control topology drifted")
    if int(before["queue_count"]) != 2 or int(before["descriptor_free_count"]) != 2:
        raise AssertionError("v0.41 v0.40-control counts drifted")
    if int(before["tail_page"]) != 6 or int(before["tail_predecessor_page"]) != 4:
        raise AssertionError("v0.41 v0.40-control tail authority drifted")
    if before != after or control["arena_before"] != control["arena_after"]:
        raise AssertionError("v0.41 v0.40-control changed state")
    if not bool(control["exact_state_unchanged"]):
        raise AssertionError("v0.41 v0.40-control exact-state guard failed")
    trace = control["trace"]
    if bool(trace["released"]):
        raise AssertionError("v0.41 v0.40-control unexpectedly relocated multiple-FREE tail")
    for name in (
        "retirement_descriptor_preads",
        "retirement_descriptor_pwrites",
        "retirement_descriptors_scanned",
        "live_descriptor_relocations",
        "retirement_arena_pages_released",
        "retirement_arena_bytes_released",
    ):
        if int(trace[name]) != 0:
            raise AssertionError(f"v0.41 v0.40-control work drifted: {name}")


def _validate_candidate(candidate: dict[str, Any]) -> None:
    if int(candidate["keys_inserted"]) != 129:
        raise AssertionError("v0.41 discriminating workload trigger drifted")
    before = candidate["queue_before"]
    after = candidate["queue_after"]
    if _pages(before) != [4, 6] or _free_pages(before) != [2, 0]:
        raise AssertionError("v0.41 candidate start topology drifted")
    if (int(before["queue_count"]), int(before["descriptor_free_count"]), int(before["descriptor_arena_pages"])) != (2, 2, 8):
        raise AssertionError("v0.41 candidate start counts drifted")
    if int(before["tail_page"]) != 6 or int(before["tail_incarnation"]) != 4:
        raise AssertionError("v0.41 candidate start tail identity drifted")
    if int(before["tail_predecessor_page"]) != 4 or int(before["tail_predecessor_incarnation"]) != 3:
        raise AssertionError("v0.41 candidate queue predecessor authority drifted")
    if int(before["descriptor_free_head_page"]) != 2 or int(before["descriptor_free_head_incarnation"]) != 2:
        raise AssertionError("v0.41 candidate free-head identity drifted")
    if int(before["free_descriptors"][0]["next_descriptor_page"]) != 0:
        raise AssertionError("v0.41 destination no longer directly names free successor")
    if int(before["free_descriptors"][1]["prev_descriptor_page"]) != 2:
        raise AssertionError("v0.41 free successor predecessor authority drifted")

    if _pages(after) != [4, 2] or _free_pages(after) != [0]:
        raise AssertionError("v0.41 candidate end topology drifted")
    if (int(after["queue_count"]), int(after["descriptor_free_count"]), int(after["descriptor_arena_pages"])) != (2, 1, 6):
        raise AssertionError("v0.41 candidate end counts drifted")
    if int(after["tail_page"]) != 2 or int(after["tail_incarnation"]) != 5:
        raise AssertionError("v0.41 relocated tail identity drifted")
    if int(after["tail_predecessor_page"]) != 4 or int(after["tail_predecessor_incarnation"]) != 3:
        raise AssertionError("v0.41 relocated queue predecessor authority drifted")
    if int(after["descriptor_free_head_page"]) != 0 or int(after["descriptor_free_head_incarnation"]) != 1:
        raise AssertionError("v0.41 new free-head identity drifted")
    free_head = after["free_descriptors"][0]
    if "prev_descriptor_page" in free_head or "prev_descriptor_incarnation" in free_head:
        raise AssertionError("v0.41 new free head retained predecessor authority")

    if int(candidate["arena_before"]["arena_file_bytes"]) != 32768:
        raise AssertionError("v0.41 physical arena start length drifted")
    if int(candidate["arena_after"]["arena_file_bytes"]) != 24576:
        raise AssertionError("v0.41 physical arena end length drifted")
    if int(candidate["released_arena_pages"]) != 2 or int(candidate["released_arena_bytes"]) != 8192:
        raise AssertionError("v0.41 released amount drifted")
    for name in (
        "stale_tail_rejected",
        "stale_destination_rejected",
        "successor_identity_preserved",
        "successor_predecessor_cleared",
    ):
        if not bool(candidate[name]):
            raise AssertionError(f"v0.41 identity/topology invariant failed: {name}")

    trace = candidate["trace"]
    exact_trace = {
        "retirement_arena_pages_before": 8,
        "retirement_arena_pages_after": 6,
        "retirement_arena_pages_released": 2,
        "retirement_arena_bytes_released": 8192,
        "retirement_descriptor_free_count_before": 2,
        "retirement_descriptor_free_count_after": 1,
        "retirement_descriptor_preads": 8,
        "retirement_descriptor_pwrites": 3,
        "retirement_descriptors_scanned": 0,
        "live_descriptor_relocations": 1,
        "retirement_queue_count": 2,
        "tail_page": 6,
        "tail_incarnation": 4,
        "predecessor_page": 4,
        "predecessor_incarnation": 3,
        "destination_page": 2,
        "destination_old_incarnation": 2,
        "destination_new_incarnation": 5,
        "retirement_arena_fsyncs": 2,
    }
    for name, expected in exact_trace.items():
        if int(trace[name]) != expected:
            raise AssertionError(f"v0.41 candidate trace drifted: {name}")
    if not bool(trace["released"]) or not bool(trace["tail_was_queued"]) or not bool(trace["tail_was_queue_tail"]):
        raise AssertionError("v0.41 trace lost live physical queue-tail relocation")


def _validate_sole_free(row: dict[str, Any]) -> None:
    if int(row["keys_inserted"]) != 65:
        raise AssertionError("v0.41 sole-FREE compatibility trigger drifted")
    if _pages(row["queue_before"]) != [2, 4] or _free_pages(row["queue_before"]) != [0]:
        raise AssertionError("v0.41 sole-FREE compatibility topology drifted")
    trace = row["trace"]
    if not bool(trace["released"]):
        raise AssertionError("v0.41 regressed v0.40 sole-FREE relocation")
    if int(trace["retirement_descriptor_preads"]) != 6:
        raise AssertionError("v0.41 sole-FREE compatibility read count drifted")
    if int(trace["retirement_descriptor_pwrites"]) != 2:
        raise AssertionError("v0.41 sole-FREE compatibility write count drifted")
    if int(trace["retirement_descriptors_scanned"]) != 0 or int(trace["live_descriptor_relocations"]) != 1:
        raise AssertionError("v0.41 sole-FREE compatibility introduced non-local work")
    if int(row["queue_after"]["descriptor_free_count"]) != 0:
        raise AssertionError("v0.41 sole-FREE compatibility left a free descriptor")


def _validate_crashes(crashes: dict[str, Any]) -> None:
    if list(crashes["failpoints"]) != EXPECTED_FAILPOINTS:
        raise AssertionError("v0.41 crash failpoint sequence drifted")
    if int(crashes["case_count"]) != len(EXPECTED_FAILPOINTS):
        raise AssertionError("v0.41 crash case count drifted")
    for name in (
        "all_exact_committed_state_match",
        "all_recovery_scan_free",
        "all_second_recovery_idempotent",
    ):
        if not bool(crashes[name]):
            raise AssertionError(f"v0.41 crash aggregate failed: {name}")

    rows = {row["failpoint"]: row for row in crashes["cases"]}
    for name in EXPECTED_FAILPOINTS[:4]:
        row = rows[name]
        if row["expected_state"] != "pre":
            raise AssertionError(f"v0.41 pre-commit failpoint misclassified: {name}")
        if int(row["first_recovery"]["retirement_descriptor_arena_truncated_bytes"]) != 0:
            raise AssertionError(f"v0.41 pre-commit recovery incorrectly truncated arena: {name}")

    for name in ("committed", "retirement_live_tail_relocation_committed"):
        row = rows[name]
        if row["expected_state"] != "post":
            raise AssertionError(f"v0.41 post-commit failpoint misclassified: {name}")
        before = row["arena_before_recovery"]
        if int(before["arena_file_bytes"]) != 32768:
            raise AssertionError(f"v0.41 post-commit physical residue drifted: {name}")
        if int(before["committed_arena_bytes"]) != 24576:
            raise AssertionError(f"v0.41 post-commit target length drifted: {name}")
        if int(before["uncommitted_arena_tail_bytes"]) != 8192:
            raise AssertionError(f"v0.41 post-commit residue size drifted: {name}")
        if int(row["first_recovery"]["retirement_descriptor_arena_truncated_bytes"]) != 8192:
            raise AssertionError(f"v0.41 recovery did not remove one descriptor pair: {name}")

    for name in ("retirement_arena_truncated", "retirement_live_tail_relocation_synced"):
        row = rows[name]
        if row["expected_state"] != "post":
            raise AssertionError(f"v0.41 post-truncate failpoint misclassified: {name}")
        if int(row["arena_before_recovery"]["arena_file_bytes"]) != 24576:
            raise AssertionError(f"v0.41 post-truncate physical length drifted: {name}")
        if int(row["first_recovery"]["retirement_descriptor_arena_truncated_bytes"]) != 0:
            raise AssertionError(f"v0.41 post-truncate recovery changed arena length: {name}")

    for row in crashes["cases"]:
        if not bool(row["exact_committed_state_match"]):
            raise AssertionError(f"v0.41 crash state mismatch: {row['failpoint']}")
        _require_scan_free(row["first_recovery"])
        _require_scan_free(row["second_recovery"])
        if int(row["second_recovery"]["physical_truncated_bytes"]) != 0:
            raise AssertionError("v0.41 second recovery changed primary physical length")
        if int(row["second_recovery"]["retirement_descriptor_arena_truncated_bytes"]) != 0:
            raise AssertionError("v0.41 second recovery changed descriptor arena length")


def _validate_free_scaling(scaling: dict[str, Any]) -> None:
    if list(scaling["target_descriptor_counts"]) != EXPECTED_SCALE_COUNTS:
        raise AssertionError("v0.41 free-chain scaling target counts drifted")
    if not bool(scaling["all_constant_relocation_work"]):
        raise AssertionError("v0.41 relocation work depends on tested free-chain length")
    expected = [
        (4, 2, 129, 6, 4, 2, 0, 8, 6),
        (5, 3, 257, 8, 6, 4, 2, 10, 8),
        (6, 4, 513, 10, 8, 6, 4, 12, 10),
        (7, 5, 1025, 12, 10, 8, 6, 14, 12),
    ]
    for row, values in zip(scaling["rows"], expected):
        (
            target,
            chain_len,
            keys,
            tail,
            predecessor,
            destination,
            successor,
            arena_before,
            arena_after,
        ) = values
        if (
            int(row["target_descriptor_count"]) != target
            or int(row["free_chain_length_before"]) != chain_len
            or int(row["keys_inserted"]) != keys
            or int(row["physical_tail_page"]) != tail
            or int(row["predecessor_page"]) != predecessor
            or int(row["destination_page"]) != destination
            or int(row["successor_page"]) != successor
            or int(row["arena_pages_before"]) != arena_before
            or int(row["arena_pages_after"]) != arena_after
        ):
            raise AssertionError("v0.41 free-chain scaling geometry drifted")
        if int(row["live_queue_depth_before"]) != 2:
            raise AssertionError("v0.41 free-chain scaling live depth drifted")
        if int(row["retirement_descriptor_preads"]) != 8:
            raise AssertionError("v0.41 free-chain scaling read count drifted")
        if int(row["retirement_descriptor_pwrites"]) != 3:
            raise AssertionError("v0.41 free-chain scaling write count drifted")
        if int(row["retirement_descriptors_scanned"]) != 0:
            raise AssertionError("v0.41 free-chain scaling scanned descriptor history")
        if int(row["live_descriptor_relocations"]) != 1:
            raise AssertionError("v0.41 free-chain scaling relocation count drifted")


def _validate_queue_scaling(scaling: dict[str, Any]) -> None:
    if list(scaling["target_descriptor_counts"]) != EXPECTED_SCALE_COUNTS:
        raise AssertionError("v0.41 queue-depth scaling target counts drifted")
    if not bool(scaling["all_constant_relocation_work"]):
        raise AssertionError("v0.41 relocation work depends on tested queue depth")
    expected_depths = [2, 3, 4, 5]
    expected_keys = [129, 257, 513, 1025]
    for target, depth, keys, row in zip(
        EXPECTED_SCALE_COUNTS,
        expected_depths,
        expected_keys,
        scaling["rows"],
    ):
        if int(row["target_descriptor_count"]) != target:
            raise AssertionError("v0.41 queue-depth scaling target drifted")
        if int(row["live_queue_depth_before"]) != depth:
            raise AssertionError("v0.41 queue-depth scaling live depth drifted")
        if int(row["free_chain_length_before"]) != 2:
            raise AssertionError("v0.41 queue-depth scaling free-chain length drifted")
        if int(row["keys_inserted"]) != keys:
            raise AssertionError("v0.41 queue-depth scaling workload trigger drifted")
        if int(row["physical_tail_page"]) != 2 * (target - 1):
            raise AssertionError("v0.41 queue-depth scaling physical tail drifted")
        if int(row["predecessor_page"]) != 2 * (target - 2):
            raise AssertionError("v0.41 queue-depth scaling predecessor drifted")
        if int(row["destination_page"]) != 2 or int(row["successor_page"]) != 0:
            raise AssertionError("v0.41 queue-depth scaling FREE topology drifted")
        if int(row["arena_pages_before"]) != 2 * target or int(row["arena_pages_after"]) != 2 * (target - 1):
            raise AssertionError("v0.41 queue-depth scaling frontier drifted")
        if int(row["retirement_descriptor_preads"]) != 8:
            raise AssertionError("v0.41 queue-depth scaling read count drifted")
        if int(row["retirement_descriptor_pwrites"]) != 3:
            raise AssertionError("v0.41 queue-depth scaling write count drifted")
        if int(row["retirement_descriptors_scanned"]) != 0:
            raise AssertionError("v0.41 queue-depth scaling scanned descriptor history")
        if int(row["live_descriptor_relocations"]) != 1:
            raise AssertionError("v0.41 queue-depth scaling relocation count drifted")


def _validate(payload: dict[str, Any]) -> None:
    if payload["experiment"] != "v0.41-multiple-free-live-tail-evacuation":
        raise AssertionError("unexpected v0.41 experiment id")
    if not bool(payload["survived"]):
        raise AssertionError("v0.41 candidate no longer survives")
    if int(payload["candidate_free_chain_walks"]) != 0:
        raise AssertionError("v0.41 candidate introduced free-chain traversal")
    if int(payload["candidate_queue_walks"]) != 0:
        raise AssertionError("v0.41 candidate introduced queue traversal")

    _validate_control(payload["v040_multiple_free_control"])
    _validate_candidate(payload["multiple_free_live_tail_evacuation"])
    _validate_sole_free(payload["sole_free_compatibility"])
    _validate_crashes(payload["crash_matrix"])
    _validate_free_scaling(payload["free_chain_scaling"])
    _validate_queue_scaling(payload["queue_depth_scaling"])
    _require_no_key(payload, "allocated_bytes")


def main() -> None:
    payload = run()
    _validate(payload)

    canonical = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    committed = RESULTS_PATH.read_bytes()
    if committed != canonical:
        raise AssertionError("v0.41 result file is not the canonical serialization")
    actual = hashlib.sha256(committed).hexdigest()
    if actual != EXPECTED_SHA256:
        raise AssertionError(f"v0.41 canonical SHA-256 drifted: {actual}")

    print(f"v0.41 canonical result verified: sha256:{actual}")


if __name__ == "__main__":
    main()
