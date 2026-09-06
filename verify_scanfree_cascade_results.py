from __future__ import annotations

import json
from pathlib import Path

import run_cascade_experiment as experiment
from verify_cascade_results import (
    _compare_recorded_integrated,
    _compare_recorded_topology,
    _require_equal,
)

ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "cascade_results.json"


def main() -> None:
    recorded_text = RESULTS_PATH.read_text()
    recorded = json.loads(recorded_text)

    # v0.8 promoted scan-free affected-region discovery into the canonical
    # CascadeMaintainer and made the benchmark rebuild explicit target IDs. Re-run
    # the unchanged v0.7 scenarios through that ordinary path and require the same
    # compact ledger to reproduce.
    observed = experiment.run()

    _require_equal("experiment", recorded["experiment"], observed["experiment"])
    _require_equal("cardinalities", recorded["cardinalities"], observed["cardinalities"])
    _compare_recorded_integrated(recorded, observed)
    _compare_recorded_topology(recorded, observed)

    # run() writes verbose raw output to the ledger path. Restore the compact,
    # committed record so verification is non-destructive.
    RESULTS_PATH.write_text(recorded_text)
    print("RECORDED_CASCADE_RESULTS_MATCH_CANONICAL_SCANFREE_EXPERIMENT")


if __name__ == "__main__":
    main()
