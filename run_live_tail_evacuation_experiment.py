from __future__ import annotations

import json
from pathlib import Path

from simulator.bounded_live_tail_evacuation import run_live_tail_evacuation_experiment
from simulator.live_tail_predecessor_crash import (
    run_live_tail_predecessor_crash_experiment,
)

RESULTS_PATH = Path(__file__).resolve().parent / "live_tail_evacuation_results.json"
DIAGNOSTIC_PATH = Path(__file__).resolve().parent / "live_tail_evacuation_diagnostic.json"


def run() -> dict:
    result = run_live_tail_evacuation_experiment()
    result["tail_predecessor_maintenance_crashes"] = (
        run_live_tail_predecessor_crash_experiment()
    )
    RESULTS_PATH.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> None:
    try:
        run()
    except BaseException as exc:
        DIAGNOSTIC_PATH.write_text(
            json.dumps(
                {
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        raise


if __name__ == "__main__":
    main()
