from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from simulator.multi_free_bidirectional_queued_retirement_descriptor import (
    run_multi_free_bidirectional_queued_retirement_descriptor_experiment,
)

RESULTS_PATH = Path(__file__).with_name(
    "multi_free_bidirectional_queued_retirement_descriptor_results.json"
)


def run() -> dict[str, Any]:
    result = run_multi_free_bidirectional_queued_retirement_descriptor_experiment()
    RESULTS_PATH.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    result = run()
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
