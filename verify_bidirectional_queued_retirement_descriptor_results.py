from __future__ import annotations

import hashlib
from typing import Any

from run_bidirectional_queued_retirement_descriptor_experiment import RESULTS_PATH, run

EXPECTED_SHA256 = "1b387911d187559352eaf2eea6b275047911e89c154197d2585dab22c2ff0d3e"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _require_topology(
    row: dict[str, Any],
    *,
    queue: list[int],
    free: list[int],
    arena_pages: int,
    head: int,
    tail: int,
    tail_predecessor: int,
    physical_tail: int,
    physical_tail_predecessor: int,
    queued_predecessors: list[int | None],
) -> None:
    expected = {
        "queue": queue,
        "free": free,
        "arena_pages": arena_pages,
        "head": head,
        "tail": tail,
        "tail_predecessor": tail_predecessor,
        "physical_tail": physical_tail,
        "physical_tail_predecessor": physical_tail_predecessor,
        "queued_predecessors": queued_predecessors,
    }
    _require(row == expected, f"topology drifted: {row!r} != {expected!r}")


def _require_crash_group(row: dict[str, Any], *, count: int) -> None:
    _require(int(row["count"]) == count, "crash-case count drifted")
    _require(len(row["cases"]) == count, "crash-case payload count drifted")
    _require(bool(row["all_exact"]), "crash recovery was not exact")
    _require(bool(row["all_scan_free"]), "crash recovery scanned history")
    _require(bool(row["all_second_idempotent"]), "second recovery was not idempotent")
    for case in row["cases"]:
        _require(bool(case["exact"]), f"crash case was not exact: {case!r}")
        _require(bool(case["scan_free"]), f"crash case scanned history: {case!r}")
        _require(
            bool(case["second_idempotent"]),
            f"second recovery was not idempotent: {case!r}",
        )
        _require(case["expected_state"] in {"pre", "post"}, "invalid crash classification")


