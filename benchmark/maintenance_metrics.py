from __future__ import annotations

from dataclasses import asdict, dataclass

from rag.maintenance import MaintenanceResult
from rag.scalable_planner import SubjectProfileIndex


@dataclass(frozen=True)
class MaintenanceMeasurement:
    entity_count: int
    operation: str
    affected_subjects: int
    incremental_work: int
    rebuild_work: int
    materialization_equal: bool
    semantic_check: bool
    subjects_refreshed: int
    assertions_examined: int
    evidence_records_examined: int
    profile_tokens_examined: int
    posting_mutations: int
    predicate_posting_mutations: int

    @property
    def work_fraction_vs_rebuild(self) -> float:
        return self.incremental_work / max(self.rebuild_work, 1)

    def to_dict(self) -> dict:
        out = asdict(self)
        out["work_fraction_vs_rebuild"] = self.work_fraction_vs_rebuild
        return out


def measure_result(
    entity_count: int,
    result: MaintenanceResult,
    incremental_index: SubjectProfileIndex,
    rebuilt_index: SubjectProfileIndex,
    *,
    semantic_check: bool,
) -> MaintenanceMeasurement:
    trace = result.trace
    return MaintenanceMeasurement(
        entity_count=entity_count,
        operation=result.operation,
        affected_subjects=len(result.affected_subject_ids),
        incremental_work=trace.logical_work,
        rebuild_work=rebuilt_index.build_trace.logical_work,
        materialization_equal=incremental_index.equivalent_to(rebuilt_index),
        semantic_check=semantic_check,
        subjects_refreshed=trace.subjects_refreshed,
        assertions_examined=trace.assertions_examined,
        evidence_records_examined=trace.evidence_records_examined,
        profile_tokens_examined=trace.profile_tokens_examined,
        posting_mutations=trace.posting_additions + trace.posting_removals,
        predicate_posting_mutations=(
            trace.predicate_posting_additions + trace.predicate_posting_removals
        ),
    )
