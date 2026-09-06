from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from simulator.cascade import subject_id
from storage.process_store import PersistentProcessStore


ROOT = Path(__file__).resolve().parent.parent
WORKER = ROOT / "persistent_recovery_worker.py"

OPERATIONS = (
    "replace_evidence_payload",
    "replace_assertion_object",
    "delete_assertion",
)

FAILPOINTS = (
    "prepared_committed",
    "canonical_uncommitted",
    "canonical_committed",
    "invalidation_uncommitted",
    "invalidated_committed",
    "partial_rebuild_uncommitted",
    "partial_rebuild_committed",
    "repair_uncommitted",
    "repaired_committed",
    "finalize_uncommitted",
    "finalized_committed",
)

EXPECTED_DURABLE_PHASE: dict[str, str | None] = {
    "prepared_committed": "prepared",
    "canonical_uncommitted": "prepared",
    "canonical_committed": "canonical_applied",
    "invalidation_uncommitted": "canonical_applied",
    "invalidated_committed": "invalidated",
    "partial_rebuild_uncommitted": "invalidated",
    "partial_rebuild_committed": "rebuilding",
    "repair_uncommitted": "rebuilding",
    "repaired_committed": "repaired",
    "finalize_uncommitted": "repaired",
    "finalized_committed": None,
}


@dataclass(frozen=True)
class ProcessRecoveryCase:
    entity_count: int
    operation: str
    failpoint: str
    durable_phase_after_crash: str | None
    expected_durable_phase: str | None
    canonical_visible_after_crash: bool
    expected_canonical_visible: bool
    journal_rows_after_crash: int
    invalid_nodes_after_crash: int
    rebuilding_nodes_after_crash: int
    read_blocked_before_recovery: bool
    expected_read_blocked_before_recovery: bool
    recovery_trace: dict[str, int]
    materialization_equal: bool
    semantic_check: bool
    all_derived_fresh: bool
    journal_empty: bool
    journal_mode: str
    synchronous: int
    dependency_lookup_uses_index: bool
    affected_traversal_uses_index: bool
    full_rebuild_work: int

    @property
    def transaction_boundary_correct(self) -> bool:
        if self.durable_phase_after_crash != self.expected_durable_phase:
            return False
        if self.canonical_visible_after_crash != self.expected_canonical_visible:
            return False
        if self.read_blocked_before_recovery != self.expected_read_blocked_before_recovery:
            return False
        if self.failpoint in {"prepared_committed", "canonical_uncommitted", "canonical_committed", "invalidation_uncommitted"}:
            if self.invalid_nodes_after_crash != 0 or self.rebuilding_nodes_after_crash != 0:
                return False
        if self.failpoint in {"invalidated_committed", "partial_rebuild_uncommitted"}:
            if self.invalid_nodes_after_crash <= 0 or self.rebuilding_nodes_after_crash != 0:
                return False
        if self.failpoint in {"partial_rebuild_committed", "repair_uncommitted"}:
            if self.invalid_nodes_after_crash <= 0 or self.rebuilding_nodes_after_crash != 1:
                return False
        if self.failpoint in {"repaired_committed", "finalize_uncommitted", "finalized_committed"}:
            if self.invalid_nodes_after_crash != 0 or self.rebuilding_nodes_after_crash != 0:
                return False
        if self.failpoint == "finalized_committed":
            if self.journal_rows_after_crash != 0:
                return False
        elif self.journal_rows_after_crash != 1:
            return False
        return True

    @property
    def recovery_work(self) -> int:
        return int(self.recovery_trace["logical_work"])

    @property
    def work_fraction_vs_full_rebuild(self) -> float:
        if not self.full_rebuild_work:
            return 0.0
        return self.recovery_work / self.full_rebuild_work

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_count": self.entity_count,
            "operation": self.operation,
            "failpoint": self.failpoint,
            "durable_phase_after_crash": self.durable_phase_after_crash,
            "expected_durable_phase": self.expected_durable_phase,
            "canonical_visible_after_crash": self.canonical_visible_after_crash,
            "expected_canonical_visible": self.expected_canonical_visible,
            "journal_rows_after_crash": self.journal_rows_after_crash,
            "invalid_nodes_after_crash": self.invalid_nodes_after_crash,
            "rebuilding_nodes_after_crash": self.rebuilding_nodes_after_crash,
            "read_blocked_before_recovery": self.read_blocked_before_recovery,
            "expected_read_blocked_before_recovery": self.expected_read_blocked_before_recovery,
            "recovery_trace": self.recovery_trace,
            "recovery_work": self.recovery_work,
            "materialization_equal": self.materialization_equal,
            "semantic_check": self.semantic_check,
            "all_derived_fresh": self.all_derived_fresh,
            "journal_empty": self.journal_empty,
            "journal_mode": self.journal_mode,
            "synchronous": self.synchronous,
            "dependency_lookup_uses_index": self.dependency_lookup_uses_index,
            "affected_traversal_uses_index": self.affected_traversal_uses_index,
            "transaction_boundary_correct": self.transaction_boundary_correct,
            "full_rebuild_work": self.full_rebuild_work,
            "work_fraction_vs_full_rebuild": self.work_fraction_vs_full_rebuild,
        }


