from __future__ import annotations

import hashlib
import json

from run_partial_tail_shrink_experiment import RESULTS_PATH, run

EXPECTED_SHA256 = "853f66bc3af81b0a1ba071d342b953795fabf1d0c415863da319f2a030dc7de0"
EXPECTED_FAILPOINTS = [
    "retirement_tail_release_staged",
    "committed",
    "retirement_tail_release_committed",
    "retirement_arena_truncated",
    "retirement_tail_release_synced",
]


def _require_scan_free(recovery: dict) -> None:
    if int(recovery["logical_work"]) != 0:
        raise AssertionError("v0.38 recovery required logical redo")
    if int(recovery["generation_pages_scanned"]) != 0:
        raise AssertionError("v0.38 recovery scanned generation pages")
    if int(recovery["mapping_nodes_scanned"]) != 0:
        raise AssertionError("v0.38 recovery scanned radix nodes")
    if int(recovery.get("retirement_descriptors_scanned", 0)) != 0:
        raise AssertionError("v0.38 recovery scanned retirement descriptors")


def _validate(payload: dict) -> None:
    if payload["experiment"] != "v0.38_partial_retirement_descriptor_tail_shrink":
        raise AssertionError("unexpected v0.38 experiment id")

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
            raise AssertionError(f"v0.38 semantic guard failed: {name}")

    candidate = payload["partial_tail_shrink"]
    if not bool(candidate["survived"]):
        raise AssertionError("v0.38 candidate no longer survives")
    if int(candidate["candidate_descriptor_history_walks"]) != 0:
        raise AssertionError("v0.38 candidate history-walk count drifted")
    if int(candidate["candidate_relocations"]) != 0:
        raise AssertionError("v0.38 candidate relocation count drifted")

    control = candidate["v037_control"]
    if int(control["keys_inserted"]) != 257:
        raise AssertionError("v0.38 control workload trigger drifted")
    if int(control["retained_arena_pages"]) != 6:
        raise AssertionError("v0.38 v0.37 control arena pages drifted")
    if int(control["retained_arena_bytes"]) != 24576:
        raise AssertionError("v0.38 v0.37 control arena bytes drifted")
    if int(control["queue"]["queue_count"]) != 2:
        raise AssertionError("v0.38 control does not retain live backlog")
    if int(control["queue"]["descriptor_free_head_page"]) != 4:
        raise AssertionError("v0.38 control free-list head no longer matches tail fixture")

    nonaligned = candidate["nonaligned_noop"]
    if nonaligned["queue_before"] != nonaligned["queue_after"]:
        raise AssertionError("v0.38 non-aligned no-op changed committed descriptor state")
    noop = nonaligned["trace"]
    if bool(noop["released"]):
        raise AssertionError("v0.38 non-aligned case unexpectedly released capacity")
    if int(noop["retirement_descriptor_preads"]) != 0:
        raise AssertionError("v0.38 non-aligned no-op searched descriptor storage")
    if int(noop["retirement_descriptors_scanned"]) != 0:
        raise AssertionError("v0.38 non-aligned no-op scanned descriptor history")
    if int(noop["candidate_relocations"]) != 0:
        raise AssertionError("v0.38 non-aligned no-op relocated descriptor state")

    release = candidate["live_backlog_release"]
    if int(release["keys_inserted"]) != 257:
        raise AssertionError("v0.38 release fixture workload trigger drifted")
    if not bool(release["live_backlog_preserved"]):
        raise AssertionError("v0.38 partial shrink drained the live backlog")
    if int(release["released_arena_pages"]) != 2:
        raise AssertionError("v0.38 released page count drifted")
    if int(release["released_arena_bytes"]) != 8192:
        raise AssertionError("v0.38 released byte count drifted")
    if int(release["queue_before"]["queue_count"]) != 2:
        raise AssertionError("v0.38 release fixture lost live queue before shrink")
    if int(release["queue_after"]["queue_count"]) != 2:
        raise AssertionError("v0.38 release fixture changed queue depth")
    if int(release["queue_before"]["descriptor_arena_pages"]) != 6:
        raise AssertionError("v0.38 release fixture did not start at six arena pages")
    if int(release["queue_after"]["descriptor_arena_pages"]) != 4:
        raise AssertionError("v0.38 release fixture did not finish at four arena pages")
    if int(release["arena_before"]["arena_file_bytes"]) != 24576:
        raise AssertionError("v0.38 release physical start length drifted")
    if int(release["arena_after"]["arena_file_bytes"]) != 16384:
        raise AssertionError("v0.38 release physical end length drifted")

    trace = release["trace"]
    exact_trace = {
        "retirement_arena_pages_before": 6,
        "retirement_arena_pages_after": 4,
        "retirement_arena_pages_released": 2,
        "retirement_arena_bytes_released": 8192,
        "retirement_descriptor_free_count_before": 1,
        "retirement_descriptor_free_count_after": 0,
        "retirement_descriptor_preads": 2,
        "retirement_descriptor_pwrites": 0,
        "retirement_descriptors_scanned": 0,
        "candidate_relocations": 0,
        "retirement_queue_count": 2,
        "tail_page": 4,
        "tail_incarnation": 3,
    }
    for name, expected in exact_trace.items():
        if int(trace[name]) != expected:
            raise AssertionError(f"v0.38 release trace drifted: {name}")
    if not bool(trace["released"]) or not bool(trace["tail_aligned"]):
        raise AssertionError("v0.38 release trace lost aligned-tail release")

    identity = candidate["identity_after_partial_shrink"]
    exact_identity = {
        "stale_page": 4,
        "stale_incarnation": 3,
        "reuse_trigger_key_index": 512,
        "current_incarnation": 6,
        "current_identity_generation": 5,
        "arena_pages_after_reuse": 6,
    }
    for name, expected in exact_identity.items():
        if int(identity[name]) != expected:
            raise AssertionError(f"v0.38 identity field drifted: {name}")
    for name in (
        "stale_rejected_after_shrink",
        "incarnation_advanced",
        "stale_rejected_after_reuse",
    ):
        if not bool(identity[name]):
            raise AssertionError(f"v0.38 identity invariant failed: {name}")

    crashes = candidate["crash_matrix"]
    if list(crashes["failpoints"]) != EXPECTED_FAILPOINTS:
        raise AssertionError("v0.38 failpoint sequence drifted")
    if int(crashes["case_count"]) != len(EXPECTED_FAILPOINTS):
        raise AssertionError("v0.38 crash case count drifted")
    for name in (
        "all_exact_committed_state_match",
        "all_recovery_scan_free",
        "all_second_recovery_idempotent",
    ):
        if not bool(crashes[name]):
            raise AssertionError(f"v0.38 crash aggregate failed: {name}")

    rows = {row["failpoint"]: row for row in crashes["rows"]}
    staged = rows["retirement_tail_release_staged"]
    if bool(staged["expected_committed"]):
        raise AssertionError("v0.38 staged failpoint incorrectly classified committed")
    if int(staged["arena_before_recovery"]["committed_arena_bytes"]) != 24576:
        raise AssertionError("v0.38 pre-commit committed arena target drifted")
    if int(staged["recovery_one"]["retirement_descriptor_arena_truncated_bytes"]) != 0:
        raise AssertionError("v0.38 pre-commit recovery incorrectly truncated arena")

    for name in ("committed", "retirement_tail_release_committed"):
        row = rows[name]
        if not bool(row["expected_committed"]):
            raise AssertionError(f"v0.38 post-commit failpoint misclassified: {name}")
        if not bool(row["frontier_rejected_stale_tail"]):
            raise AssertionError(f"v0.38 committed frontier exposed stale tail residue: {name}")
        before = row["arena_before_recovery"]
        if int(before["arena_file_bytes"]) != 24576:
            raise AssertionError(f"v0.38 post-commit residue physical length drifted: {name}")
        if int(before["committed_arena_bytes"]) != 16384:
            raise AssertionError(f"v0.38 post-commit target length drifted: {name}")
        if int(before["uncommitted_arena_tail_bytes"]) != 8192:
            raise AssertionError(f"v0.38 post-commit residue size drifted: {name}")
        if int(row["recovery_one"]["retirement_descriptor_arena_truncated_bytes"]) != 8192:
            raise AssertionError(f"v0.38 recovery did not remove exactly one descriptor pair: {name}")

    for name in ("retirement_arena_truncated", "retirement_tail_release_synced"):
        row = rows[name]
        if not bool(row["expected_committed"]):
            raise AssertionError(f"v0.38 post-truncate failpoint misclassified: {name}")
        if int(row["arena_before_recovery"]["arena_file_bytes"]) != 16384:
            raise AssertionError(f"v0.38 post-truncate arena length drifted: {name}")
        if int(row["recovery_one"]["retirement_descriptor_arena_truncated_bytes"]) != 0:
            raise AssertionError(f"v0.38 post-truncate recovery changed arena length: {name}")

    for row in crashes["rows"]:
        if not bool(row["exact_committed_state_match"]):
            raise AssertionError(f"v0.38 crash committed state mismatch: {row['failpoint']}")
        _require_scan_free(row["recovery_one"])
        _require_scan_free(row["recovery_two"])
        if int(row["recovery_two"]["physical_truncated_bytes"]) != 0:
            raise AssertionError("v0.38 second recovery changed primary file length")
        if int(row["recovery_two"]["retirement_descriptor_arena_truncated_bytes"]) != 0:
            raise AssertionError("v0.38 second recovery changed descriptor arena length")


def main() -> None:
    payload = run()
    _validate(payload)
    canonical = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    raw = canonical.encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    if digest != EXPECTED_SHA256:
        raise AssertionError(f"v0.38 canonical SHA-256 drifted: {digest}")
    generated = RESULTS_PATH.read_bytes()
    if generated != raw:
        raise AssertionError("v0.38 generated result bytes are not canonical")
    if hashlib.sha256(generated).hexdigest() != EXPECTED_SHA256:
        raise AssertionError("v0.38 generated result SHA-256 drifted")


if __name__ == "__main__":
    main()
