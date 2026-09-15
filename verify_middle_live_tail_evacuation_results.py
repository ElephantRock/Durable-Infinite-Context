from __future__ import annotations

import hashlib
import json

from run_middle_live_tail_evacuation_experiment import RESULTS_PATH, run

EXPECTED_SHA256 = "9a62e5a95978c2a680602539eb9be6312b51a9e46a23956e0ed5837eec5477e0"
EXPECTED_RELOCATION_FAILPOINTS = [
    "retirement_middle_tail_destination_staged",
    "retirement_middle_tail_predecessor_staged",
    "retirement_middle_tail_dependencies_synced",
    "committed",
    "retirement_middle_tail_relocation_committed",
    "retirement_arena_truncated",
    "retirement_middle_tail_relocation_synced",
]
EXPECTED_AUTHORITY_FAILPOINTS = [
    "retirement_descriptor_reused",
    "retirement_tail_linked",
    "retirement_arena_synced",
    "data_synced",
    "committed",
]


def _require_topology(
    row: dict,
    *,
    queue: list[int],
    free: list[int],
    arena_pages: int,
    head: int,
    tail: int,
    tail_predecessor: int,
    free_head: int | None,
    physical_tail: int | None,
    physical_tail_predecessor: int | None,
) -> None:
    expected = {
        "queue": queue,
        "free": free,
        "arena_pages": arena_pages,
        "head": head,
        "tail": tail,
        "tail_predecessor": tail_predecessor,
        "free_head": free_head,
        "physical_tail": physical_tail,
        "physical_tail_predecessor": physical_tail_predecessor,
    }
    if row != expected:
        raise AssertionError(f"v0.42 topology drifted: {row!r} != {expected!r}")


def _validate_crash_aggregates(matrix: dict, expected_failpoints: list[str]) -> None:
    if list(matrix["failpoints"]) != expected_failpoints:
        raise AssertionError("v0.42 crash failpoint sequence drifted")
    if int(matrix["case_count"]) != len(expected_failpoints):
        raise AssertionError("v0.42 crash case count drifted")
    for name in (
        "all_exact_committed_state_match",
        "all_recovery_scan_free",
        "all_second_recovery_idempotent",
    ):
        if not bool(matrix[name]):
            raise AssertionError(f"v0.42 crash aggregate failed: {name}")
    if len(matrix["cases"]) != len(expected_failpoints):
        raise AssertionError("v0.42 crash rows drifted")
    for row in matrix["cases"]:
        if not bool(row["exact"]) or not bool(row["scan_free"]) or not bool(row["second_idempotent"]):
            raise AssertionError(f"v0.42 crash invariant failed: {row['failpoint']}")


