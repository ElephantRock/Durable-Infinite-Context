from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum


class DerivationStatus(str, Enum):
    FRESH = "fresh"
    INVALID = "invalid"
    REBUILDING = "rebuilding"


@dataclass
class DependencyTrace:
    roots_seen: int = 0
    nodes_visited: int = 0
    edges_traversed: int = 0
    nodes_invalidated: int = 0
    nodes_rebuilt: int = 0
    nodes_retired: int = 0
    dependency_edges_registered: int = 0
    dependency_edges_removed: int = 0
    assertion_reads: int = 0
    relation_reads: int = 0
    evidence_reads: int = 0
    profile_work: int = 0
    materialization_reads: int = 0
    materialization_writes: int = 0
    invalidated_node_ids: set[str] = field(default_factory=set, repr=False)

    @property
    def logical_work(self) -> int:
        return (
            self.roots_seen
            + self.nodes_visited
            + self.edges_traversed
            + self.nodes_invalidated
            + self.nodes_rebuilt
            + self.nodes_retired
            + self.dependency_edges_registered
            + self.dependency_edges_removed
            + self.assertion_reads
            + self.relation_reads
            + self.evidence_reads
            + self.profile_work
            + self.materialization_reads
            + self.materialization_writes
        )

    def absorb(self, other: "DependencyTrace") -> None:
        self.roots_seen += other.roots_seen
        self.nodes_visited += other.nodes_visited
        self.edges_traversed += other.edges_traversed
        self.nodes_invalidated += other.nodes_invalidated
        self.nodes_rebuilt += other.nodes_rebuilt
        self.nodes_retired += other.nodes_retired
        self.dependency_edges_registered += other.dependency_edges_registered
        self.dependency_edges_removed += other.dependency_edges_removed
        self.assertion_reads += other.assertion_reads
        self.relation_reads += other.relation_reads
        self.evidence_reads += other.evidence_reads
        self.profile_work += other.profile_work
        self.materialization_reads += other.materialization_reads
        self.materialization_writes += other.materialization_writes
        self.invalidated_node_ids.update(other.invalidated_node_ids)


