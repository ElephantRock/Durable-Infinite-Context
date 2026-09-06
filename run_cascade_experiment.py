from __future__ import annotations

import json
from pathlib import Path

from benchmark.cascade_metrics import TopologyMeasurement, measure_cascade
from core.models import Assertion, AssertionRelation, EvidenceRecord, RelationType
from state.cascade import (
    CascadeMaintainer,
    CascadeMaterialization,
    CascadeResult,
    clone_canonical_store,
    context_node,
)
from state.dependencies import DependencyTrace, DerivationStatus
from simulator.cascade import (
    alias,
    assertion_id,
    build_cascade_store,
    build_topology_case,
    evidence_id,
    subject_id,
)

ROOT = Path(__file__).resolve().parent


def combine_results(operation: str, *results: CascadeResult) -> CascadeResult:
    trace = DependencyTrace()
    invalidated: set[str] = set()
    for result in results:
        trace.absorb(result.trace)
        invalidated.update(result.invalidated_node_ids)
    return CascadeResult(operation, tuple(sorted(invalidated)), trace)


def record_integrated(
    rows: list[dict],
    n: int,
    result: CascadeResult,
    materialization: CascadeMaterialization,
    *,
    semantic_check,
) -> None:
    rebuild_trace = materialization.rebuild(result.invalidated_node_ids)
    semantic = bool(semantic_check())
    oracle = CascadeMaterialization(clone_canonical_store(materialization.store))
    measurement = measure_cascade(
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
        raise AssertionError(f"cascade drift after {result.operation} at N={n}")
    if not measurement["semantic_check"]:
        raise AssertionError(f"semantic check failed after {result.operation} at N={n}")
    if not measurement["all_fresh_after_rebuild"]:
        raise AssertionError(f"invalid descendants remain after {result.operation} at N={n}")


def run_integrated_cardinality(n: int) -> list[dict]:
    store = build_cascade_store(n)
    materialization = CascadeMaterialization(store)
    maintainer = CascadeMaintainer(materialization)
    rows: list[dict] = []

    # 1. Evidence payload changes provenance/addressability but not current state.
    i_evidence = max(1, n // 7)
    result = maintainer.upsert_evidence(EvidenceRecord(
        evidence_id(i_evidence),
        f"{alias(i_evidence, 'Nova')} finance migration deadline is day 42.",
        "source",
        n + 1,
        source_event_time=42,
    ))
    key_evidence = (subject_id(i_evidence), "deadline", "default")
    record_integrated(
        rows,
        n,
        CascadeResult("replace_evidence_payload", result.invalidated_node_ids, result.trace),
        materialization,
        semantic_check=lambda: (
            materialization.store.state[key_evidence].operative_values == [42]
            and "Nova" in (materialization.read_context(key_evidence) or "")
            and subject_id(i_evidence)
            in materialization.index.token_posting(alias(i_evidence, "Nova").lower(), "deadline")
        ),
    )

    # 2. Object-only assertion replacement changes current state but leaves the
    # lexical/predicate addressability profile valid.
    i_object = max(2, n // 5)
    old = store.assertions[assertion_id(i_object)]
    result = maintainer.upsert_assertion(Assertion(
        old.id,
        old.subject_id,
        old.predicate,
        43,
        old.recorded_seq,
        valid_from=old.valid_from,
        valid_to=old.valid_to,
        evidence_ids=old.evidence_ids,
    ))
    key_object = old.key
    record_integrated(
        rows,
        n,
        CascadeResult("replace_assertion_object", result.invalidated_node_ids, result.trace),
        materialization,
        semantic_check=lambda: (
            materialization.store.state[key_object].operative_values == [43]
            and materialization.graph.status_of(f"profile:{old.subject_id}") == DerivationStatus.FRESH
        ),
    )

    # 3. Insert a correcting assertion plus explicit CORRECTS relation. The state
    # chain and profile are invalidated, but unrelated subjects remain untouched.
    i_correction = max(3, n // 3)
    correction_eid = f"correction-evidence-{n}"
    correction_aid = f"correction-assertion-{n}"
    r_e = maintainer.upsert_evidence(EvidenceRecord(
        correction_eid,
        f"Correction {alias(i_correction)} deadline is day 45.",
        "authoritative",
        n + 2,
        source_event_time=42,
    ))
    r_a = maintainer.upsert_assertion(Assertion(
        correction_aid,
        subject_id(i_correction),
        "deadline",
        45,
        n + 2,
        valid_from=42,
        evidence_ids=(correction_eid,),
    ))
    r_r = maintainer.add_relation(AssertionRelation(
        correction_aid,
        RelationType.CORRECTS,
        assertion_id(i_correction),
    ))
    result = combine_results("insert_correction", r_e, r_a, r_r)
    key_correction = (subject_id(i_correction), "deadline", "default")
    record_integrated(
        rows,
        n,
        result,
        materialization,
        semantic_check=lambda: (
            materialization.store.state[key_correction].operative_values == [45]
            and assertion_id(i_correction)
            in materialization.store.state[key_correction].historical_assertion_ids
            and "Correction" in (materialization.read_context(key_correction) or "")
        ),
    )

    # 4. Establish a shared evidence dependency across four subjects, repair that
    # setup locally, then measure only the shared evidence replacement cascade.
    shared_eid = f"shared-cascade-{n}"
    setup_results: list[CascadeResult] = [maintainer.upsert_evidence(EvidenceRecord(
        shared_eid, "SharedBeacon portfolio marker.", "source", n + 3
    ))]
    fanout_indices = tuple(sorted({n // 10, 3 * n // 10, 7 * n // 10, 9 * n // 10}))
    fanout_indices = tuple(i for i in fanout_indices if 0 <= i < n)
    for i in fanout_indices:
        old = store.assertions[assertion_id(i)]
        setup_results.append(maintainer.upsert_assertion(Assertion(
            old.id,
            old.subject_id,
            old.predicate,
            old.object_value,
            old.recorded_seq,
            valid_from=old.valid_from,
            valid_to=old.valid_to,
            evidence_ids=tuple(dict.fromkeys((*old.evidence_ids, shared_eid))),
        )))
    setup = combine_results("shared_setup", *setup_results)
    materialization.rebuild(setup.invalidated_node_ids)

    result = maintainer.upsert_evidence(EvidenceRecord(
        shared_eid, "SharedNova portfolio marker.", "source", n + 4
    ))
    fanout_subjects = {subject_id(i) for i in fanout_indices}
    record_integrated(
        rows,
        n,
        CascadeResult("replace_shared_evidence", result.invalidated_node_ids, result.trace),
        materialization,
        semantic_check=lambda: (
            fanout_subjects.issubset(materialization.index.token_posting("sharednova", None))
            and all(
                "SharedNova" in (
                    materialization.read_context((sid, "deadline", "default")) or ""
                )
                for sid in fanout_subjects
            )
        ),
    )

    # 5. Deleting one assertion removes its state/profile/support/context branch
    # while preserving unrelated derived nodes.
    i_delete = max(4, 3 * n // 5)
    delete_key = (subject_id(i_delete), "deadline", "default")
    result = maintainer.delete_assertion(assertion_id(i_delete))
    record_integrated(
        rows,
        n,
        CascadeResult("delete_assertion", result.invalidated_node_ids, result.trace),
        materialization,
        semantic_check=lambda: (
            delete_key not in materialization.store.state
            and delete_key not in materialization.supports
            and delete_key not in materialization.contexts
            and subject_id(i_delete) not in materialization.index.profiles
        ),
    )

    return rows


def run_topology_sweeps() -> list[dict]:
    rows: list[dict] = []

    for total_branches in (100, 1_000, 10_000):
        case = build_topology_case(total_branches, depth=4, fanout=4)
        trace = case.graph.invalidate_from([case.root_id])
        measurement = TopologyMeasurement(
            total_branches=case.total_branches,
            depth=case.depth,
            fanout=case.fanout,
            expected_affected_nodes=case.expected_affected_nodes,
            invalidated_nodes=trace.nodes_invalidated,
            total_derived_nodes=case.total_derived_nodes,
            invalidation_work=trace.logical_work,
            edges_traversed=trace.edges_traversed,
            unaffected_probe_fresh=(
                case.unaffected_probe is None
                or case.graph.status_of(case.unaffected_probe) == DerivationStatus.FRESH
            ),
        ).to_dict()
        if not measurement["exact_affected_region"] or not measurement["unaffected_probe_fresh"]:
            raise AssertionError(f"topology cardinality locality failed at branches={total_branches}")
        rows.append(measurement)

    for depth in (1, 2, 4, 8):
        for fanout in (1, 4, 16, 64):
            case = build_topology_case(1_024, depth=depth, fanout=fanout)
            trace = case.graph.invalidate_from([case.root_id])
            measurement = TopologyMeasurement(
                total_branches=case.total_branches,
                depth=case.depth,
                fanout=case.fanout,
                expected_affected_nodes=case.expected_affected_nodes,
                invalidated_nodes=trace.nodes_invalidated,
                total_derived_nodes=case.total_derived_nodes,
                invalidation_work=trace.logical_work,
                edges_traversed=trace.edges_traversed,
                unaffected_probe_fresh=(
                    case.unaffected_probe is None
                    or case.graph.status_of(case.unaffected_probe) == DerivationStatus.FRESH
                ),
            ).to_dict()
            if not measurement["exact_affected_region"] or not measurement["unaffected_probe_fresh"]:
                raise AssertionError(f"topology locality failed at depth={depth} fanout={fanout}")
            rows.append(measurement)

    return rows


def run():
    cardinalities = [100, 1_000, 10_000, 50_000]
    integrated_rows: list[dict] = []
    for n in cardinalities:
        rows = run_integrated_cardinality(n)
        integrated_rows.extend(rows)
        print(
            "CASCADE",
            n,
            {
                row["operation"]: {
                    "invalidated": row["invalidated_nodes"],
                    "rebuilt": row["rebuilt_nodes"],
                    "work": row["incremental_work"],
                    "full": row["full_rebuild_work"],
                    "fraction": round(row["work_fraction_vs_full_rebuild"], 8),
                    "equal": row["materialization_equal"],
                }
                for row in rows
            },
        )

    topology_rows = run_topology_sweeps()
    out = {
        "experiment": "v0.7_dependency_cascade_invalidation",
        "cardinalities": cardinalities,
        "integrated_rows": integrated_rows,
        "topology_rows": topology_rows,
        "invariant": (
            "invalidation + selective rebuild work should scale with the true affected "
            "dependency subgraph rather than total derived-memory cardinality"
        ),
    }
    (ROOT / "cascade_results.json").write_text(json.dumps(out, indent=2))
    print("CASCADE_RESULTS_JSON")
    print(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    run()
