from __future__ import annotations

import hashlib
import json
from pathlib import Path

import run_lazy_generation_allocation_experiment

ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "lazy_generation_allocation_results.json"
EXPECTED_RESULT_SHA256 = "bbf4b1285909fdaf429d59d7a63bbbd386b25b082f337188e083691f434eb405"
EXPECTED_ARTIFACT_SHA256 = "f78e49dfa066122c3cd52e6ab793ed4fc6aed5ccd860b6b7170c0a34e8535430"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_semantic_guard(out: dict) -> None:
    required = (
        "membership_equal",
        "materialization_equal",
        "head_index_equal",
        "all_derived_fresh",
        "full_assembly_equal",
        "partial_assembly_equal",
    )
    if not all(bool(out["semantic_guard"][name]) for name in required):
        raise AssertionError("v0.28 semantic guard failed")


def _require_sweep(out: dict) -> None:
    sweep = out["sweep"]
    capacities = [32, 128, 512, 2048]
    if sweep["capacities"] != capacities:
        raise AssertionError("v0.28 capacity sweep drifted")
    if int(sweep["page_size"]) != 4096 or int(sweep["bucket_size"]) != 4:
        raise AssertionError("v0.28 page/bucket geometry drifted")
    if int(sweep["sample_count"]) != 4096:
        raise AssertionError("v0.28 sample count drifted")

    rows = sweep["rows"]
    if len(rows) != 4:
        raise AssertionError("v0.28 row count drifted")
    for row, capacity in zip(rows, capacities):
        if int(row["pages_materialized_by_first_write"]) != 1:
            raise AssertionError("v0.28 first lazy write no longer materializes one logical page")
        if int(row["eager_extension_bytes"]) != 4096 * (capacity + 2):
            raise AssertionError("v0.28 eager control drifted")
        if int(row["lazy_first_bucket_bytes"]) != 4096:
            raise AssertionError("v0.28 first bucket residue drifted")
        if int(row["lazy_last_bucket_bytes"]) != 4096 * (capacity - 1):
            raise AssertionError("v0.28 bucket worst-case formula drifted")
        if int(row["lazy_stash_bytes"]) != 4096 * (capacity + 1):
            raise AssertionError("v0.28 stash formula drifted")
        if int(row["sampled_max_bytes"]) > int(row["lazy_last_bucket_bytes"]):
            raise AssertionError("v0.28 sample exceeded algebraic bucket maximum")
        if int(row["sampled_p95_bytes"]) < int(row["lazy_last_bucket_bytes"]) * 0.85:
            raise AssertionError("v0.28 deterministic sample no longer exercises high offsets")

    p95 = [int(row["sampled_p95_bytes"]) for row in rows]
    if p95 != sorted(p95):
        raise AssertionError("v0.28 sampled p95 no longer grows with capacity")


def main() -> None:
    recorded_text = RESULTS_PATH.read_text()
    recorded = json.loads(recorded_text)
    try:
        out = run_lazy_generation_allocation_experiment.run()
        _require_semantic_guard(out)
        _require_sweep(out)

        actual_sha = _sha256(RESULTS_PATH)
        if actual_sha != EXPECTED_RESULT_SHA256:
            raise AssertionError(
                "v0.28 executable evidence drifted from initial successful CI artifact: "
                f"expected {EXPECTED_RESULT_SHA256}, observed {actual_sha}"
            )
        if recorded != out:
            raise AssertionError("committed v0.28 ledger differs from executable experiment object")

        print("RECORDED_LAZY_GENERATION_ALLOCATION_RESULTS_MATCH_EXECUTABLE_EXPERIMENT")
        print(f"RESULT_SHA256={actual_sha}")
        print(f"INITIAL_ARTIFACT_SHA256={EXPECTED_ARTIFACT_SHA256}")
    finally:
        RESULTS_PATH.write_text(recorded_text)


if __name__ == "__main__":
    main()
