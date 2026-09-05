from __future__ import annotations

import json
from pathlib import Path

from benchmark.maintenance_metrics import measure_result
from core.models import Assertion, EvidenceRecord
from rag.maintenance import AddressabilityMaintainer, MaintenanceResult
from rag.scalable_planner import MaintenanceTrace, ScalableQueryPlanner, SubjectProfileIndex
from simulator.maintenance import (
    alias,
    assertion_id,
    build_maintenance_store,
    evidence_id,
    subject_id,
)

ROOT = Path(__file__).resolve().parent


def combine_results(operation: str, *results: MaintenanceResult) -> MaintenanceResult:
    trace = MaintenanceTrace()
    subjects: set[str] = set()
    for result in results:
        trace.absorb(result.trace)
        subjects.update(result.affected_subject_ids)
    return MaintenanceResult(operation, tuple(sorted(subjects)), trace)


def record(
    rows: list[dict],
    n: int,
    result: MaintenanceResult,
    store,
    index: SubjectProfileIndex,
    *,
    semantic_check: bool,
) -> None:
    rebuilt = SubjectProfileIndex(store)
    measurement = measure_result(
        n,
        result,
        index,
        rebuilt,
        semantic_check=semantic_check,
    ).to_dict()
    rows.append(measurement)
    if not measurement["materialization_equal"]:
        raise AssertionError(f"incremental materialization drift after {result.operation} at N={n}")
    if not semantic_check:
        raise AssertionError(f"semantic check failed after {result.operation} at N={n}")


