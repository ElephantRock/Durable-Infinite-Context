from __future__ import annotations

import json
import signal
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from simulator.cross_store_hybrid import _physical_tail_bytes, _worker as admission_worker, prepare_scenario
from storage.recovery_interruption import InterruptibleCrossStoreHybridStore

ROOT = Path(__file__).resolve().parent.parent
RECOVERY_WORKER = ROOT / "recovery_interruption_worker.py"

RECOVERY_INTERRUPTION_CASES = {
    "future_overflow": ("future_delete_uncommitted", "future_delete_committed"),
    "fixed_tail": ("tail_truncated", "tail_synced"),
    "combined_control": (
        "future_delete_uncommitted",
        "future_delete_committed",
        "tail_truncated",
        "tail_synced",
    ),
}


@dataclass(frozen=True)
class RecoveryInterruptionCase:
    residue_kind: str
    failpoint: str
    synthetic_combined_control: bool
    pre_future_rows: int
    pre_tail_bytes: int
    interrupted_snapshot_exact: bool
    existing_keys_found_after_interrupt: bool
    abandoned_visible_after_interrupt: bool
    future_rows_after_interrupt: int
    tail_bytes_after_interrupt: int
    retry_one: dict[str, Any]
    retry_two: dict[str, Any]
    converged_to_exact_snapshot: bool
    final_future_rows: int
    final_tail_bytes: int
    final_audit_valid: bool
    resurrection_checked: bool
    abandoned_visible_after_epoch_advance: bool | None
    replacement_visible_after_epoch_advance: bool | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _recovery_worker(
    primary: Path,
    overflow: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(RECOVERY_WORKER),
            "--primary",
            str(primary),
            "--overflow",
            str(overflow),
            *args,
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=check,
    )


def _json_stdout(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise AssertionError(f"recovery worker produced no JSON: {result.stderr!r}")
    return json.loads(lines[-1])


def _prepare_future_residue(primary: Path, overflow: Path) -> tuple[list[str], str, str]:
    _store, existing, abandoned = prepare_scenario(primary, overflow, "overflow_admission")
    crashed = admission_worker(
        primary,
        overflow,
        "crash",
        "--key",
        abandoned,
        "--failpoint",
        "overflow_committed",
        check=False,
    )
    if crashed.returncode != -signal.SIGKILL:
        raise AssertionError("future-residue fixture did not SIGKILL")
    reopened = InterruptibleCrossStoreHybridStore(primary, overflow)
    audit = reopened.audit()
    if int(audit["future_overflow_rows"]) != 1 or reopened.lookup(abandoned).found:
        raise AssertionError("future-residue fixture did not leave one hidden future row")
    if _physical_tail_bytes(reopened) != 0:
        raise AssertionError("future-residue fixture unexpectedly retained fixed tail")
    return existing, abandoned, "collision_000017"


def _prepare_tail_residue(primary: Path, overflow: Path) -> tuple[list[str], str, str]:
    _store, existing, target = prepare_scenario(primary, overflow, "migration_start")
    crashed = admission_worker(
        primary,
        overflow,
        "crash",
        "--key",
        target,
        "--failpoint",
        "primary_data_synced",
        check=False,
    )
    if crashed.returncode != -signal.SIGKILL:
        raise AssertionError("tail-residue fixture did not SIGKILL")
    reopened = InterruptibleCrossStoreHybridStore(primary, overflow)
    audit = reopened.audit()
    if int(audit["future_overflow_rows"]) != 0:
        raise AssertionError("tail-residue fixture unexpectedly retained future overflow")
    if _physical_tail_bytes(reopened) <= 0 or reopened.lookup(target).found:
        raise AssertionError("tail-residue fixture did not retain stale precommit tail")
    return existing, target, target


def _prepare_combined_residue(primary: Path, overflow: Path) -> tuple[list[str], str, str]:
    """Synthetic control combining two real v0.25 residue classes.

    The stale tail is produced by the real migration-start crash. A durable future row is
    then inserted directly at coordinator epoch+1 only to exercise ordered recovery with
    both residue classes present simultaneously. This is not claimed as one natural
    admission crash image.
    """
    existing, _target, replacement = _prepare_tail_residue(primary, overflow)
    reopened = InterruptibleCrossStoreHybridStore(primary, overflow)
    epoch, _meta = reopened._read_epoch_meta()
    abandoned = "synthetic_future_cleanup_control"
    conn = sqlite3.connect(overflow)
    try:
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO overflow(key, epoch) VALUES (?, ?)",
            (abandoned, int(epoch) + 1),
        )
        conn.commit()
    finally:
        conn.close()
    audit = reopened.audit()
    if int(audit["future_overflow_rows"]) != 1:
        raise AssertionError("combined control failed to create one future row")
    if _physical_tail_bytes(reopened) <= 0:
        raise AssertionError("combined control lost stale fixed tail")
    if reopened.lookup(abandoned).found:
        raise AssertionError("combined future row became visible before coordinator advance")
    return existing, abandoned, replacement