def _validate(payload: dict) -> None:
    if payload["experiment"] != "v0.42-middle-live-tail-evacuation":
        raise AssertionError("unexpected v0.42 experiment id")
    if not bool(payload["survived"]):
        raise AssertionError("v0.42 candidate no longer survives")
    if payload["claim_boundary"] != {
        "physical_tail_predecessor_is_queue_head": True,
        "physical_tail_successor_is_queue_tail": True,
        "queue_count": 3,
        "queue_or_history_walk": False,
        "sole_free_destination": True,
    }:
        raise AssertionError("v0.42 claim boundary drifted")

    control = payload["v041_control"]
    if control["trigger_key"] != "k-0256" or not bool(control["exact_state_unchanged"]):
        raise AssertionError("v0.42 v0.41 control trigger/state drifted")
    for name in ("before", "after"):
        _require_topology(
            control[name],
            queue=[4, 6, 2],
            free=[0],
            arena_pages=8,
            head=4,
            tail=2,
            tail_predecessor=6,
            free_head=0,
            physical_tail=None,
            physical_tail_predecessor=None,
        )
    ctrace = control["trace"]
    if bool(ctrace["released"]):
        raise AssertionError("v0.41 control unexpectedly released interior tail")
    for name in (
        "retirement_arena_pages_released",
        "retirement_arena_bytes_released",
        "retirement_descriptor_preads",
        "retirement_descriptor_pwrites",
        "retirement_descriptors_scanned",
        "live_descriptor_relocations",
    ):
        if int(ctrace[name]) != 0:
            raise AssertionError(f"v0.41 control work drifted: {name}")

    candidate = payload["middle_tail_evacuation"]
    if candidate["trigger_key"] != "k-0256":
        raise AssertionError("v0.42 trigger key drifted")
    _require_topology(
        candidate["before"],
        queue=[4, 6, 2],
        free=[0],
        arena_pages=8,
        head=4,
        tail=2,
        tail_predecessor=6,
        free_head=0,
        physical_tail=6,
        physical_tail_predecessor=4,
    )
    _require_topology(
        candidate["after"],
        queue=[4, 0, 2],
        free=[],
        arena_pages=6,
        head=4,
        tail=2,
        tail_predecessor=0,
        free_head=None,
        physical_tail=4,
        physical_tail_predecessor=None,
    )
    for name in (
        "stale_tail_rejected",
        "stale_destination_rejected",
        "successor_identity_preserved",
        "relocated_payload_preserved",
    ):
        if not bool(candidate[name]):
            raise AssertionError(f"v0.42 identity/payload invariant failed: {name}")

    trace = candidate["trace"]
    exact_trace = {
        "retirement_arena_pages_before": 8,
        "retirement_arena_pages_after": 6,
        "retirement_arena_pages_released": 2,
        "retirement_arena_bytes_released": 8192,
        "retirement_descriptor_free_count_before": 1,
        "retirement_descriptor_free_count_after": 0,
        "retirement_descriptor_preads": 8,
        "retirement_descriptor_pwrites": 2,
        "retirement_descriptors_scanned": 0,
        "live_descriptor_relocations": 1,
        "physical_tail_page": 6,
        "physical_tail_incarnation": 4,
        "predecessor_page": 4,
        "predecessor_incarnation": 3,
        "successor_page": 2,
        "successor_incarnation": 5,
        "destination_page": 0,
        "destination_old_incarnation": 1,
        "destination_new_incarnation": 6,
        "new_physical_tail_page": 4,
        "new_physical_tail_incarnation": 3,
        "retirement_arena_fsyncs": 2,
    }
    if not bool(trace["released"]):
        raise AssertionError("v0.42 trace lost successful relocation")
    for name, expected in exact_trace.items():
        if int(trace[name]) != expected:
            raise AssertionError(f"v0.42 trace drifted: {name}")

    relocation = payload["relocation_crash_matrix"]
    _validate_crash_aggregates(relocation, EXPECTED_RELOCATION_FAILPOINTS)
    rows = {row["failpoint"]: row for row in relocation["cases"]}
    for name in EXPECTED_RELOCATION_FAILPOINTS[:3]:
        row = rows[name]
        if row["expected_state"] != "pre":
            raise AssertionError(f"v0.42 relocation precommit classification drifted: {name}")
        if int(row["committed_arena_bytes_before_recovery"]) != 32768:
            raise AssertionError(f"v0.42 precommit arena frontier drifted: {name}")
        if int(row["first_arena_truncated_bytes"]) != 0:
            raise AssertionError(f"v0.42 precommit recovery truncated arena: {name}")
    for name in ("committed", "retirement_middle_tail_relocation_committed"):
        row = rows[name]
        if row["expected_state"] != "post":
            raise AssertionError(f"v0.42 postcommit classification drifted: {name}")
        if int(row["arena_file_bytes_before_recovery"]) != 32768:
            raise AssertionError(f"v0.42 postcommit physical arena drifted: {name}")
        if int(row["committed_arena_bytes_before_recovery"]) != 24576:
            raise AssertionError(f"v0.42 postcommit target frontier drifted: {name}")
        if int(row["first_arena_truncated_bytes"]) != 8192:
            raise AssertionError(f"v0.42 postcommit recovery did not remove one pair: {name}")
    for name in ("retirement_arena_truncated", "retirement_middle_tail_relocation_synced"):
        row = rows[name]
        if row["expected_state"] != "post":
            raise AssertionError(f"v0.42 post-truncate classification drifted: {name}")
        if int(row["arena_file_bytes_before_recovery"]) != 24576:
            raise AssertionError(f"v0.42 post-truncate physical length drifted: {name}")
        if int(row["first_arena_truncated_bytes"]) != 0:
            raise AssertionError(f"v0.42 post-truncate recovery changed arena: {name}")

    authority = payload["physical_tail_authority_crashes"]
    _validate_crash_aggregates(authority, EXPECTED_AUTHORITY_FAILPOINTS)
    if int(authority["trigger_index"]) != 256 or authority["trigger_key"] != "k-0256":
        raise AssertionError("v0.42 physical-tail authority trigger drifted")
    _require_topology(
        authority["pre_topology"],
        queue=[4, 6],
        free=[2, 0],
        arena_pages=8,
        head=4,
        tail=6,
        tail_predecessor=4,
        free_head=2,
        physical_tail=6,
        physical_tail_predecessor=4,
    )
    _require_topology(
        authority["post_topology"],
        queue=[4, 6, 2],
        free=[0],
        arena_pages=8,
        head=4,
        tail=2,
        tail_predecessor=6,
        free_head=0,
        physical_tail=6,
        physical_tail_predecessor=4,
    )
    if authority["clean_trace"] != {
        "retirement_arena_pages": 8,
        "retirement_descriptor_free_count": 1,
        "retirement_descriptor_preads": 6,
        "retirement_descriptor_pwrites": 3,
        "retirement_descriptor_reuses": 1,
        "retirement_queue_count": 3,
    }:
        raise AssertionError("v0.42 physical-tail authority clean trace drifted")
    authority_rows = {row["failpoint"]: row for row in authority["cases"]}
    for name in EXPECTED_AUTHORITY_FAILPOINTS[:-1]:
        if authority_rows[name]["expected_state"] != "pre":
            raise AssertionError(f"v0.42 authority precommit classification drifted: {name}")
        if int(authority_rows[name]["first_arena_truncated_bytes"]) != 0:
            raise AssertionError(f"v0.42 FREE-reuse recovery changed arena: {name}")
    if authority_rows["committed"]["expected_state"] != "post":
        raise AssertionError("v0.42 authority committed classification drifted")
    if int(authority_rows["committed"]["first_arena_truncated_bytes"]) != 0:
        raise AssertionError("v0.42 committed FREE-reuse recovery changed arena")


def main() -> None:
    frozen = RESULTS_PATH.read_bytes()
    payload = run()
    _validate(payload)

    canonical = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if frozen != canonical:
        raise AssertionError("v0.42 committed result file is not the canonical serialization")
    if RESULTS_PATH.read_bytes() != canonical:
        raise AssertionError("v0.42 runner did not reproduce canonical result bytes")
    if b"allocated_bytes" in canonical:
        raise AssertionError("v0.42 canonical evidence retained filesystem allocation observations")
    actual = hashlib.sha256(canonical).hexdigest()
    if actual != EXPECTED_SHA256:
        raise AssertionError(f"v0.42 canonical SHA-256 drifted: {actual}")

    print(f"v0.42 canonical result verified: sha256:{actual}")


if __name__ == "__main__":
    main()
