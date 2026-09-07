from __future__ import annotations

import json
from pathlib import Path

import run_rare_overflow_experiment


ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "rare_overflow_results.json"


def main() -> None:
    recorded_text = RESULTS_PATH.read_text()
    recorded = json.loads(recorded_text)
    try:
        observed = run_rare_overflow_experiment.run()
        if observed != recorded:
            raise AssertionError("recorded v0.22 evidence does not match executable experiment")
        print("RECORDED_RARE_OVERFLOW_RESULTS_MATCH_EXECUTABLE_EXPERIMENT")
    finally:
        RESULTS_PATH.write_text(recorded_text)


if __name__ == "__main__":
    main()
