from __future__ import annotations

import json
from pathlib import Path

import run_cascade_experiment as legacy
from state.cascade import CascadeMaterialization, clone_canonical_store
from state.scanfree_cascade import ScanFreeCascadeMaintainer

ROOT = Path(__file__).resolve().parent


def local_record_integrated(
    rows: list[dict],
    n: int,
    result,
    materialization: CascadeMaterialization,
    *,
    semantic_check,
) -> None:
    # The affected node IDs are emitted by the traversal itself. Passing them into
    # rebuild avoids the legacy no-argument whole-graph invalid-node discovery scan.
    rebuild_trace = materialization.rebuild(result.invalidated_node_ids)
    semantic = bool(semantic_check())
    oracle = CascadeMaterialization(clone_canonical_store(materialization.store))
    measurement = legacy.measure_cascade(
        entity_count=n,
        operation=result.operation,
        invalidated_nodes=len(result.invalidated_node_ids),
        invalidation_trace=result.trace,
        rebuild_trace=rebuild_trace,
        materialization=materialization,
        oracle=oracle,
        semantic_check=semantic,
    ).to_dict()
    rows.append(measurement)

    if not measurement["materialization_equal"]:
        raise AssertionError(f"scan-free cascade drift after {result.operation} at N={n}")
    if not measurement["semantic_check"]:
        raise AssertionError(f"scan-free semantic check failed after {result.operation} at N={n}")
    if not measurement["all_fresh_after_rebuild"]:
        raise AssertionError(f"scan-free invalid descendants remain after {result.operation} at N={n}")


def main() -> None:
    expected_path = ROOT / "cascade_results.json"
    expected = json.loads(expected_path.read_text())

    # Keep the original v0.7 scenario definitions and acceptance metrics fixed;
    # replace only the hidden whole-graph affected-region discovery mechanism.
    legacy.CascadeMaintainer = ScanFreeCascadeMaintainer
    legacy.record_integrated = local_record_integrated
    observed = legacy.run()

    if observed != expected:
        raise AssertionError(
            "scan-free v0.7 reproduction differs from committed cascade_results.json"
        )
    print("SCANFREE_CASCADE_RESULTS_MATCH")


if __name__ == "__main__":
    main()
