from __future__ import annotations

import json
from pathlib import Path

import run_normalized_membership_experiment as experiment


ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "normalized_membership_results.json"


def main() -> None:
    recorded_text = RESULTS_PATH.read_text()
    recorded = json.loads(recorded_text)
    try:
        observed = experiment.run()
        if observed != recorded:
            raise AssertionError("committed v0.16 evidence differs from executable experiment")
        print("RECORDED_NORMALIZED_MEMBERSHIP_RESULTS_MATCH_EXECUTABLE_EXPERIMENT")
    finally:
        RESULTS_PATH.write_text(recorded_text)


if __name__ == "__main__":
    main()
