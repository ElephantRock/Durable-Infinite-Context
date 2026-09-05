from __future__ import annotations

from dataclasses import asdict, dataclass

from state.cascade import CascadeMaterialization
from state.dependencies import DependencyTrace


@dataclass(frozen=True)
class CascadeMeasurement:
    entity_count: int
    operation: str
    invalidated_nodes: int
    rebuilt_nodes: int
    invalidation_work: int
    rebuild_work: int
    incremental_work: int
    full_rebuild_work: int
    total_derived_nodes: int
    materialization_equal: bool
    semantic_check: bool
    all_fresh_after_rebuild: bool

    @property
    def work_fraction_vs_full_rebuild(self) -> float:
        return self.incremental_work / max(self.full_rebuild_work, 1)

    @property
    def affected_fraction(self) -> float:
        return self.invalidated_nodes / max(self.total_derived_nodes, 1)

    def to_dict(self) -> dict:
        out = asdict(self)
        out["work_fraction_vs_full_rebuild"] = self.work_fraction_vs_full_rebuild
        out["affected_fraction"] = self.affected_fraction
        return out


@dataclass(frozen=True)
class TopologyMeasurement:
    total_branches: int
    depth: int
    fanout: int
    expected_affected_nodes: int
    invalidated_nodes: int
    total_derived_nodes: int
    invalidation_work: int
    edges_traversed: int
    unaffected_probe_fresh: bool

    @property
    def exact_affected_region(self) -> bool:
        return self.invalidated_nodes == self.expected_affected_nodes

    @property
    def work_per_affected_node(self) -> float:
        return self.invalidation_work / max(self.invalidated_nodes, 1)

    def to_dict(self) -> dict:
        out = asdict(self)
        out["exact_affected_region"] = self.exact_affected_region
        out["work_per_affected_node"] = self.work_per_affected_node
        return out


def measure_cascade(
    *,
    entity_count: int,
    operation: str,
    invalidated_nodes: int,
    invalidation_trace: DependencyTrace,
    rebuild_trace: DependencyTrace,
    materialization: CascadeMaterialization,
    oracle: CascadeMaterialization,
    semantic_check: bool,
) -> CascadeMeasurement:
    incremental_work = invalidation_trace.logical_work + rebuild_trace.logical_work
    return CascadeMeasurement(
        entity_count=entity_count,
        operation=operation,
        invalidated_nodes=invalidated_nodes,
        rebuilt_nodes=rebuild_trace.nodes_rebuilt,
        invalidation_work=invalidation_trace.logical_work,
        rebuild_work=rebuild_trace.logical_work,
        incremental_work=incremental_work,
        full_rebuild_work=oracle.build_trace.logical_work,
        total_derived_nodes=len(materialization.graph.derived_nodes()),
        materialization_equal=materialization.equivalent_to(oracle),
        semantic_check=semantic_check,
        all_fresh_after_rebuild=not materialization.graph.invalid_nodes(),
    )
