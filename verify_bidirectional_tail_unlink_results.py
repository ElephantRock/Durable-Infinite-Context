from __future__ import annotations

import hashlib
import json

from run_bidirectional_tail_unlink_experiment import RESULTS_PATH, run

EXPECTED_SHA256 = "224c39ef61415e5f59eecc42741ff937bd9c7b783ae68ae0e35bcdc99368838e"
EXPECTED_SHRINK_FAILPOINTS = [
    "retirement_tail_unlink_staged",
    "retirement_tail_unlink_dependencies_synced",
    "committed",
    "retirement_tail_unlink_committed",
    "retirement_arena_truncated",
    "retirement_tail_unlink_synced",
]
EXPECTED_RECLAIM_FAILPOINTS = [
    "retirement_descriptor_freed",
    "retirement_arena_synced",
    "dependencies_synced",
    "committed",
]
EXPECTED_INSERT_FAILPOINTS = [
    "retirement_descriptor_reused",
    "retirement_arena_synced",
    "data_synced",
    "committed",
]


def _require_scan_free(recovery: dict) -> None:
    if int(recovery["logical_work"]) != 0:
        raise AssertionError("v0.39 recovery required logical redo")
    if int(recovery["generation_pages_scanned"]) != 0:
        raise AssertionError("v0.39 recovery scanned generation pages")
    if int(recovery["mapping_nodes_scanned"]) != 0:
        raise AssertionError("v0.39 recovery scanned radix nodes")
    if int(recovery.get("retirement_descriptors_scanned", 0)) != 0:
        raise AssertionError("v0.39 recovery scanned retirement descriptors")