def run_cardinality(n: int) -> list[dict]:
    store = build_maintenance_store(n)
    index = SubjectProfileIndex(store)
    maintainer = AddressabilityMaintainer(store, index)
    rows: list[dict] = []

    # 1. Insert a new evidence/assertion pair. New evidence alone has no dependents;
    # the assertion makes exactly one new subject addressable.
    i = n
    r_e = maintainer.upsert_evidence(EvidenceRecord(
        evidence_id(i),
        f"{alias(i)} finance migration deadline is day 42.",
        "source",
        n + 1,
        source_event_time=42,
    ))
    r_a = maintainer.upsert_assertion(Assertion(
        assertion_id(i),
        subject_id(i),
        "deadline",
        42,
        n + 1,
        valid_from=42,
        evidence_ids=(evidence_id(i),),
    ))
    result = combine_results("insert_subject", r_e, r_a)
    semantic = ScalableQueryPlanner(index).plan(
        f"What is {alias(i)}'s due date?"
    ).subject_id == subject_id(i)
    record(rows, n, result, store, index, semantic_check=semantic)

    # 2. Replace the lexical payload behind one stable evidence address.
    i_alias = n // 5
    result = maintainer.upsert_evidence(EvidenceRecord(
        evidence_id(i_alias),
        f"{alias(i_alias, 'Nova')} finance migration deadline is day 42.",
        "source",
        n + 2,
        source_event_time=42,
    ))
    semantic = (
        subject_id(i_alias) in index.token_posting(alias(i_alias, "Nova").lower(), "deadline")
        and subject_id(i_alias) not in index.token_posting(alias(i_alias).lower(), "deadline")
    )
    record(rows, n, MaintenanceResult("replace_evidence_payload", result.affected_subject_ids, result.trace), store, index, semantic_check=semantic)

    # 3. Change an assertion predicate while retaining evidence and subject identity.
    i_pred = n // 3
    old = store.assertions[assertion_id(i_pred)]
    result = maintainer.upsert_assertion(Assertion(
        old.id,
        old.subject_id,
        "approved",
        True,
        old.recorded_seq,
        valid_from=old.valid_from,
        evidence_ids=old.evidence_ids,
    ))
    token = alias(i_pred).lower()
    semantic = (
        subject_id(i_pred) in index.token_posting(token, "approved")
        and subject_id(i_pred) not in index.token_posting(token, "deadline")
        and ScalableQueryPlanner(index).plan(f"Is {alias(i_pred)} approved?").subject_id == subject_id(i_pred)
    )
    record(rows, n, MaintenanceResult("change_predicate", result.affected_subject_ids, result.trace), store, index, semantic_check=semantic)

    # 4. Rebind one assertion to a new evidence record, changing its addressable alias.
    i_rebind = n // 2
    rebound_eid = f"rebound-{evidence_id(i_rebind)}"
    r_e = maintainer.upsert_evidence(EvidenceRecord(
        rebound_eid,
        f"{alias(i_rebind, 'Rebind')} finance migration deadline is day 42.",
        "source",
        n + 3,
        source_event_time=42,
    ))
    old = store.assertions[assertion_id(i_rebind)]
    r_a = maintainer.upsert_assertion(Assertion(
        old.id,
        old.subject_id,
        old.predicate,
        old.object_value,
        old.recorded_seq,
        valid_from=old.valid_from,
        evidence_ids=(rebound_eid,),
    ))
    result = combine_results("rebind_assertion_evidence", r_e, r_a)
    semantic = (
        subject_id(i_rebind) in index.token_posting(alias(i_rebind, "Rebind").lower(), "deadline")
        and subject_id(i_rebind) not in index.token_posting(alias(i_rebind).lower(), "deadline")
    )
    record(rows, n, result, store, index, semantic_check=semantic)

    # 5. Establish a small shared dependency, then mutate it. The measured refresh
    # should scale with fan-out (four subjects), not with N.
    shared_eid = f"shared-{n}"
    maintainer.upsert_evidence(EvidenceRecord(
        shared_eid, "SharedBeacon portfolio marker.", "source", n + 4
    ))
    fanout = tuple(sorted({n // 10, 3 * n // 10, 7 * n // 10, 9 * n // 10}))
    for j in fanout:
        old = store.assertions[assertion_id(j)]
        maintainer.upsert_assertion(Assertion(
            old.id,
            old.subject_id,
            old.predicate,
            old.object_value,
            old.recorded_seq,
            valid_from=old.valid_from,
            evidence_ids=tuple(dict.fromkeys((*old.evidence_ids, shared_eid))),
        ))

    result = maintainer.upsert_evidence(EvidenceRecord(
        shared_eid, "SharedNova portfolio marker.", "source", n + 5
    ))
    new_posting = index.token_posting("sharednova", None)
    old_posting = index.token_posting("sharedbeacon", None)
    semantic = (
        set(result.affected_subject_ids) == {subject_id(j) for j in fanout}
        and {subject_id(j) for j in fanout}.issubset(new_posting)
        and not ({subject_id(j) for j in fanout} & old_posting)
    )
    record(rows, n, MaintenanceResult("replace_shared_evidence", result.affected_subject_ids, result.trace), store, index, semantic_check=semantic)

    # 6. Delete one assertion. Its orphaned evidence remains canonical, but the
    # subject must disappear from addressability because nothing interprets it.
    i_delete_assertion = 3 * n // 5
    result = maintainer.delete_assertion(assertion_id(i_delete_assertion))
    semantic = subject_id(i_delete_assertion) not in index.profiles
    record(rows, n, result, store, index, semantic_check=semantic)

    # 7. Delete evidence while retaining the assertion reference. The missing source
    # removes the subject's lexical profile; a rebuild must make the same decision.
    i_delete_evidence = 4 * n // 5
    result = maintainer.delete_evidence(evidence_id(i_delete_evidence))
    semantic = subject_id(i_delete_evidence) not in index.profiles
    record(rows, n, result, store, index, semantic_check=semantic)

    return rows


def run():
    cardinalities = [100, 1_000, 10_000, 50_000]
    out = {
        "experiment": "v0.6_incremental_addressability_maintenance",
        "cardinalities": cardinalities,
        "logical_work_definition": (
            "subject refreshes + subject-local assertions/evidence/tokens examined + "
            "base/predicate posting membership mutations"
        ),
        "rows": [],
    }

    for n in cardinalities:
        rows = run_cardinality(n)
        out["rows"].extend(rows)
        print(
            "MAINT",
            n,
            {
                row["operation"]: {
                    "affected": row["affected_subjects"],
                    "work": row["incremental_work"],
                    "rebuild": row["rebuild_work"],
                    "fraction": round(row["work_fraction_vs_rebuild"], 8),
                    "equal": row["materialization_equal"],
                }
                for row in rows
            },
        )

    (ROOT / "maintenance_results.json").write_text(json.dumps(out, indent=2))
    print("MAINTENANCE_RESULTS_JSON")
    print(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    run()