class DependencyGraph:
    """Reverse dependency graph for rebuildable derivations.

    Edges are stored as ``dependency -> dependent`` for invalidation traversal and
    mirrored as ``dependent -> dependencies`` for ordered reconstruction. Canonical
    nodes do not need lifecycle state; only registered derived nodes are marked
    invalid/fresh.
    """

    def __init__(self) -> None:
        self._dependencies: dict[str, set[str]] = {}
        self._dependents: dict[str, set[str]] = {}
        self._status: dict[str, DerivationStatus] = {}
        self._kind: dict[str, str] = {}

    def register_derived(
        self,
        node_id: str,
        kind: str,
        dependencies: set[str] | tuple[str, ...] | list[str],
        *,
        status: DerivationStatus = DerivationStatus.FRESH,
    ) -> DependencyTrace:
        trace = DependencyTrace()
        old = self._dependencies.get(node_id, set())
        new = set(dependencies)

        for dependency in old - new:
            dependents = self._dependents.get(dependency)
            if dependents is not None:
                dependents.discard(node_id)
                if not dependents:
                    self._dependents.pop(dependency, None)
            trace.dependency_edges_removed += 1

        for dependency in new - old:
            self._dependents.setdefault(dependency, set()).add(node_id)
            trace.dependency_edges_registered += 1

        self._dependencies[node_id] = new
        self._kind[node_id] = kind
        self._status[node_id] = status
        return trace

    def remove_derived(self, node_id: str) -> DependencyTrace:
        """Retire a derived leaf and account for metadata cleanup work.

        Callers must retire dependents before dependencies. Refusing to remove a
        node with live derived dependents prevents silent dangling lineage edges.
        """

        trace = DependencyTrace()
        if node_id not in self._status:
            return trace
        live_dependents = self._dependents.get(node_id, set())
        if live_dependents:
            raise ValueError(
                f"Cannot retire {node_id}; derived dependents remain: "
                f"{sorted(live_dependents)}"
            )

        dependencies = self._dependencies.pop(node_id, set())
        for dependency in dependencies:
            dependents = self._dependents.get(dependency)
            if dependents is not None:
                dependents.discard(node_id)
                if not dependents:
                    self._dependents.pop(dependency, None)
            trace.dependency_edges_removed += 1

        self._dependents.pop(node_id, None)
        self._status.pop(node_id, None)
        self._kind.pop(node_id, None)
        trace.nodes_retired = 1
        return trace

    def invalidate_from(self, root_ids: set[str] | tuple[str, ...] | list[str]) -> DependencyTrace:
        """Invalidate registered descendants of canonical or derived roots.

        Traversal is duplicate-safe and its work is proportional to the reachable
        dependency subgraph, not to the total graph cardinality. The trace carries
        the exact newly-invalidated node IDs so callers never need a whole-graph
        ``invalid_nodes()`` scan merely to identify the affected region.
        """

        roots = tuple(dict.fromkeys(root_ids))
        trace = DependencyTrace(roots_seen=len(roots))
        queue: deque[str] = deque(roots)
        seen = set(roots)

        while queue:
            current = queue.popleft()
            trace.nodes_visited += 1
            dependents = self._dependents.get(current, set())
            trace.edges_traversed += len(dependents)
            for dependent in dependents:
                if self._status.get(dependent) != DerivationStatus.INVALID:
                    self._status[dependent] = DerivationStatus.INVALID
                    trace.nodes_invalidated += 1
                    trace.invalidated_node_ids.add(dependent)
                if dependent not in seen:
                    seen.add(dependent)
                    queue.append(dependent)
        return trace

    def invalidate_nodes(self, node_ids: set[str] | tuple[str, ...] | list[str]) -> DependencyTrace:
        """Invalidate known derived nodes and all of their descendants."""

        trace = DependencyTrace()
        roots: list[str] = []
        for node_id in dict.fromkeys(node_ids):
            if node_id in self._status:
                if self._status[node_id] != DerivationStatus.INVALID:
                    self._status[node_id] = DerivationStatus.INVALID
                    trace.nodes_invalidated += 1
                    trace.invalidated_node_ids.add(node_id)
                roots.append(node_id)
        if roots:
            downstream = self.invalidate_from(roots)
            trace.absorb(downstream)
        return trace

    def dependencies_of(self, node_id: str) -> tuple[str, ...]:
        return tuple(sorted(self._dependencies.get(node_id, set())))

    def dependents_of(self, node_id: str) -> tuple[str, ...]:
        return tuple(sorted(self._dependents.get(node_id, set())))

    def status_of(self, node_id: str) -> DerivationStatus | None:
        return self._status.get(node_id)

    def kind_of(self, node_id: str) -> str | None:
        return self._kind.get(node_id)

    def mark_rebuilding(self, node_id: str) -> None:
        if node_id not in self._status:
            raise KeyError(f"Unknown derived node: {node_id}")
        self._status[node_id] = DerivationStatus.REBUILDING

    def mark_fresh(self, node_id: str) -> None:
        if node_id not in self._status:
            raise KeyError(f"Unknown derived node: {node_id}")
        self._status[node_id] = DerivationStatus.FRESH

    def invalid_nodes(self) -> tuple[str, ...]:
        return tuple(sorted(
            node_id
            for node_id, status in self._status.items()
            if status == DerivationStatus.INVALID
        ))

    def derived_nodes(self) -> tuple[str, ...]:
        return tuple(sorted(self._status))

    def edge_count(self) -> int:
        return sum(len(dependencies) for dependencies in self._dependencies.values())

    def equivalent_to(self, other: "DependencyGraph") -> bool:
        """Exact parity for derived lineage/lifecycle metadata."""

        return (
            self._dependencies == other._dependencies
            and self._dependents == other._dependents
            and self._status == other._status
            and self._kind == other._kind
        )
