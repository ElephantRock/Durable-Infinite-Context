from __future__ import annotations

import hashlib
import json
from pathlib import Path

from run_descriptor_tail_release_experiment import RESULTS_PATH, run

EXPECTED_SHA256 = "76f17b72a44e59d193a7e0cf785f57b782935b64c2809929fc6dbc78ecdd0bb5"
EXPECTED_CASES = {
    "single_descriptor_case": {
        "key_count": 17,
        "expected_descriptor_pool": 1,
        "committed_physical_frontier_page": 88,
        "free_head_page": 52,
        "free_head_end_page": 54,
        "committed_suffix_pages_after_free_head": 34,
        "minimum_committed_suffix_pages_after_free_descriptor": 34,
        "layout": [(52, 54, 34, 1)],
    },
    "three_descriptor_peak_case": {
        "key_count": 65,
        "expected_descriptor_pool": 3,
        "committed_physical_frontier_page": 296,
        "free_head_page": 158,
        "free_head_end_page": 160,
        "committed_suffix_pages_after_free_head": 136,
        "minimum_committed_suffix_pages_after_free_descriptor": 136,
        "layout": [(158, 160, 136, 1), (88, 90, 206, 1), (52, 54, 242, 1)],
    },
}


def verify() -> None:
    payload = run()
    if payload["experiment"] != "v0.36_descriptor_tail_release":
        raise AssertionError("v0.36 experiment identifier drifted")
    if not all(bool(v) for v in payload["semantic_guard"].values()):
        raise AssertionError("v0.16 semantic guard failed")

    control = payload["tail_release_control"]
    if not control["falsified"]:
        raise AssertionError("v0.36 candidate is no longer falsified")
    if (int(control["candidate_history_walks"]), int(control["candidate_relocations"]), int(control["candidate_physical_pages_released"])) != (0, 0, 0):
        raise AssertionError("v0.36 candidate work/release invariant drifted")

    for name, expected in EXPECTED_CASES.items():
        row = control[name]
        for field in (
            "key_count",
            "expected_descriptor_pool",
            "committed_physical_frontier_page",
            "free_head_page",
            "free_head_end_page",
            "committed_suffix_pages_after_free_head",
            "minimum_committed_suffix_pages_after_free_descriptor",
        ):
            if int(row[field]) != expected[field]:
                raise AssertionError(f"v0.36 {name} {field} drifted")
        if row["free_head_is_physical_tail"] or row["head_only_tail_release_possible"]:
            raise AssertionError(f"v0.36 {name} unexpectedly became tail-releasable")
        if not row["all_free_descriptors_buried_below_tail"]:
            raise AssertionError(f"v0.36 {name} lost buried-descriptor evidence")
        if not row["diagnostic_free_chain_traversal_is_not_candidate_work"]:
            raise AssertionError("v0.36 diagnostic traversal was reclassified as candidate work")
        if int(row["descriptor_history_walks_required_by_control"]) != 0 or int(row["physical_pages_released_by_control"]) != 0:
            raise AssertionError(f"v0.36 {name} control invariant drifted")
        pool = expected["expected_descriptor_pool"]
        if int(row["queue_before_drain"]["descriptor_pool_count"]) != pool:
            raise AssertionError(f"v0.36 {name} pre-drain pool drifted")
        if int(row["drained_queue"]["descriptor_pool_count"]) != pool or int(row["drained_queue"]["descriptor_free_count"]) != pool or int(row["drained_queue"]["queue_count"]) != 0:
            raise AssertionError(f"v0.36 {name} drain state drifted")
        layout = [
            (int(x["descriptor_page"]), int(x["descriptor_end_page"]), int(x["committed_suffix_pages"]), int(x["descriptor_incarnation"]))
            for x in row["free_descriptor_layout_diagnostic"]
        ]
        if layout != expected["layout"] or any(x["is_physical_tail"] or int(x["committed_suffix_pages"]) <= 0 for x in row["free_descriptor_layout_diagnostic"]):
            raise AssertionError(f"v0.36 {name} physical layout drifted")

    canonical = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    if hashlib.sha256(canonical).hexdigest() != EXPECTED_SHA256:
        raise AssertionError("v0.36 canonical result hash drifted")
    generated = Path(RESULTS_PATH).read_bytes()
    if generated != canonical or hashlib.sha256(generated).hexdigest() != EXPECTED_SHA256:
        raise AssertionError("v0.36 generated result bytes drifted")


if __name__ == "__main__":
    verify()
