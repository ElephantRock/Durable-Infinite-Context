from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class CrashRecoveryMeasurement:
    entity_count: int
    operation: str
    crash_phase: str
    read_blocked_before_recovery: bool
    affected_nodes_before_crash: int
    recovery_work: int
    canonical_mutations_during_recovery: int
    reinvalidation_work: int
    rebuilt_nodes: int
    rebuild_work: int
    full_rebuild_work: int
    materialization_equal: bool
    semantic_check: bool
    all_derived_fresh: bool
    journal_empty: bool

    @property
    def work_fraction_vs_full_rebuild(self) -> float:
        return self.recovery_work / max(self.full_rebuild_work, 1)

    def to_dict(self) -> dict:
        out = asdict(self)
        out["work_fraction_vs_full_rebuild"] = self.work_fraction_vs_full_rebuild
        return out
