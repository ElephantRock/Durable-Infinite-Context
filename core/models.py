from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class StateStatus(str, Enum):
    RESOLVED = "resolved"
    CONTESTED = "contested"
    UNKNOWN = "unknown"


class RelationType(str, Enum):
    CORRECTS = "corrects"
    SUPERSEDES = "supersedes"
    CONTRADICTS = "contradicts"
    REFINES = "refines"


@dataclass(frozen=True)
class EvidenceRecord:
    id: str
    payload: str
    source_id: str
    recorded_seq: int
    source_event_time: Optional[int] = None
    scope: str = "default"
    lifecycle: str = "active"


@dataclass(frozen=True)
class Assertion:
    id: str
    subject_id: str
    predicate: str
    object_value: Any
    recorded_seq: int
    valid_from: Optional[int] = None
    valid_to: Optional[int] = None
    modality: str = "asserted"
    polarity: str = "positive"
    evidence_ids: tuple[str, ...] = field(default_factory=tuple)
    extraction_version: str = "oracle-v1"

    @property
    def key(self) -> tuple[str, str, str]:
        # scope can be added later; v0.1 keeps one scope in assertions.
        return (self.subject_id, self.predicate, "default")


@dataclass(frozen=True)
class AssertionRelation:
    source_assertion_id: str
    relation: RelationType
    target_assertion_id: str


@dataclass
class StateCell:
    key: tuple[str, str, str]
    operative_values: list[Any]
    status: StateStatus
    supporting_assertion_ids: list[str] = field(default_factory=list)
    competing_assertion_ids: list[str] = field(default_factory=list)
    historical_assertion_ids: list[str] = field(default_factory=list)
    version: int = 1


@dataclass(frozen=True)
class QueryCase:
    id: str
    scenario_type: str
    question_type: str
    subject_id: str
    predicate: str
    as_of_valid_time: Optional[int]
    as_of_recorded_seq: Optional[int]
    expected_status: StateStatus
    expected_value: Any = None
    expected_relation: Optional[str] = None
    relevant_evidence_ids: tuple[str, ...] = field(default_factory=tuple)
    question_text: Optional[str] = None


@dataclass
class Answer:
    status: StateStatus
    value: Any = None
    relation: Optional[str] = None
    evidence_ids: list[str] = field(default_factory=list)
