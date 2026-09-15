from __future__ import annotations

import json
from pathlib import Path

from simulator.multi_free_live_tail_evacuation import (
    run_multi_free_live_tail_evacuation_experiment,
)

ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "multi_free_live_tail_evacuation_results.json"
DIAGNOSTIC_PATH = ROOT / "multi_free_live_tail_evacuation_diagnostic.json"


def run() -> dict:
    result = run_multi_free_live_tail_evacuation_experiment()
    RESULTS_PATH.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
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
