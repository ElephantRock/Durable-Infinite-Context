from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import run_predicate_schema_experiment as experiment


ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "predicate_schema_results.json"


def require_equal(label: str, expected: Any, observed: Any) -> None:
    if expected != observed:
        raise AssertionError(f"{label}: expected {expected!r}, observed {observed!r}")


def require_trace_consistency(label: str, case: dict[str, Any]) -> None:
    trace = case["recovery_trace"]
    require_equal(f"{label}.trace.logical_work", case["recovery_work"], trace["logical_work"])


def require_safety(observed: dict[str, Any]) -> None:
    control = observed["control"]
    require_equal("control.canonical_changed", True, control["canonical_changed"])
    require_equal("control.new_context_present", True, control["new_context_present"])
    require_equal("control.old_context_retired", True, control["old_context_retired"])
    require_equal("control.profile_present", False, control["profile_present"])
    require_equal("control.subject_derived_count", 3, control["subject_derived_count"])
    require_equal("control.materialization_equal", False, control["materialization_equal"])
    require_equal("control.all_derived_fresh", True, control["all_derived_fresh"])
    require_trace_consistency("control", control)

    replacement = observed["replacement"]
    require_equal("replacement.canonical_changed", True, replacement["canonical_changed"])
    require_equal("replacement.new_context_present", True, replacement["new_context_present"])
    require_equal("replacement.old_context_retired", True, replacement["old_context_retired"])
    require_equal("replacement.profile_predicates", ["launch_date"], replacement["profile_predicates"])
    require_equal("replacement.subject_derived_count", 4, replacement["subject_derived_count"])
    require_equal("replacement.materialization_equal", True, replacement["materialization_equal"])
    require_equal("replacement.all_derived_fresh", True, replacement["all_derived_fresh"])
    require_equal("replacement.profile_lookup_uses_index", True, replacement["profile_lookup_uses_index"])
    require_trace_consistency("replacement", replacement)

    addition = observed["addition"]
    require_equal("addition.profile_predicates", ["deadline", "launch_date"], addition["profile_predicates"])
    require_equal("addition.subject_derived_count", 7, addition["subject_derived_count"])
    require_equal("addition.deadline_context_present", True, addition["deadline_context_present"])
    require_equal("addition.new_context_present", True, addition["new_context_present"])
    require_equal("addition.materialization_equal", True, addition["materialization_equal"])
    require_equal("addition.all_derived_fresh", True, addition["all_derived_fresh"])
    require_equal("addition.profile_lookup_uses_index", True, addition["profile_lookup_uses_index"])
    require_equal("addition.queue.done", 2, addition["queue_final"]["done"])
    require_equal("addition.queue.conflict", 0, addition["queue_final"]["conflict"])

    removal = observed["removal"]
    require_equal("removal.deadline_assertion_present", False, removal["deadline_assertion_present"])
    require_equal("removal.added_assertion_present", True, removal["added_assertion_present"])
    require_equal("removal.deadline_context_present", False, removal["deadline_context_present"])
    require_equal("removal.new_context_present", True, removal["new_context_present"])
    require_equal("removal.profile_predicates", ["launch_date"], removal["profile_predicates"])
    require_equal("removal.subject_derived_count", 4, removal["subject_derived_count"])
    require_equal("removal.materialization_equal", True, removal["materialization_equal"])
    require_equal("removal.all_derived_fresh", True, removal["all_derived_fresh"])
    require_equal("removal.profile_lookup_uses_index", True, removal["profile_lookup_uses_index"])
    require_equal("removal.queue.done", 3, removal["queue_final"]["done"])
    require_equal("removal.queue.conflict", 0, removal["queue_final"]["conflict"])

    work = set()
    for row in observed["locality_rows"]:
        n = row["entity_count"]
        work.add(row["recovery_work"])
        require_equal(f"locality[{n}].canonical_changed", True, row["canonical_changed"])
        require_equal(f"locality[{n}].new_context_present", True, row["new_context_present"])
        require_equal(f"locality[{n}].old_context_retired", True, row["old_context_retired"])
        require_equal(f"locality[{n}].profile_predicates", ["launch_date"], row["profile_predicates"])
        require_equal(f"locality[{n}].subject_derived_count", 4, row["subject_derived_count"])
        require_equal(f"locality[{n}].materialization_equal", True, row["materialization_equal"])
        require_equal(f"locality[{n}].all_derived_fresh", True, row["all_derived_fresh"])
        require_equal(f"locality[{n}].profile_lookup_uses_index", True, row["profile_lookup_uses_index"])
        require_trace_consistency(f"locality[{n}]", row)
        if row["recovery_work"] >= row["full_rebuild_work"]:
            raise AssertionError(
                f"locality[{n}] local repair not cheaper than rebuild: "
                f"{row['recovery_work']} >= {row['full_rebuild_work']}"
            )
    require_equal("predicate replacement recovery-work cardinality", 1, len(work))


def main() -> None:
    recorded_text = RESULTS_PATH.read_text()
    recorded = json.loads(recorded_text)
    try:
        observed = experiment.run()
        require_safety(observed)
        require_equal("committed v0.13 evidence", recorded, observed)
        print("RECORDED_PREDICATE_SCHEMA_RESULTS_MATCH_EXECUTABLE_EXPERIMENT")
    finally:
        RESULTS_PATH.write_text(recorded_text)


if __name__ == "__main__":
    main()
