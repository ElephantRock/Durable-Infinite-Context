from __future__ import annotations

from core.models import QueryCase
from core.storage import MemoryStore


def compile_context(store: MemoryStore, query: QueryCase) -> str:
    """Rule-based context compiler for v0.1 instrumentation and inspection."""
    key = (query.subject_id, query.predicate, "default")
    lines = [f"ENTITY: {query.subject_id}", f"PROPERTY: {query.predicate}"]
    cell = store.state.get(key)
    if cell:
        lines.append(f"CURRENT STATUS: {cell.status.value}")
        if cell.operative_values:
            lines.append(f"CURRENT VALUE: {cell.operative_values}")
        if cell.competing_assertion_ids:
            lines.append("COMPETING ASSERTIONS:")
            for aid in cell.competing_assertion_ids:
                a = store.assertions[aid]
                lines.append(f"- {aid}: {a.object_value!r}")
    if query.question_type in {"historical", "historical_belief", "relation_classification", "provenance"}:
        lines.append("ASSERTION HISTORY:")
        for a in store.assertions_for_key(key):
            lines.append(f"- seq={a.recorded_seq} {a.id}: {a.object_value!r}")
    if query.question_type == "provenance":
        lines.append("EVIDENCE:")
        for a in store.assertions_for_key(key):
            for eid in a.evidence_ids:
                e = store.evidence[eid]
                lines.append(f"- {eid}: {e.payload}")
    return "\n".join(lines)
