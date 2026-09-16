from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from run_deep_middle_live_tail_evacuation_experiment import RESULTS_PATH, run

EXPECTED_SHA256 = "6ced2a53b5c8fd61524ce005645b6d7303f87d0baf6b3cdcf3e844a4f0f710a4"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _topology(
    row: dict[str, Any],
    *,
    queue: list[int],
    free: list[int],
    arena: int,
    head: int,
    tail: int,
    tail_predecessor: int,
    physical_tail: int,
    physical_tail_predecessor: int,
    physical_tail_predecessor_predecessor: int | None,
) -> None:
    expected = {
        "queue": queue,
        "free": free,
        "arena_pages": arena,
        "head": head,
        "tail": tail,
        "tail_predecessor": tail_predecessor,
        "physical_tail": physical_tail,
        "physical_tail_predecessor": physical_tail_predecessor,
        "physical_tail_predecessor_predecessor": physical_tail_predecessor_predecessor,
    }
    _require(row == expected, f"topology drifted: {row!r} != {expected!r}")


def _zero_work(row: dict[str, Any], *, arena: int) -> None:
    _require(
        row
        == {
            "released": False,
            "preads": 0,
            "pwrites": 0,
            "scans": 0,
            "relocations": 0,
            "arena_before": arena,
            "arena_after": arena,
            "bytes_released": 0,
        },
        f"zero-work refusal drifted: {row!r}",
    )


def _crash_summary(
    row: dict[str, Any],
    *,
    count: int,
    failpoints: list[str],
    expected_states: list[str],
    arena_truncated: list[int],
    primary_truncated: list[int],
) -> None:
    _require(int(row["count"]) == count, "crash-case count drifted")
    _require(row["failpoints"] == failpoints, "crash failpoint ordering drifted")
    _require(row["expected_states"] == expected_states, "crash state classification drifted")
    _require(row["arena_truncated_bytes"] == arena_truncated, "arena cleanup drifted")
    _require(row["primary_truncated_bytes"] == primary_truncated, "primary cleanup drifted")
    _require(bool(row["all_exact"]), "crash recovery was not exact")
    _require(bool(row["all_scan_free"]), "crash recovery scanned history")
    _require(bool(row["all_second_idempotent"]), "second recovery was not idempotent")


