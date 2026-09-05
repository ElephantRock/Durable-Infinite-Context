import json
from pathlib import Path

from benchmark.evaluator import default_state_architectures, evaluate
from benchmark.metrics import format_results
from simulator.world import build_suite


def main() -> None:
    scenarios = build_suite(n_each=250)  # 750 timelines, 2500 query cases
    rows = evaluate(scenarios, default_state_architectures())
    print(f"scenarios={len(scenarios)} queries={sum(len(s.queries) for s in scenarios)}")
    print(format_results(rows))
    Path("results.json").write_text(json.dumps({
        "scenarios": len(scenarios),
        "queries": sum(len(s.queries) for s in scenarios),
        "results": rows,
    }, indent=2))


if __name__ == "__main__":
    main()
