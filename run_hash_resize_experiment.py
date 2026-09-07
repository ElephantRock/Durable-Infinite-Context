from __future__ import annotations

import json
from pathlib import Path

from simulator.normalized_membership import run_v016_normalized_case
from storage.hash_resize import run_hash_resize_envelope


ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "hash_resize_results.json"


def _require_semantic_guard(row: dict) -> None:
    if not row["membership_equal"]:
        raise AssertionError("membership drifted before v0.18 experiment")
    if not row["materialization_equal"] or not row["all_derived_fresh"]:
        raise AssertionError("semantic guard failed clean-rebuild parity")
    if not row["head_index_equal"]:
        raise AssertionError("semantic guard lost current-head parity")
    if not row["full_assembly_equal"] or not row["partial_assembly_equal"]:
        raise AssertionError("semantic guard changed logical profile semantics")


def run() -> dict:
    semantic_guard = run_v016_normalized_case(
        entity_count=128,
        predicate_count=16,
        history_depth=8,
        changed_count=1,
    )
    _require_semantic_guard(semantic_guard)

    envelope = run_hash_resize_envelope(
        checkpoints=(1_000, 4_000, 16_000, 64_000, 256_000),
        initial_capacity=128,
        slots_per_page=64,
        max_load=0.50,
        sample_count=512,
    )
    rows = envelope["rows"]

    # The attractive half of the hypothesis: with the load factor held at 0.5,
    # ordinary successful point lookups should stay in a tiny page envelope.
    if any(row["lookup_page_p95"] > 2 for row in rows):
        raise AssertionError(
            "bounded-load hash lookup did not achieve the expected small page envelope"
        )
    if any(row["lookup_page_max"] > 4 for row in rows):
        raise AssertionError("ordinary lookup page tail unexpectedly exploded")

    # The discriminating half: a conventional stop-the-world resize must move all
    # prior rows. If that spike grows with N, this mechanism is not sufficient for
    # global-N-independent mutation locality even if lookup is excellent.
    largest_spikes = [row["largest_single_resize_rows"] for row in rows]
    if largest_spikes != sorted(largest_spikes):
        raise AssertionError(f"resize spike decreased unexpectedly: {largest_spikes}")
    if largest_spikes[-1] <= largest_spikes[0]:
        raise AssertionError("experiment did not cross enough resize thresholds")
    for event in envelope["resize_events"]:
        if event["rehashed_rows"] != event["table_size_before"]:
            raise AssertionError("stop-the-world resize did not rehash every live row")

    for row in rows:
        print("HASH_RESIZE_N", row)

    out = {
        "experiment": "v0.18_hash_resize_envelope",
        "semantic_guard": {
            "membership_equal": semantic_guard["membership_equal"],
            "materialization_equal": semantic_guard["materialization_equal"],
            "head_index_equal": semantic_guard["head_index_equal"],
            "all_derived_fresh": semantic_guard["all_derived_fresh"],
            "full_assembly_equal": semantic_guard["full_assembly_equal"],
            "partial_assembly_equal": semantic_guard["partial_assembly_equal"],
        },
        "hash_envelope": envelope,
        "hypothesis_under_test": (
            "a conventional bounded-load open-addressed hash index is sufficient to replace "
            "the B-tree membership index when the requirement is both expected O(1) lookup "
            "page probes and global-N-independent mutation work"
        ),
        "prediction": (
            "successful point-lookup page probes and the largest per-insert relocation work "
            "must both remain bounded as N grows"
        ),
        "result": (
            "falsified as a sufficient mechanism: the bounded-load hash model keeps ordinary "
            "successful lookup in a tiny page envelope, but each capacity doubling rehashes "
            "all previously live rows, producing an O(N) mutation spike"
        ),
        "revision": (
            "constant expected point lookup is not enough; any hash/direct-address replacement "
            "must also make growth incremental or otherwise bound migration work per logical "
            "mutation, while preserving crash/read safety"
        ),
        "engineering_consequence": (
            "do not replace the production B-tree with a stop-the-world resized hash table. "
            "The next candidate should use incremental migration such as linear/extendible "
            "hashing or dual-generation rehash with a bounded migration budget per mutation."
        ),
        "measurement_scope": envelope["measurement_scope"],
    }
    RESULTS_PATH.write_text(json.dumps(out, indent=2))
    print("HASH_RESIZE_RESULTS_JSON")
    print(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    run()