def verify_payload(payload: dict[str, Any]) -> None:
    _require(
        payload["experiment"] == "v0.43-one-extra-hop-physical-tail-authority",
        "experiment id drifted",
    )
    _require(bool(payload["survived"]), "v0.43 experiment did not survive")
    _require(
        payload["claim_boundary"]
        == {
            "physical_tail_predecessor_is_queue_head": False,
            "physical_tail_predecessor_predecessor_is_queue_head": True,
            "physical_tail_successor_is_queue_tail": True,
            "queue_count": 4,
            "queue_or_history_walk": False,
            "sole_free_destination": True,
        },
        "claim boundary drifted",
    )

    control = payload["v042_control"]
    _topology(
        control["before"],
        queue=[4, 6, 8, 2],
        free=[0],
        arena=10,
        head=4,
        tail=2,
        tail_predecessor=8,
        physical_tail=8,
        physical_tail_predecessor=6,
        physical_tail_predecessor_predecessor=None,
    )
    _require(control["after"] == control["before"], "v0.42 control changed state")
    _require(bool(control["unchanged"]), "v0.42 control did not report exact no-op")
    _zero_work(control["work"], arena=10)

    candidate = payload["candidate"]
    _topology(
        candidate["before"],
        queue=[4, 6, 8, 2],
        free=[0],
        arena=10,
        head=4,
        tail=2,
        tail_predecessor=8,
        physical_tail=8,
        physical_tail_predecessor=6,
        physical_tail_predecessor_predecessor=4,
    )
    _topology(
        candidate["after"],
        queue=[4, 6, 0, 2],
        free=[],
        arena=8,
        head=4,
        tail=2,
        tail_predecessor=0,
        physical_tail=6,
        physical_tail_predecessor=4,
        physical_tail_predecessor_predecessor=None,
    )
    _require(
        candidate["work"]
        == {
            "released": True,
            "preads": 10,
            "pwrites": 2,
            "scans": 0,
            "relocations": 1,
            "arena_before": 10,
            "arena_after": 8,
            "bytes_released": 8192,
        },
        "candidate bounded-work result drifted",
    )
    _require(
        candidate["identity"]
        == {
            "physical_tail": [8, 5],
            "predecessor": [6, 4],
            "predecessor_predecessor": [4, 3],
            "successor": [2, 6],
            "destination_old": [0, 1],
            "destination_new": [0, 7],
            "new_physical_tail": [6, 4],
            "new_physical_tail_predecessor": [4, 3],
        },
        "tagged relocation identities drifted",
    )
    for name in (
        "stale_tail_rejected",
        "stale_destination_rejected",
        "successor_identity_preserved",
        "payload_preserved",
    ):
        _require(bool(candidate[name]), f"candidate invariant failed: {name}")

    _crash_summary(
        payload["relocation_crashes"],
        count=7,
        failpoints=[
            "retirement_deep_tail_destination_staged",
            "retirement_deep_tail_predecessor_staged",
            "retirement_deep_tail_dependencies_synced",
            "committed",
            "retirement_deep_tail_relocation_committed",
            "retirement_arena_truncated",
            "retirement_deep_tail_relocation_synced",
        ],
        expected_states=["pre", "pre", "pre", "post", "post", "post", "post"],
        arena_truncated=[0, 0, 0, 8192, 8192, 0, 0],
        primary_truncated=[0, 0, 0, 0, 0, 0, 0],
    )

    append = payload["append_authority_crashes"]
    _require(append["mode"] == "append" and append["trigger_key"] == "k-0064", "append fixture drifted")
    _topology(
        append["pre"],
        queue=[0, 2],
        free=[],
        arena=4,
        head=0,
        tail=2,
        tail_predecessor=0,
        physical_tail=2,
        physical_tail_predecessor=0,
        physical_tail_predecessor_predecessor=None,
    )
    _topology(
        append["post"],
        queue=[0, 2, 4],
        free=[],
        arena=6,
        head=0,
        tail=4,
        tail_predecessor=2,
        physical_tail=4,
        physical_tail_predecessor=2,
        physical_tail_predecessor_predecessor=0,
    )
    _crash_summary(
        append,
        count=5,
        failpoints=[
            "retirement_arena_descriptor_written",
            "retirement_tail_linked",
            "retirement_arena_synced",
            "data_synced",
            "committed",
        ],
        expected_states=["pre", "pre", "pre", "pre", "post"],
        arena_truncated=[8192, 8192, 8192, 8192, 0],
        primary_truncated=[0, 0, 0, 557056, 0],
    )

    reuse = payload["reuse_authority_crashes"]
    _require(reuse["mode"] == "reuse" and reuse["trigger_key"] == "k-0512", "reuse fixture drifted")
    _topology(
        reuse["pre"],
        queue=[4, 6, 8],
        free=[2, 0],
        arena=10,
        head=4,
        tail=8,
        tail_predecessor=6,
        physical_tail=8,
        physical_tail_predecessor=6,
        physical_tail_predecessor_predecessor=4,
    )
    _topology(
        reuse["post"],
        queue=[4, 6, 8, 2],
        free=[0],
        arena=10,
        head=4,
        tail=2,
        tail_predecessor=8,
        physical_tail=8,
        physical_tail_predecessor=6,
        physical_tail_predecessor_predecessor=4,
    )
    _crash_summary(
        reuse,
        count=5,
        failpoints=[
            "retirement_descriptor_reused",
            "retirement_tail_linked",
            "retirement_arena_synced",
            "data_synced",
            "committed",
        ],
        expected_states=["pre", "pre", "pre", "pre", "post"],
        arena_truncated=[0, 0, 0, 0, 0],
        primary_truncated=[0, 0, 0, 4177920, 0],
    )

    refusal = payload["deeper_prefix_refusal"]
    _topology(
        refusal["before"],
        queue=[4, 6, 8, 10, 2],
        free=[0],
        arena=12,
        head=4,
        tail=2,
        tail_predecessor=10,
        physical_tail=10,
        physical_tail_predecessor=8,
        physical_tail_predecessor_predecessor=6,
    )
    _require(refusal["after"] == refusal["before"], "deeper-prefix refusal changed state")
    _require(bool(refusal["unchanged"]), "deeper-prefix refusal did not report exact no-op")
    _zero_work(refusal["work"], arena=12)


def main() -> None:
    committed_bytes = RESULTS_PATH.read_bytes()
    committed_sha = hashlib.sha256(committed_bytes).hexdigest()
    _require(committed_sha == EXPECTED_SHA256, "committed canonical SHA-256 drifted")
    committed_payload = json.loads(committed_bytes)
    verify_payload(committed_payload)

    regenerated = run()
    regenerated_bytes = RESULTS_PATH.read_bytes()
    regenerated_sha = hashlib.sha256(regenerated_bytes).hexdigest()
    _require(regenerated_sha == EXPECTED_SHA256, "regenerated canonical SHA-256 drifted")
    _require(regenerated_bytes == committed_bytes, "canonical byte reproduction drifted")
    verify_payload(regenerated)
    print(f"v0.43 canonical result verified: sha256:{regenerated_sha}")


if __name__ == "__main__":
    main()