def verify_payload(payload: dict[str, Any]) -> None:
    _require(
        payload["experiment"] == "v0.44-tagged-queued-predecessor-authority",
        "experiment id drifted",
    )
    _require(bool(payload["survived"]), "v0.44 experiment did not survive")
    _require(
        payload["claim_boundary"]
        == {
            "minimum_live_queue_depth": 5,
            "physical_tail_successor_is_queue_tail": True,
            "queue_or_history_walk": False,
            "single_writer": True,
            "sole_free_destination": True,
            "superblock_reverse_window_claim_bearing": False,
            "tagged_predecessor_per_queued_descriptor": True,
        },
        "claim boundary drifted",
    )

    control = payload["v043_control"]
    _require(
        control
        == {
            "before": {
                "arena_pages": 12,
                "free": [0],
                "physical_tail": 10,
                "queue": [4, 6, 8, 10, 2],
            },
            "descriptor_preads": 0,
            "released": False,
            "unchanged": True,
        },
        "v0.43 control drifted",
    )

    candidate = payload["candidate"]
    _require_topology(
        candidate["before"],
        queue=[4, 6, 8, 10, 2],
        free=[0],
        arena_pages=12,
        head=4,
        tail=2,
        tail_predecessor=10,
        physical_tail=10,
        physical_tail_predecessor=8,
        queued_predecessors=[None, 4, 6, 8, 10],
    )
    _require_topology(
        candidate["after"],
        queue=[4, 6, 8, 0, 2],
        free=[],
        arena_pages=10,
        head=4,
        tail=2,
        tail_predecessor=0,
        physical_tail=8,
        physical_tail_predecessor=6,
        queued_predecessors=[None, 4, 6, 8, 0],
    )
    _require(
        candidate["work"]
        == {
            "arena_after": 10,
            "arena_before": 12,
            "bytes_released": 8192,
            "descriptor_preads": 10,
            "descriptor_pwrites": 2,
            "descriptor_scans": 0,
            "predecessor_preads": 8,
            "predecessor_pwrites": 2,
            "predecessor_scans": 0,
            "released": True,
            "relocations": 1,
        },
        "candidate direct-work accounting drifted",
    )
    _require(
        candidate["identity"]
        == {
            "destination_new": [0, 8],
            "destination_old": [0, 1],
            "new_physical_tail": [8, 5],
            "new_physical_tail_predecessor": [6, 4],
            "physical_tail": [10, 6],
            "predecessor": [8, 5],
            "predecessor_predecessor": [6, 4],
            "successor": [2, 7],
        },
        "candidate tagged identities drifted",
    )
    for name in (
        "payload_preserved",
        "queued_predecessor_consistent",
        "stale_destination_descriptor_rejected",
        "stale_destination_predecessor_rejected",
        "stale_tail_descriptor_rejected",
        "stale_tail_predecessor_rejected",
    ):
        _require(bool(candidate[name]), f"candidate invariant failed: {name}")

    append = payload["append_authority_crashes"]
    reuse = payload["reuse_authority_crashes"]
    reclaim = payload["reclaim_authority_crashes"]
    relocation = payload["relocation_crashes"]
    _require_crash_group(append, count=6)
    _require_crash_group(reuse, count=6)
    _require_crash_group(reclaim, count=6)
    _require_crash_group(relocation, count=10)
    _require(
        sum(int(row["count"]) for row in (append, reuse, reclaim, relocation)) == 28,
        "combined crash matrix count drifted",
    )

    _require(append["mode"] == "append", "append mode drifted")
    _require(reuse["mode"] == "reuse", "reuse mode drifted")
    _require(append["trigger_key"] == "k-0064", "append trigger drifted")
    _require(reuse["trigger_key"] == "k-1024", "reuse trigger drifted")

    _require(
        relocation["failpoints"]
        == [
            "retirement_biqueue_destination_staged",
            "retirement_biqueue_predecessor_staged",
            "retirement_biqueue_destination_prev_staged",
            "retirement_biqueue_successor_prev_staged",
            "retirement_biqueue_dependencies_synced",
            "committed",
            "retirement_biqueue_relocation_committed",
            "retirement_arena_truncated",
            "retirement_queued_predecessor_truncated",
            "retirement_biqueue_relocation_synced",
        ],
        "relocation failpoint ordering drifted",
    )
    _require(relocation["clean_work"] == candidate["work"], "relocation oracle drifted")

    scaling = payload["scaling"]
    _require(bool(scaling["constant_direct_work"]), "direct work grew with prefix depth")
    _require(scaling["depths"] == [5, 6, 7, 8], "scaling depths drifted")
    for row in scaling["rows"]:
        _require(
            (
                int(row["descriptor_preads"]),
                int(row["descriptor_pwrites"]),
                int(row["predecessor_preads"]),
                int(row["predecessor_pwrites"]),
                int(row["descriptor_scans"]),
                int(row["predecessor_scans"]),
                int(row["relocations"]),
                int(row["bytes_released"]),
            )
            == (10, 2, 8, 2, 0, 0, 1, 8192),
            f"scaling work drifted: {row!r}",
        )

    malformed = payload["malformed_predecessor_refusal"]
    _require(bool(malformed["rejected"]), "malformed predecessor was not rejected")
    _require(
        bool(malformed["files_unchanged_by_refusal"]),
        "malformed predecessor refusal changed files",
    )
    _require(
        (int(malformed["physical_tail"]), int(malformed["malformed_prev"])) == (10, 4),
        "malformed predecessor fixture drifted",
    )


def main() -> None:
    regenerated = run()
    regenerated_bytes = RESULTS_PATH.read_bytes()
    regenerated_sha = hashlib.sha256(regenerated_bytes).hexdigest()
    _require(regenerated_sha == EXPECTED_SHA256, "canonical SHA-256 drifted")
    _require(b'"allocated_bytes"' not in regenerated_bytes, "environment-sensitive allocation leaked into canonical evidence")
    verify_payload(regenerated)
    print(f"v0.44 canonical result verified: sha256:{regenerated_sha}")


if __name__ == "__main__":
    main()
