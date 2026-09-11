from __future__ import annotations

import json
from pathlib import Path

from simulator.normalized_membership import run_v016_normalized_case
from simulator.recyclable_retirement_descriptors import (
    run_descriptor_storage_controls,
    run_real_recycling_cycles,
)

ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "recyclable_retirement_descriptor_results.json"


def _require_semantic_guard(row: dict) -> None:
    required = (
        "membership_equal",
        "materialization_equal",
        "head_index_equal",
        "all_derived_fresh",
        "full_assembly_equal",
        "partial_assembly_equal",
    )
    if not all(bool(row[name]) for name in required):
        raise AssertionError("v0.16 semantic guard failed before v0.35 experiment")


def run() -> dict:
    semantic_guard = run_v016_normalized_case(
        entity_count=128,
        predicate_count=16,
        history_depth=8,
        changed_count=1,
    )
    _require_semantic_guard(semantic_guard)
    controls = run_descriptor_storage_controls()
    cycles = run_real_recycling_cycles()

    if cycles["initial_descriptor_pages_appended"] != 6:
        raise AssertionError("three-descriptor initial pool did not append six pages")
    if cycles["descriptor_pages_appended_after_pool_established"] != 0:
        raise AssertionError("descriptor pool continued to grow after recyclable capacity existed")
    if cycles["descriptor_pool_count_after_five_generations"] != 3:
        raise AssertionError("descriptor pool grew with completed-generation history")
    if not cycles["stale_reference"]["stale_reference_rejected"]:
        raise AssertionError("tagged reference failed to reject ABA-style stale identity")
    if not cycles["all_257_keys_visible"]:
        raise AssertionError("real v0.35 cycle lost live primary keys")

    out = {
        "experiment": "v0.35_recyclable_retirement_descriptors",
        "semantic_guard": {
            "membership_equal": semantic_guard["membership_equal"],
            "materialization_equal": semantic_guard["materialization_equal"],
            "head_index_equal": semantic_guard["head_index_equal"],
            "all_derived_fresh": semantic_guard["all_derived_fresh"],
            "full_assembly_equal": semantic_guard["full_assembly_equal"],
            "partial_assembly_equal": semantic_guard["partial_assembly_equal"],
        },
        "storage_controls": controls,
        "real_recycling_cycles": cycles,
        "observe": (
            "v0.34 bounds queue publication and reclaim work but leaves each dequeued dual-copy "
            "retirement descriptor permanently allocated, so descriptor storage grows with completed "
            "generation history even after the queue drains."
        ),
        "first_principle": (
            "recycling a physical descriptor address is safe only when logical descriptor identity "
            "survives reuse. Foreground enqueue/reclaim must remain bounded by current queue/free heads, "
            "and restart must not reconstruct descriptor identity by scanning history."
        ),
        "hypothesis_under_test": (
            "tag every queue/free descriptor reference with a uint64 incarnation. Dequeue publishes the "
            "page pair as FREE under its current incarnation; reuse writes the next incarnation and "
            "publishes only that tagged reference. Readers select the newest committed physical copy "
            "before checking the expected tag, so an obsolete copy cannot satisfy a stale reference."
        ),
        "prediction": (
            "an append-only control grows two pages per completed generation, while a serial recyclable "
            "pool stays at one dual-copy descriptor. In the real primary, the initial three-descriptor "
            "pool should serve later generation retirements with zero new descriptor-page append, a "
            "non-empty enqueue should remain bounded by one free-head read plus one queue-tail read, and "
            "the old `(page, incarnation)` identity should be rejected after the same page is reused."
        ),
        "result": (
            "initial real-primary and ABA controls only; process-crash recycling matrices remain required "
            "before v0.35 can survive its full falsification gate."
        ),
    }
    RESULTS_PATH.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    return out


if __name__ == "__main__":
    run()
