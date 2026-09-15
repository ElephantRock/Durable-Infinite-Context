from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import simulator.live_tail_evacuation as base
from storage.bounded_live_tail_evacuation_retirement_descriptor_primary import (
    LiveTailEvacuationRetirementDescriptorPrimaryStore,
)
from storage.fixed_page_primary import PAGE_SIZE
from storage.retirement_descriptor_pool import RETIREMENT_STATUS_QUEUED

# Reuse the existing fixture/crash/scaling machinery, but bind every candidate-store
# lookup to the claim-bearing sole-free implementation rather than the unguarded
# prototype primitive.
base.LiveTailEvacuationRetirementDescriptorPrimaryStore = (
    LiveTailEvacuationRetirementDescriptorPrimaryStore
)


def _candidate_release_case() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v040-release-") as tmp:
        path = Path(tmp) / "primary.pages"
        fixture = base._build_live_tail_fixture(
            path, LiveTailEvacuationRetirementDescriptorPrimaryStore
        )
        store = LiveTailEvacuationRetirementDescriptorPrimaryStore(str(path))
        before = store.retirement_queue_snapshot()
        arena_before = store.descriptor_arena_diagnostic()
        stale_tail_page = int(fixture["tail_page"])
        stale_tail_incarnation = int(fixture["tail_incarnation"])
        stale_destination_page = int(fixture["destination_page"])
        stale_destination_incarnation = int(fixture["destination_incarnation"])

        trace = store.evacuate_live_retirement_arena_tail_step()
        after = store.retirement_queue_snapshot()
        arena_after = store.descriptor_arena_diagnostic()

        if not trace.released or not trace.tail_was_queued or not trace.tail_was_queue_tail:
            raise AssertionError("v0.40 did not evacuate the live physical queue tail")
        if trace.predecessor_page != 2 or trace.destination_page != 0:
            raise AssertionError("v0.40 relocation did not use named predecessor/destination")
        if int(trace.retirement_descriptor_preads) != 6:
            raise AssertionError("v0.40 relocation should perform exactly three dual-copy reads")
        if int(trace.retirement_descriptor_pwrites) != 2:
            raise AssertionError("v0.40 relocation should perform exactly two descriptor rewrites")
        if int(trace.retirement_descriptors_scanned) != 0:
            raise AssertionError("v0.40 relocation scanned descriptor history")
        if int(trace.live_descriptor_relocations) != 1:
            raise AssertionError("v0.40 relocation count drifted")
        if int(before["descriptor_arena_pages"]) != 6 or int(after["descriptor_arena_pages"]) != 4:
            raise AssertionError("v0.40 committed arena frontier did not shrink 6 -> 4")
        if int(arena_before["arena_file_bytes"]) != 6 * PAGE_SIZE:
            raise AssertionError("v0.40 pre-relocation physical arena length drifted")
        if int(arena_after["arena_file_bytes"]) != 4 * PAGE_SIZE:
            raise AssertionError("v0.40 post-relocation physical arena length drifted")
        if [int(row["descriptor_page"]) for row in after["descriptors"]] != [2, 0]:
            raise AssertionError("v0.40 relocated queue is not [2,0]")
        if int(after["queue_count"]) != 2 or int(after["descriptor_free_count"]) != 0:
            raise AssertionError("v0.40 relocation changed queue/free counts incorrectly")
        if int(after["tail_page"]) != 0 or int(after["tail_predecessor_page"]) != 2:
            raise AssertionError("v0.40 committed queue-tail authority drifted")

        stale_tail_rejected = False
        try:
            store.retirement_descriptor_reference(
                stale_tail_page,
                stale_tail_incarnation,
                expected_status=RETIREMENT_STATUS_QUEUED,
            )
        except RuntimeError:
            stale_tail_rejected = True
        if not stale_tail_rejected:
            raise AssertionError("v0.40 truncated live-tail identity remained visible")

        stale_destination_rejected = False
        try:
            store.retirement_descriptor_reference(
                stale_destination_page,
                stale_destination_incarnation,
                expected_status=RETIREMENT_STATUS_QUEUED,
            )
        except RuntimeError:
            stale_destination_rejected = True
        if not stale_destination_rejected:
            raise AssertionError("v0.40 old FREE destination identity aliased relocated live state")

        current = store.retirement_descriptor_reference(
            int(after["tail_page"]),
            int(after["tail_incarnation"]),
            expected_status=RETIREMENT_STATUS_QUEUED,
        )
        return {
            "keys_inserted": int(fixture["next_key_index"]),
            "queue_before": before,
            "arena_before": arena_before,
            "trace": trace.to_dict(),
            "queue_after": after,
            "arena_after": arena_after,
            "stale_tail_rejected": stale_tail_rejected,
            "stale_destination_rejected": stale_destination_rejected,
            "current_destination_generation": int(current["generation"]),
            "released_arena_pages": 2,
            "released_arena_bytes": 2 * PAGE_SIZE,
        }


def run_live_tail_evacuation_experiment() -> dict[str, Any]:
    control = base._v039_control_case()
    candidate = _candidate_release_case()
    crashes = base._crash_matrix()
    scaling = base._scaling()
    return {
        "experiment": "v0.40-live-tail-evacuation",
        "survived": True,
        "v039_control": control,
        "live_tail_evacuation": candidate,
        "crash_matrix": crashes,
        "queue_depth_scaling": scaling,
        "claim": (
            "When the physical descriptor tail is also the logical queue tail and the sole "
            "committed FREE descriptor is a lower pair, one tagged queue-tail predecessor is "
            "sufficient to evacuate the live tail with three direct dual-copy reads, two "
            "descriptor rewrites, zero queue/history scans, and one live relocation before "
            "publishing a two-page-shorter authoritative arena frontier."
        ),
        "nonclaims": [
            "The physical tail must also be the logical queue tail in this experiment.",
            "The relocation destination must be the sole committed FREE descriptor and current free-list head.",
            "Multiple FREE descriptors require an additional successor-predecessor topology rewrite and are refused by v0.40.",
            "A live physical tail in the middle of the queue remains outside this claim.",
            "File-length truncation is not a claim about filesystem allocated-block reclamation.",
            "Process SIGKILL evidence is not a hardware power-loss or torn-write proof.",
            "Descriptor page/incarnation authority remains finite-width.",
        ],
    }
