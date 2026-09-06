from __future__ import annotations

import json
from pathlib import Path

from benchmark.recovery_metrics import CrashRecoveryMeasurement
from core.models import Assertion, EvidenceRecord
from simulator.cascade import alias, assertion_id, build_cascade_store, evidence_id, subject_id
from state.cascade import CascadeMaterialization, clone_canonical_store
from state.recovery import MaintenancePhase, RecoveryCoordinator

ROOT = Path(__file__).resolve().parent


OPERATIONS = ("replace_evidence_payload", "replace_assertion_object", "delete_assertion")
CRASH_PHASES = (
    MaintenancePhase.PREPARED,
    MaintenancePhase.CANONICAL_APPLIED,
    MaintenancePhase.INVALIDATED,
    MaintenancePhase.REBUILDING,
    MaintenancePhase.REPAIRED,
)


def prepare_case(n: int, operation: str):
    store = build_cascade_store(n)
    coordinator = RecoveryCoordinator(CascadeMaterialization(store))

    if operation == "replace_evidence_payload":
        i = max(1, n // 7)
        key = (subject_id(i), "deadline", "default")
        intent_id = coordinator.prepare_upsert_evidence(EvidenceRecord(
            evidence_id(i),
            f"{alias(i, 'Nova')} finance migration deadline is day 42.",
            "source",
            n + 101,
            source_event_time=42,
        ))
        expected = {"kind": "evidence", "key": key, "token": "Nova"}

    elif operation == "replace_assertion_object":
        i = max(2, n // 5)
        old = coordinator.store.assertions[assertion_id(i)]
        intent_id = coordinator.prepare_upsert_assertion(Assertion(
            old.id,
            old.subject_id,
            old.predicate,
            77,
            old.recorded_seq,
            valid_from=old.valid_from,
            valid_to=old.valid_to,
            evidence_ids=old.evidence_ids,
        ))
        expected = {"kind": "value", "key": old.key, "value": 77}

    elif operation == "delete_assertion":
        i = max(4, 3 * n // 5)
        old = coordinator.store.assertions[assertion_id(i)]
        intent_id = coordinator.prepare_delete_assertion(old.id)
        expected = {
            "kind": "delete",
            "key": old.key,
            "subject_id": old.subject_id,
        }

    else:
        raise ValueError(f"Unknown operation: {operation}")

    return coordinator, intent_id, expected


def semantic_check(coordinator: RecoveryCoordinator, expected: dict) -> bool:
    kind = expected["kind"]
    key = expected["key"]

    if kind == "evidence":
        return (
            coordinator.store.state[key].operative_values == [42]
            and expected["token"] in (coordinator.read_context(key) or "")
        )
    if kind == "value":
        return coordinator.store.state[key].operative_values == [expected["value"]]
    if kind == "delete":
        return (
            key not in coordinator.store.state
            and key not in coordinator.materialization.supports
            and key not in coordinator.materialization.contexts
            and expected["subject_id"] not in coordinator.materialization.index.profiles
        )
    raise ValueError(f"Unknown expectation: {kind}")


def run_case(n: int, operation: str, phase: MaintenancePhase) -> dict:
    coordinator, intent_id, expected = prepare_case(n, operation)
    coordinator.run_until(intent_id, phase)
    affected_before = len(coordinator.journal[intent_id].affected_node_ids)

    read_blocked = False
    try:
        coordinator.read_context(expected["key"])
    except RuntimeError:
        read_blocked = True

    restarted = coordinator.durable_image()
    recovery_trace = restarted.recover_all()
    oracle = CascadeMaterialization(clone_canonical_store(restarted.store))

    measurement = CrashRecoveryMeasurement(
        entity_count=n,
        operation=operation,
        crash_phase=phase.value,
        read_blocked_before_recovery=read_blocked,
        affected_nodes_before_crash=affected_before,
        recovery_work=recovery_trace.logical_work,
        canonical_mutations_during_recovery=recovery_trace.canonical_mutations,
        reinvalidation_work=recovery_trace.reinvalidation.logical_work,
        rebuilt_nodes=recovery_trace.rebuild.nodes_rebuilt,
        rebuild_work=recovery_trace.rebuild.logical_work,
        full_rebuild_work=oracle.build_trace.logical_work,
        materialization_equal=restarted.materialization.equivalent_to(oracle),
        semantic_check=semantic_check(restarted, expected),
        all_derived_fresh=restarted.all_derived_fresh(),
        journal_empty=not restarted.journal,
    ).to_dict()

    required = (
        measurement["read_blocked_before_recovery"]
        and measurement["materialization_equal"]
        and measurement["semantic_check"]
        and measurement["all_derived_fresh"]
        and measurement["journal_empty"]
    )
    if not required:
        raise AssertionError(
            f"recovery gate failed: N={n} operation={operation} phase={phase.value}: {measurement}"
        )
    return measurement


def run():
    phase_rows: list[dict] = []
    for operation in OPERATIONS:
        for phase in CRASH_PHASES:
            row = run_case(100, operation, phase)
            phase_rows.append(row)
            print(
                "RECOVERY_PHASE",
                operation,
                phase.value,
                {
                    "work": row["recovery_work"],
                    "canonical": row["canonical_mutations_during_recovery"],
                    "rebuilt": row["rebuilt_nodes"],
                    "equal": row["materialization_equal"],
                },
            )

    cardinalities = [100, 1_000, 10_000, 50_000]
    locality_rows: list[dict] = []
    for n in cardinalities:
        row = run_case(n, "delete_assertion", MaintenancePhase.REBUILDING)
        locality_rows.append(row)
        print(
            "RECOVERY_SCALE",
            n,
            {
                "work": row["recovery_work"],
                "rebuilt": row["rebuilt_nodes"],
                "full": row["full_rebuild_work"],
                "fraction": round(row["work_fraction_vs_full_rebuild"], 8),
            },
        )

    locality_work = [row["recovery_work"] for row in locality_rows]
    if max(locality_work) > max(1, min(locality_work)) * 1.25:
        raise AssertionError(
            f"recovery locality degraded with unrelated cardinality: {locality_work}"
        )
    if any(row["rebuilt_nodes"] > 4 for row in locality_rows):
        raise AssertionError("deletion recovery rebuilt outside the expected local branch")

    out = {
        "experiment": "v0.8_interrupted_maintenance_crash_recovery",
        "phase_matrix_entity_count": 100,
        "crash_phases": [phase.value for phase in CRASH_PHASES],
        "operations": list(OPERATIONS),
        "phase_rows": phase_rows,
        "locality_cardinalities": cardinalities,
        "locality_rows": locality_rows,
        "invariant": (
            "a durable maintenance intent must prevent stale derived reads and permit "
            "idempotent recovery proportional to the recorded affected region rather "
            "than total memory"
        ),
        "scope": (
            "deep-copied durable-image simulation; single in-flight mutation; no claim "
            "of filesystem/database atomicity, concurrent writers, or distributed recovery"
        ),
    }
    (ROOT / "recovery_results.json").write_text(json.dumps(out, indent=2))
    print("RECOVERY_RESULTS_JSON")
    print(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    run()