def _validate(payload: dict) -> None:
    if payload["experiment"] != "v0.39_bidirectional_free_tail_unlink":
        raise AssertionError("unexpected v0.39 experiment id")

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
            raise AssertionError(f"v0.39 semantic guard failed: {name}")

    candidate = payload["bidirectional_tail_unlink"]
    if not bool(candidate["survived"]):
        raise AssertionError("v0.39 candidate no longer survives")
    if int(candidate["candidate_descriptor_history_walks"]) != 0:
        raise AssertionError("v0.39 candidate history-walk count drifted")
    if int(candidate["candidate_relocations"]) != 0:
        raise AssertionError("v0.39 candidate relocation count drifted")

    control = candidate["v038_control"]
    if int(control["keys_inserted"]) != 257:
        raise AssertionError("v0.39 v0.38-control workload trigger drifted")
    if int(control["retained_arena_pages"]) != 6 or int(control["retained_arena_bytes"]) != 24576:
        raise AssertionError("v0.39 v0.38-control retained arena drifted")
    if int(control["queue_before"]["queue_count"]) != 1:
        raise AssertionError("v0.39 control live queue depth drifted")
    if int(control["queue_before"]["descriptor_free_count"]) != 2:
        raise AssertionError("v0.39 control free count drifted")
    if int(control["queue_before"]["descriptor_free_head_page"]) != 2:
        raise AssertionError("v0.39 control free-list head drifted")
    if [int(row["descriptor_page"]) for row in control["queue_before"]["free_descriptors"]] != [2, 4]:
        raise AssertionError("v0.39 control free-list order drifted")
    if control["queue_before"] != control["queue_after"]:
        raise AssertionError("v0.39 v0.38 control changed committed state")
    control_trace = control["trace"]
    if bool(control_trace["released"]):
        raise AssertionError("v0.39 v0.38 control unexpectedly released buried tail")
    if int(control_trace["retirement_descriptor_preads"]) != 0:
        raise AssertionError("v0.39 v0.38 control searched descriptor storage")
    if int(control_trace["retirement_descriptors_scanned"]) != 0:
        raise AssertionError("v0.39 v0.38 control scanned descriptor history")

    release = candidate["non_head_release"]
    if int(release["keys_inserted"]) != 257:
        raise AssertionError("v0.39 release workload trigger drifted")
    if int(release["released_arena_pages"]) != 2 or int(release["released_arena_bytes"]) != 8192:
        raise AssertionError("v0.39 released amount drifted")
    before = release["queue_before"]
    after = release["queue_after"]
    if (int(before["queue_count"]), int(before["descriptor_free_count"]), int(before["descriptor_arena_pages"])) != (1, 2, 6):
        raise AssertionError("v0.39 release fixture start state drifted")
    if int(before["descriptor_free_head_page"]) != 2:
        raise AssertionError("v0.39 release fixture no longer buries tail behind page 2")
    if (int(after["queue_count"]), int(after["descriptor_free_count"]), int(after["descriptor_arena_pages"])) != (1, 1, 4):
        raise AssertionError("v0.39 release fixture end state drifted")
    if int(after["descriptor_free_head_page"]) != 2:
        raise AssertionError("v0.39 release changed surviving free-list head")
    if int(release["arena_before"]["arena_file_bytes"]) != 24576:
        raise AssertionError("v0.39 physical arena start length drifted")
    if int(release["arena_after"]["arena_file_bytes"]) != 16384:
        raise AssertionError("v0.39 physical arena end length drifted")

    trace = release["trace"]
    exact_trace = {
        "retirement_arena_pages_before": 6,
        "retirement_arena_pages_after": 4,
        "retirement_arena_pages_released": 2,
        "retirement_arena_bytes_released": 8192,
        "retirement_descriptor_free_count_before": 2,
        "retirement_descriptor_free_count_after": 1,
        "retirement_descriptor_preads": 4,
        "retirement_descriptor_pwrites": 1,
        "retirement_descriptors_scanned": 0,
        "candidate_relocations": 0,
        "retirement_queue_count": 1,
        "tail_page": 4,
        "tail_incarnation": 3,
        "predecessor_page": 2,
        "predecessor_incarnation": 4,
        "retirement_arena_fsyncs": 2,
    }
    for name, expected in exact_trace.items():
        if int(trace[name]) != expected:
            raise AssertionError(f"v0.39 release trace drifted: {name}")
    if not bool(trace["released"]) or not bool(trace["tail_was_free"]):
        raise AssertionError("v0.39 release trace lost physical-free-tail release")
    if bool(trace["tail_was_free_head"]):
        raise AssertionError("v0.39 release no longer exercises non-head tail")
    if trace["successor_page"] is not None or trace["successor_incarnation"] is not None:
        raise AssertionError("v0.39 physical tail unexpectedly has a successor")

    reuse = candidate["reuse_head_predecessor_repair"]
    if int(reuse["reuse_trigger_key_index"]) != 512:
        raise AssertionError("v0.39 free-head reuse trigger drifted")
    if not bool(reuse["new_head_predecessor_cleared"]):
        raise AssertionError("v0.39 reused free-list head did not clear successor predecessor")
    reuse_queue = reuse["queue_after"]
    if int(reuse_queue["descriptor_free_count"]) != 1:
        raise AssertionError("v0.39 reuse free count drifted")
    if int(reuse_queue["descriptor_free_head_page"]) != 4:
        raise AssertionError("v0.39 reuse did not advance free-list head to page 4")
    free_row = reuse_queue["free_descriptors"][0]
    if "prev_descriptor_page" in free_row or "prev_descriptor_incarnation" in free_row:
        raise AssertionError("v0.39 new free-list head retained predecessor authority")

    identity = candidate["identity_after_non_head_shrink"]
    exact_identity = {
        "stale_page": 4,
        "stale_incarnation": 3,
        "reuse_trigger_key_index": 1024,
        "current_incarnation": 7,
        "current_identity_generation": 6,
        "arena_pages_after_reuse": 6,
    }
    for name, expected in exact_identity.items():
        if int(identity[name]) != expected:
            raise AssertionError(f"v0.39 identity field drifted: {name}")
    for name in (
        "stale_rejected_after_shrink",
        "incarnation_advanced",
        "stale_rejected_after_reuse",
    ):
        if not bool(identity[name]):
            raise AssertionError(f"v0.39 identity invariant failed: {name}")

    crashes = candidate["crash_matrix"]
    if list(crashes["failpoints"]) != EXPECTED_SHRINK_FAILPOINTS:
        raise AssertionError("v0.39 shrink failpoint sequence drifted")
    if int(crashes["case_count"]) != len(EXPECTED_SHRINK_FAILPOINTS):
        raise AssertionError("v0.39 shrink crash case count drifted")
    for name in (
        "all_exact_committed_state_match",
        "all_recovery_scan_free",
        "all_second_recovery_idempotent",
    ):
        if not bool(crashes[name]):
            raise AssertionError(f"v0.39 shrink crash aggregate failed: {name}")
    crash_rows = {row["failpoint"]: row for row in crashes["cases"]}
    for name in EXPECTED_SHRINK_FAILPOINTS[:2]:
        row = crash_rows[name]
        if row["expected_state"] != "pre":
            raise AssertionError(f"v0.39 pre-commit failpoint misclassified: {name}")
        if int(row["arena_before_recovery"]["committed_arena_bytes"]) != 24576:
            raise AssertionError(f"v0.39 pre-commit arena target drifted: {name}")
        if int(row["first_recovery"]["retirement_descriptor_arena_truncated_bytes"]) != 0:
            raise AssertionError(f"v0.39 pre-commit recovery incorrectly truncated: {name}")
    for name in ("committed", "retirement_tail_unlink_committed"):
        row = crash_rows[name]
        if row["expected_state"] != "post":
            raise AssertionError(f"v0.39 post-commit failpoint misclassified: {name}")
        before_recovery = row["arena_before_recovery"]
        if int(before_recovery["arena_file_bytes"]) != 24576:
            raise AssertionError(f"v0.39 post-commit residue physical length drifted: {name}")
        if int(before_recovery["committed_arena_bytes"]) != 16384:
            raise AssertionError(f"v0.39 post-commit target length drifted: {name}")
        if int(before_recovery["uncommitted_arena_tail_bytes"]) != 8192:
            raise AssertionError(f"v0.39 post-commit residue size drifted: {name}")
        if int(row["first_recovery"]["retirement_descriptor_arena_truncated_bytes"]) != 8192:
            raise AssertionError(f"v0.39 recovery did not remove exactly one descriptor pair: {name}")
    for name in ("retirement_arena_truncated", "retirement_tail_unlink_synced"):
        row = crash_rows[name]
        if row["expected_state"] != "post":
            raise AssertionError(f"v0.39 post-truncate failpoint misclassified: {name}")
        if int(row["arena_before_recovery"]["arena_file_bytes"]) != 16384:
            raise AssertionError(f"v0.39 post-truncate physical length drifted: {name}")
        if int(row["first_recovery"]["retirement_descriptor_arena_truncated_bytes"]) != 0:
            raise AssertionError(f"v0.39 post-truncate recovery changed arena length: {name}")
    for row in crashes["cases"]:
        if not bool(row["exact_committed_state_match"]):
            raise AssertionError(f"v0.39 shrink crash state mismatch: {row['failpoint']}")
        _require_scan_free(row["first_recovery"])
        _require_scan_free(row["second_recovery"])
        if int(row["second_recovery"]["physical_truncated_bytes"]) != 0:
            raise AssertionError("v0.39 second recovery changed primary physical length")
        if int(row["second_recovery"]["retirement_descriptor_arena_truncated_bytes"]) != 0:
            raise AssertionError("v0.39 second recovery changed descriptor arena length")

    topology = payload["topology_maintenance_crashes"]
    if int(topology["case_count"]) != 8:
        raise AssertionError("v0.39 topology crash case count drifted")
    for name in (
        "all_exact_committed_state_match",
        "all_recovery_scan_free",
        "all_second_recovery_idempotent",
    ):
        if not bool(topology[name]):
            raise AssertionError(f"v0.39 topology crash aggregate failed: {name}")

    push = topology["predecessor_push"]
    if list(push["failpoints"]) != EXPECTED_RECLAIM_FAILPOINTS:
        raise AssertionError("v0.39 predecessor-push failpoints drifted")
    if int(push["case_count"]) != 4:
        raise AssertionError("v0.39 predecessor-push case count drifted")
    push_trace = push["clean_trace"]
    if int(push_trace["free_predecessor_preads"]) != 2 or int(push_trace["free_predecessor_pwrites"]) != 1:
        raise AssertionError("v0.39 predecessor-push maintenance work drifted")
    if int(push_trace["retirement_descriptors_scanned"]) != 0:
        raise AssertionError("v0.39 predecessor-push scanned descriptor history")
    push_free = push["clean_post_state"]["queue"]["free_descriptors"]
    if [int(row["descriptor_page"]) for row in push_free] != [2, 4]:
        raise AssertionError("v0.39 predecessor-push free topology drifted")
    if int(push_free[1]["prev_descriptor_page"]) != 2:
        raise AssertionError("v0.39 predecessor-push did not publish reverse link")
    for row in push["cases"]:
        expected = "post" if row["failpoint"] == "committed" else "pre"
        if row["expected_state"] != expected or not bool(row["exact_committed_state_match"]):
            raise AssertionError(f"v0.39 predecessor-push crash state drifted: {row['failpoint']}")
        _require_scan_free(row["first_recovery"])
        _require_scan_free(row["second_recovery"])

    pop = topology["free_head_reuse"]
    if list(pop["failpoints"]) != EXPECTED_INSERT_FAILPOINTS:
        raise AssertionError("v0.39 free-head-reuse failpoints drifted")
    if int(pop["case_count"]) != 4:
        raise AssertionError("v0.39 free-head-reuse case count drifted")
    if pop["trigger_key"] != "k-0512":
        raise AssertionError("v0.39 free-head-reuse trigger drifted")
    if int(pop["clean_trace"]["retirement_descriptors_enqueued"]) != 1:
        raise AssertionError("v0.39 free-head-reuse clean insert no longer enqueues exactly one descriptor")
    pop_queue = pop["clean_post_state"]["queue"]
    if int(pop_queue["descriptor_free_count"]) != 1 or int(pop_queue["descriptor_free_head_page"]) != 4:
        raise AssertionError("v0.39 free-head-reuse committed topology drifted")
    pop_free = pop_queue["free_descriptors"][0]
    if "prev_descriptor_page" in pop_free or "prev_descriptor_incarnation" in pop_free:
        raise AssertionError("v0.39 free-head-reuse left predecessor on new head")
    for row in pop["cases"]:
        expected = "post" if row["failpoint"] == "committed" else "pre"
        if row["expected_state"] != expected or not bool(row["exact_committed_state_match"]):
            raise AssertionError(f"v0.39 free-head-reuse crash state drifted: {row['failpoint']}")
        _require_scan_free(row["first_recovery"])
        _require_scan_free(row["second_recovery"])

    scaling = payload["free_chain_scaling"]
    if list(scaling["target_descriptor_counts"]) != [3, 4, 5, 6]:
        raise AssertionError("v0.39 scaling target domain drifted")
    if not bool(scaling["all_constant_shrink_work"]):
        raise AssertionError("v0.39 shrink work depends on free-chain length")
    expected_rows = [
        (3, 2, 257, 4, 2, 6, 4),
        (4, 3, 1025, 6, 4, 8, 6),
        (5, 4, 4099, 8, 6, 10, 8),
        (6, 5, 16393, 10, 8, 12, 10),
    ]
    observed_rows = []
    for row in scaling["rows"]:
        observed_rows.append(
            (
                int(row["target_descriptor_count"]),
                int(row["free_chain_length_before"]),
                int(row["keys_inserted"]),
                int(row["physical_tail_page"]),
                int(row["predecessor_page"]),
                int(row["arena_pages_before"]),
                int(row["arena_pages_after"]),
            )
        )
        if int(row["retirement_descriptor_preads"]) != 4:
            raise AssertionError("v0.39 scaled shrink read count drifted")
        if int(row["retirement_descriptor_pwrites"]) != 1:
            raise AssertionError("v0.39 scaled shrink write count drifted")
        if int(row["retirement_descriptors_scanned"]) != 0:
            raise AssertionError("v0.39 scaled shrink scanned descriptor history")
        if int(row["candidate_relocations"]) != 0:
            raise AssertionError("v0.39 scaled shrink relocated live descriptors")
    if observed_rows != expected_rows:
        raise AssertionError(f"v0.39 scaling geometry drifted: {observed_rows}")


def main() -> None:
    payload = run()
    _validate(payload)
    canonical = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    raw = canonical.encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    if digest != EXPECTED_SHA256:
        raise AssertionError(f"v0.39 canonical SHA-256 drifted: {digest}")
    generated = RESULTS_PATH.read_bytes()
    if generated != raw:
        raise AssertionError("v0.39 generated result bytes are not canonical")
    if hashlib.sha256(generated).hexdigest() != EXPECTED_SHA256:
        raise AssertionError("v0.39 generated result SHA-256 drifted")


if __name__ == "__main__":
    main()