def _worker(db: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(WORKER), "--db", str(db), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=check,
    )


def _json_stdout(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise AssertionError(f"worker produced no JSON output; stderr={result.stderr!r}")
    return json.loads(lines[-1])


def _canonical_visible(store: PersistentProcessStore, operation: str, index: int) -> bool:
    if operation == "replace_evidence_payload":
        return "Nova" in (store.evidence_payload(index) or "")
    if operation == "replace_assertion_object":
        return store.canonical_value(index) == 77
    if operation == "delete_assertion":
        return store.canonical_value(index) is None
    raise ValueError(operation)


def _semantic_check(store: PersistentProcessStore, operation: str, index: int) -> bool:
    context = store.read_context(subject_id(index))
    if operation == "replace_evidence_payload":
        return (
            "Nova" in (store.evidence_payload(index) or "")
            and "Nova" in (context or "")
            and store.canonical_value(index) == 42
        )
    if operation == "replace_assertion_object":
        return store.canonical_value(index) == 77 and "77" in (context or "")
    if operation == "delete_assertion":
        return store.canonical_value(index) is None and context is None
    raise ValueError(operation)


def run_process_crash_case(
    entity_count: int,
    operation: str,
    failpoint: str,
    *,
    index: int | None = None,
) -> ProcessRecoveryCase:
    if operation not in OPERATIONS:
        raise ValueError(operation)
    if failpoint not in FAILPOINTS:
        raise ValueError(failpoint)
    if entity_count < 2:
        raise ValueError("entity_count must be >= 2")
    target = min(entity_count - 1, index if index is not None else max(1, entity_count // 3))

    with tempfile.TemporaryDirectory(prefix="dic-v09-") as tmp:
        db = Path(tmp) / "memory.sqlite3"
        bootstrap = _json_stdout(_worker(db, "bootstrap", "--entities", str(entity_count)))
        if not bootstrap["clean"]:
            raise AssertionError("bootstrap materialization does not match clean rebuild")
        if bootstrap["settings"]["journal_mode"] != "wal":
            raise AssertionError("v0.9 requires SQLite WAL mode")
        if not bootstrap["dependency_lookup_uses_index"]:
            raise AssertionError("dependency source lookup is not indexed")
        if not bootstrap["affected_traversal_uses_index"]:
            raise AssertionError("recursive affected-region traversal is not index-backed")

        crashed = _worker(
            db,
            "crash",
            "--operation",
            operation,
            "--index",
            str(target),
            "--failpoint",
            failpoint,
            check=False,
        )
        if crashed.returncode == 0:
            raise AssertionError(f"crash worker exited cleanly at failpoint {failpoint}")

        store = PersistentProcessStore(db)
        phase = store.phase_snapshot()
        canonical_visible = _canonical_visible(store, operation, target)
        expected_visible = failpoint not in {"prepared_committed", "canonical_uncommitted"}
        expected_read_blocked = failpoint != "finalized_committed"

        read_blocked = False
        try:
            store.read_context(subject_id(target))
        except RuntimeError:
            read_blocked = True

        recovery = _json_stdout(_worker(db, "recover"))
        inspection = _json_stdout(_worker(db, "inspect"))

        semantic = _semantic_check(store, operation, target)
        case = ProcessRecoveryCase(
            entity_count=entity_count,
            operation=operation,
            failpoint=failpoint,
            durable_phase_after_crash=phase["phase"],
            expected_durable_phase=EXPECTED_DURABLE_PHASE[failpoint],
            canonical_visible_after_crash=canonical_visible,
            expected_canonical_visible=expected_visible,
            journal_rows_after_crash=int(phase["journal_rows"]),
            invalid_nodes_after_crash=int(phase["invalid_nodes"]),
            rebuilding_nodes_after_crash=int(phase["rebuilding_nodes"]),
            read_blocked_before_recovery=read_blocked,
            expected_read_blocked_before_recovery=expected_read_blocked,
            recovery_trace={key: int(value) for key, value in recovery.items()},
            materialization_equal=bool(inspection["materialization_equal"]),
            semantic_check=semantic,
            all_derived_fresh=bool(inspection["all_derived_fresh"]),
            journal_empty=bool(inspection["journal_empty"]),
            journal_mode=str(inspection["settings"]["journal_mode"]),
            synchronous=int(inspection["settings"]["synchronous"]),
            dependency_lookup_uses_index=bool(bootstrap["dependency_lookup_uses_index"]),
            affected_traversal_uses_index=bool(bootstrap["affected_traversal_uses_index"]),
            full_rebuild_work=int(inspection["full_rebuild_work"]),
        )

        if not case.transaction_boundary_correct:
            raise AssertionError(f"transaction boundary mismatch: {case.to_dict()}")
        if not case.materialization_equal:
            raise AssertionError("persistent recovery drifted from clean reconstruction")
        if not case.semantic_check:
            raise AssertionError("persistent recovery semantic check failed")
        if not case.all_derived_fresh or not case.journal_empty:
            raise AssertionError("recovery left stale derived state or journal intent")
        return case