def _prepare_residue(
    primary: Path,
    overflow: Path,
    residue_kind: str,
) -> tuple[list[str], str, str]:
    if residue_kind == "future_overflow":
        return _prepare_future_residue(primary, overflow)
    if residue_kind == "fixed_tail":
        return _prepare_tail_residue(primary, overflow)
    if residue_kind == "combined_control":
        return _prepare_combined_residue(primary, overflow)
    raise ValueError(residue_kind)


def run_recovery_interruption_case(
    residue_kind: str,
    failpoint: str,
) -> RecoveryInterruptionCase:
    if residue_kind not in RECOVERY_INTERRUPTION_CASES:
        raise ValueError(residue_kind)
    if failpoint not in RECOVERY_INTERRUPTION_CASES[residue_kind]:
        raise ValueError(failpoint)

    with tempfile.TemporaryDirectory(prefix="dic-v026-recovery-") as tmp:
        root = Path(tmp)
        primary = root / "primary.pages"
        overflow = root / "overflow.sqlite"
        existing, abandoned, replacement = _prepare_residue(primary, overflow, residue_kind)

        store = InterruptibleCrossStoreHybridStore(primary, overflow)
        expected_snapshot = store.logical_snapshot()
        pre_audit = store.audit()
        pre_future = int(pre_audit["future_overflow_rows"])
        pre_tail = _physical_tail_bytes(store)
        if not pre_audit["valid"]:
            raise AssertionError("pre-recovery residue failed committed-state audit")

        crashed = _recovery_worker(
            primary,
            overflow,
            "recover-crash",
            "--failpoint",
            failpoint,
            check=False,
        )
        if crashed.returncode != -signal.SIGKILL:
            raise AssertionError(
                f"recovery did not SIGKILL at {residue_kind}/{failpoint}: "
                f"returncode={crashed.returncode}, stderr={crashed.stderr!r}"
            )

        interrupted = InterruptibleCrossStoreHybridStore(primary, overflow)
        interrupted_snapshot = interrupted.logical_snapshot()
        interrupted_audit = interrupted.audit()
        existing_found = all(interrupted.lookup(key).found for key in existing)
        abandoned_visible = interrupted.lookup(abandoned).found
        future_after_interrupt = int(interrupted_audit["future_overflow_rows"])
        tail_after_interrupt = _physical_tail_bytes(interrupted)

        if interrupted_snapshot != expected_snapshot:
            raise AssertionError("interrupted recovery changed committed logical snapshot")
        if not interrupted_audit["valid"] or not existing_found or abandoned_visible:
            raise AssertionError("interrupted recovery lost, duplicated, or exposed membership")

        if failpoint == "future_delete_uncommitted":
            if future_after_interrupt != 1:
                raise AssertionError("uncommitted future-row delete did not roll back after SIGKILL")
            if residue_kind == "combined_control" and tail_after_interrupt != pre_tail:
                raise AssertionError("early future-row interruption unexpectedly changed fixed tail")
        elif failpoint == "future_delete_committed":
            if future_after_interrupt != 0:
                raise AssertionError("committed future-row delete was not durable across SIGKILL")
            if residue_kind == "combined_control" and tail_after_interrupt != pre_tail:
                raise AssertionError("post-delete interruption unexpectedly changed fixed tail")
        elif failpoint in {"tail_truncated", "tail_synced"}:
            if tail_after_interrupt != 0:
                raise AssertionError("process-crash model did not retain completed truncate size")
            if residue_kind == "combined_control" and future_after_interrupt != 0:
                raise AssertionError("combined recovery reached tail cleanup before future cleanup committed")

        retry_one = _json_stdout(_recovery_worker(primary, overflow, "recover"))
        retry_two = _json_stdout(_recovery_worker(primary, overflow, "recover"))

        final_store = InterruptibleCrossStoreHybridStore(primary, overflow)
        final_snapshot = final_store.logical_snapshot()
        final_audit = final_store.audit()
        final_future = int(final_audit["future_overflow_rows"])
        final_tail = _physical_tail_bytes(final_store)
        if final_snapshot != expected_snapshot:
            raise AssertionError("restarted recovery changed committed logical snapshot")
        if final_future != 0 or final_tail != 0 or not final_audit["valid"]:
            raise AssertionError("restarted recovery did not converge to residue-free state")
        if int(retry_one["logical_redo"]) != 0 or int(retry_two["logical_redo"]) != 0:
            raise AssertionError("recovery interruption required application logical redo")
        if int(retry_two["deleted_future_overflow_rows"]) != 0:
            raise AssertionError("second post-interruption recovery still deleted future rows")
        if int(retry_two["reclaimed_tail_bytes"]) != 0:
            raise AssertionError("second post-interruption recovery still reclaimed fixed tail")

        resurrection_checked = residue_kind in {"future_overflow", "combined_control"}
        abandoned_after_epoch: bool | None = None
        replacement_visible: bool | None = None
        if resurrection_checked:
            advancing = InterruptibleCrossStoreHybridStore(primary, overflow)
            trace = advancing.insert(replacement)
            if trace.duplicate:
                raise AssertionError("epoch-advance control unexpectedly inserted duplicate")
            abandoned_after_epoch = bool(advancing.lookup(abandoned).found)
            replacement_visible = bool(advancing.lookup(replacement).found)
            if abandoned_after_epoch or not replacement_visible:
                raise AssertionError("abandoned future row resurrected after later epoch advance")

        return RecoveryInterruptionCase(
            residue_kind=residue_kind,
            failpoint=failpoint,
            synthetic_combined_control=residue_kind == "combined_control",
            pre_future_rows=pre_future,
            pre_tail_bytes=pre_tail,
            interrupted_snapshot_exact=True,
            existing_keys_found_after_interrupt=existing_found,
            abandoned_visible_after_interrupt=abandoned_visible,
            future_rows_after_interrupt=future_after_interrupt,
            tail_bytes_after_interrupt=tail_after_interrupt,
            retry_one=retry_one,
            retry_two=retry_two,
            converged_to_exact_snapshot=True,
            final_future_rows=final_future,
            final_tail_bytes=final_tail,
            final_audit_valid=bool(final_audit["valid"]),
            resurrection_checked=resurrection_checked,
            abandoned_visible_after_epoch_advance=abandoned_after_epoch,
            replacement_visible_after_epoch_advance=replacement_visible,
        )


def run_recovery_interruption_matrix() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for residue_kind, failpoints in RECOVERY_INTERRUPTION_CASES.items():
        for failpoint in failpoints:
            row = run_recovery_interruption_case(residue_kind, failpoint).to_dict()
            rows.append(row)
            print(
                "RECOVERY_INTERRUPTION",
                residue_kind,
                failpoint,
                {
                    "future_after_interrupt": row["future_rows_after_interrupt"],
                    "tail_after_interrupt": row["tail_bytes_after_interrupt"],
                    "converged": row["converged_to_exact_snapshot"],
                },
            )
    return {
        "rows": rows,
        "cases": len(rows),
        "natural_cases": sum(1 for row in rows if not row["synthetic_combined_control"]),
        "synthetic_combined_cases": sum(1 for row in rows if row["synthetic_combined_control"]),
    }
