from __future__ import annotations

from dataclasses import dataclass

from core.models import Assertion, EvidenceRecord
from core.storage import MemoryStore
from state.dependencies import DependencyGraph


def subject_id(i: int) -> str:
    return f"cascade-subject-{i:07d}"


def evidence_id(i: int) -> str:
    return f"cascade-evidence-{i:07d}"


def assertion_id(i: int) -> str:
    return f"cascade-assertion-{i:07d}"


def alias(i: int, prefix: str = "Atlas") -> str:
    return f"{prefix}-{i:07d}"


def build_cascade_store(entity_count: int) -> MemoryStore:
    """One evidence/assertion/current-state key per subject for locality sweeps."""

    store = MemoryStore()
    for i in range(entity_count):
        eid = evidence_id(i)
        store.add_evidence(EvidenceRecord(
            id=eid,
            payload=f"{alias(i)} finance migration deadline is day 42.",
            source_id="source",
            recorded_seq=i + 1,
            source_event_time=42,
        ))
        store.add_assertion(Assertion(
            id=assertion_id(i),
            subject_id=subject_id(i),
            predicate="deadline",
            object_value=42,
            recorded_seq=i + 1,
            valid_from=42,
            evidence_ids=(eid,),
        ))
    return store


@dataclass(frozen=True)
class TopologyCase:
    total_branches: int
    depth: int
    fanout: int
    expected_affected_nodes: int
    total_derived_nodes: int
    graph: DependencyGraph
    root_id: str
    unaffected_probe: str | None


def build_topology_case(total_branches: int, depth: int, fanout: int) -> TopologyCase:
    """Create ``fanout`` target chains plus unrelated chains of equal depth.

    A single canonical target root fans out into ``fanout`` independent chains. The
    remaining branches hang from unrelated canonical roots. Therefore the true
    affected derived region is exactly ``fanout * depth`` regardless of the total
    number of branches in the graph.
    """

    if depth < 1:
        raise ValueError("depth must be >= 1")
    if fanout < 1 or fanout > total_branches:
        raise ValueError("fanout must be within [1, total_branches]")

    graph = DependencyGraph()
    root_id = "canonical:target"

    for branch in range(fanout):
        parent = root_id
        for layer in range(depth):
            node_id = f"target:{branch}:layer:{layer}"
            graph.register_derived(node_id, f"layer-{layer}", {parent})
            parent = node_id

    unaffected_probe = None
    for branch in range(fanout, total_branches):
        parent = f"canonical:unrelated:{branch}"
        for layer in range(depth):
            node_id = f"unrelated:{branch}:layer:{layer}"
            graph.register_derived(node_id, f"layer-{layer}", {parent})
            parent = node_id
            if unaffected_probe is None:
                unaffected_probe = node_id

    return TopologyCase(
        total_branches=total_branches,
        depth=depth,
        fanout=fanout,
        expected_affected_nodes=fanout * depth,
        total_derived_nodes=total_branches * depth,
        graph=graph,
        root_id=root_id,
        unaffected_probe=unaffected_probe,
    )
