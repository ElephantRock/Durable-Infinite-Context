from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Optional

from core.models import Assertion, AssertionRelation, RelationType, StateCell, StateStatus
from core.predicates import REGISTRY


def _active_at(a: Assertion, valid_time: Optional[int]) -> bool:
    if valid_time is None:
        return True
    if a.valid_from is not None and valid_time < a.valid_from:
        return False
    if a.valid_to is not None and valid_time > a.valid_to:
        return False
    return True


def _visible_by(a: Assertion, recorded_seq: Optional[int]) -> bool:
    return recorded_seq is None or a.recorded_seq <= recorded_seq


def reconcile(
    assertions: Iterable[Assertion],
    relations: Iterable[AssertionRelation],
    *,
    valid_time: Optional[int] = None,
    recorded_seq: Optional[int] = None,
) -> StateCell:
    assertions = sorted(
        [a for a in assertions if _active_at(a, valid_time) and _visible_by(a, recorded_seq)],
        key=lambda a: a.recorded_seq,
    )
    if not assertions:
        return StateCell(("", "", "default"), [], StateStatus.UNKNOWN)

    key = assertions[0].key
    schema = REGISTRY.get(key[1])
    if schema is None:
        raise KeyError(f"Unknown predicate schema: {key[1]}")

    by_id = {a.id: a for a in assertions}
    rels = [r for r in relations if r.source_assertion_id in by_id and r.target_assertion_id in by_id]

    defeated: set[str] = set()
    superseded: set[str] = set()
    relation_by_source: dict[str, list[AssertionRelation]] = defaultdict(list)
    for r in rels:
        relation_by_source[r.source_assertion_id].append(r)
        if r.relation == RelationType.CORRECTS:
            defeated.add(r.target_assertion_id)
        elif r.relation == RelationType.SUPERSEDES:
            superseded.add(r.target_assertion_id)

    candidates = [a for a in assertions if a.id not in defeated and a.id not in superseded]
    historical = sorted(defeated | superseded)

    if not candidates:
        return StateCell(key, [], StateStatus.UNKNOWN, historical_assertion_ids=historical)

    if schema.cardinality == "multi":
        values = []
        support = []
        for a in candidates:
            if a.object_value not in values:
                values.append(a.object_value)
            support.append(a.id)
        return StateCell(key, values, StateStatus.RESOLVED, support, historical_assertion_ids=historical)

    # Single-valued predicates: latest assertion is operative only if competing
    # surviving assertions do not disagree at the same logical time.
    unique_values = {repr(a.object_value): a.object_value for a in candidates}
    if len(unique_values) == 1:
        latest = candidates[-1]
        return StateCell(
            key,
            [latest.object_value],
            StateStatus.RESOLVED,
            [a.id for a in candidates],
            historical_assertion_ids=historical,
        )

    # Explicit contradiction means contested. Absent an explicit correction/
    # supersession relation, differing overlapping assertions are also contested.
    return StateCell(
        key,
        [],
        StateStatus.CONTESTED,
        [],
        competing_assertion_ids=[a.id for a in candidates],
        historical_assertion_ids=historical,
    )


def classify_relation(relations: Iterable[AssertionRelation], newer_id: str, older_id: str) -> str | None:
    for r in relations:
        if r.source_assertion_id == newer_id and r.target_assertion_id == older_id:
            if r.relation == RelationType.CORRECTS:
                return "correction"
            if r.relation == RelationType.SUPERSEDES:
                return "transition"
    return None
