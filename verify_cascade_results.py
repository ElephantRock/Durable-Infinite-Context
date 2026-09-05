from __future__ import annotations

import json
from pathlib import Path

from run_cascade_experiment import run


ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "cascade_results.json"


def _require_equal(label: str, expected, actual) -> None:
    if expected != actual:
        raise AssertionError(f"{label}: recorded={expected!r} actual={actual!r}")


def _compare_recorded_integrated(recorded: dict, actual: dict) -> None:
    actual_by_key = {
        (row["entity_count"], row["operation"]): row
        for row in actual["integrated_rows"]
    }
    for row in recorded["integrated_rows"]:
        key = (row["entity_count"], row["operation"])
        if key not in actual_by_key:
            raise AssertionError(f"recorded integrated row missing from experiment: {key}")
        observed = actual_by_key[key]
        for field in (
            "invalidated_nodes",
            "rebuilt_nodes",
            "retired_nodes",
            "incremental_work",
            "full_rebuild_work",
        ):
            if field in row:
                _require_equal(f"integrated {key} {field}", row[field], observed[field])

        expected_fraction = row.get("work_fraction_vs_full_rebuild")
        if expected_fraction is not None:
            if abs(expected_fraction - observed["work_fraction_vs_full_rebuild"]) > 1e-15:
                raise AssertionError(
                    f"integrated {key} work fraction drift: "
                    f"recorded={expected_fraction!r} "
                    f"actual={observed['work_fraction_vs_full_rebuild']!r}"
                )

    if recorded.get("all_materializations_equal"):
        if not all(row["materialization_equal"] for row in actual["integrated_rows"]):
            raise AssertionError("recorded materialization parity no longer holds")
    if recorded.get("all_semantic_checks_passed"):
        if not all(row["semantic_check"] for row in actual["integrated_rows"]):
            raise AssertionError("recorded semantic checks no longer hold")
    if recorded.get("all_fresh_after_rebuild"):
        if not all(row["all_fresh_after_rebuild"] for row in actual["integrated_rows"]):
            raise AssertionError("recorded fresh-after-rebuild invariant no longer holds")


def _compare_recorded_topology(recorded: dict, actual: dict) -> None:
    rows = actual["topology_rows"]

    def lookup(total_branches: int, depth: int, fanout: int) -> dict:
        matches = [
            row
            for row in rows
            if row["total_branches"] == total_branches
            and row["depth"] == depth
            and row["fanout"] == fanout
        ]
        if not matches:
            raise AssertionError(
                f"topology row missing: branches={total_branches} depth={depth} fanout={fanout}"
            )
        # The D=4/F=4/1024 point appears only in the depth×fanout sweep; the
        # cardinality-control branch counts are 100/1000/10000.
        return matches[0]

    for row in recorded["topology_cardinality_control"]:
        observed = lookup(row["total_branches"], row["depth"], row["fanout"])
        _require_equal(
            "topology cardinality affected nodes",
            row["affected_nodes"],
            observed["invalidated_nodes"],
        )
        _require_equal(
            "topology cardinality invalidation work",
            row["invalidation_work"],
            observed["invalidation_work"],
        )
        _require_equal(
            "topology cardinality unaffected probe",
            row["unaffected_probe_fresh"],
            observed["unaffected_probe_fresh"],
        )

    for row in recorded["topology_depth_fanout"]:
        observed = lookup(1024, row["depth"], row["fanout"])
        _require_equal(
            "topology sweep affected nodes",
            row["affected_nodes"],
            observed["invalidated_nodes"],
        )
        _require_equal(
            "topology sweep invalidation work",
            row["invalidation_work"],
            observed["invalidation_work"],
        )
        expected_affected = row["depth"] * row["fanout"]
        expected_work = 2 + 3 * expected_affected
        _require_equal("topology formula affected nodes", expected_affected, observed["invalidated_nodes"])
        _require_equal("topology formula work", expected_work, observed["invalidation_work"])
        if not observed["unaffected_probe_fresh"]:
            raise AssertionError("unrelated topology branch became invalid")


def main() -> None:
    recorded_text = RESULTS_PATH.read_text()
    recorded = json.loads(recorded_text)
    actual = run()

    _require_equal("experiment", recorded["experiment"], actual["experiment"])
    _require_equal("cardinalities", recorded["cardinalities"], actual["cardinalities"])
    _compare_recorded_integrated(recorded, actual)
    _compare_recorded_topology(recorded, actual)

    # run() writes the verbose raw result to the same path. Restore the committed
    # compact ledger so local verification is non-destructive.
    RESULTS_PATH.write_text(recorded_text)
    print("RECORDED_CASCADE_RESULTS_MATCH_EXECUTABLE_EXPERIMENT")


if __name__ == "__main__":
    main()
