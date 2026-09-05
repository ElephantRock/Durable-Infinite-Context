from dataclasses import dataclass


@dataclass(frozen=True)
class PredicateSchema:
    name: str
    cardinality: str  # single | multi
    temporal: bool = True


REGISTRY = {
    "deadline": PredicateSchema("deadline", "single", True),
    "project_status": PredicateSchema("project_status", "single", True),
    "works_at": PredicateSchema("works_at", "multi", True),
    "lives_in": PredicateSchema("lives_in", "single", True),
    "depends_on": PredicateSchema("depends_on", "multi", True),
    "approved": PredicateSchema("approved", "multi", True),
    "value": PredicateSchema("value", "single", True),
}
