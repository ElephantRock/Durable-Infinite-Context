from __future__ import annotations

import json
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from simulator.cascade import subject_id
from storage.multi_intent import CONFLICT, DONE, MultiIntentStore


ROOT = Path(__file__).resolve().parent.parent
WORKER = ROOT / "multi_intent_worker.py"


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


def _bootstrap(db: Path, entity_count: int) -> dict[str, Any]:
    out = _json_stdout(_worker(db, "bootstrap", "--entities", str(entity_count)))
    if out["settings"]["journal_mode"] != "wal":
        raise AssertionError("multi-intent experiment requires WAL")
    if int(out["settings"]["synchronous"]) != 2:
        raise AssertionError("multi-intent experiment requires synchronous=FULL")
    for field in (
        "queue_lookup_uses_index",
        "dependency_lookup_uses_index",
        "affected_traversal_uses_index",
        "clean",
    ):
        if not out[field]:
            raise AssertionError(f"bootstrap safety condition failed: {field}")
    return out


def _enqueue_args(
    operation: str,
    index: int,
    value: int,
    writer: str,
    hold_ms: int = 0,
) -> list[str]:
    return [
        "enqueue",
        "--operation",
        operation,
        "--index",
        str(index),
        "--value",
        str(value),
        "--writer",
        writer,
        "--hold-ms",
        str(hold_ms),
    ]


def run_concurrent_admission_case(
    entity_count: int = 128,
    writer_count: int = 8,
) -> dict[str, Any]:
    """Launch real processes that contend to durably enqueue disjoint writes."""

    with tempfile.TemporaryDirectory(prefix="dic-v010-concurrent-") as tmp:
        db = Path(tmp) / "memory.sqlite3"
        bootstrap = _bootstrap(db, entity_count)
        start = max(1, entity_count // 4)
        processes: list[tuple[int, int, subprocess.Popen[str]]] = []
        for offset in range(writer_count):
            index = start + offset
            value = 100 + offset
            command = [
                sys.executable,
                str(WORKER),
                "--db",
                str(db),
                *_enqueue_args(
                    "replace_assertion_object",
                    index,
                    value,
                    f"writer-{offset}",
                    hold_ms=25,
                ),
            ]
            processes.append(
                (
                    index,
                    value,
                    subprocess.Popen(
                        command,
                        cwd=ROOT,
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    ),
                )
            )

        admissions: list[dict[str, Any]] = []
        for index, value, process in processes:
            stdout, stderr = process.communicate()
            if process.returncode != 0:
                raise AssertionError(
                    f"concurrent enqueue failed index={index} rc={process.returncode} stderr={stderr!r}"
                )
            lines = [line for line in stdout.splitlines() if line.strip()]
            if not lines:
                raise AssertionError("concurrent enqueue produced no JSON")
            row = json.loads(lines[-1])
            row["index"] = index
            row["value"] = value
            admissions.append(row)

        seqs = [int(row["seq"]) for row in admissions]
        if len(seqs) != len(set(seqs)):
            raise AssertionError(f"duplicate durable sequence numbers: {seqs}")

        store = MultiIntentStore(db)
        before = store.queue_counts()
        drain = _json_stdout(_worker(db, "drain"))
        after = store.queue_counts()
        semantic = all(
            store.canonical_value(row["index"]) == row["value"] for row in admissions
        )
        clean = store.materialization_matches_clean_rebuild()
        if before["queued"] != writer_count:
            raise AssertionError(f"not all writers were durably queued: {before}")
        if after["done"] != writer_count or after["conflict"] != 0:
            raise AssertionError(f"independent writes did not all commit: {after}")
        if not semantic or not clean:
            raise AssertionError("concurrent independent admission lost semantic parity")

        return {
            "entity_count": entity_count,
            "writer_count": writer_count,
            "admitted": len(admissions),
            "unique_sequences": len(set(seqs)),
            "sequence_min": min(seqs),
            "sequence_max": max(seqs),
            "queue_before": before,
            "queue_after": after,
            "drain": drain,
            "semantic_check": semantic,
            "materialization_equal": clean,
            "queue_lookup_uses_index": bootstrap["queue_lookup_uses_index"],
        }


def run_same_key_conflict_case(entity_count: int = 64, index: int = 7) -> dict[str, Any]:
    """Two same-record intents admitted from one base must not silently overwrite."""

    with tempfile.TemporaryDirectory(prefix="dic-v010-conflict-") as tmp:
        db = Path(tmp) / "memory.sqlite3"
        _bootstrap(db, entity_count)
        first = _json_stdout(
            _worker(db, *_enqueue_args("replace_assertion_object", index, 71, "writer-a"))
        )
        second = _json_stdout(
            _worker(db, *_enqueue_args("replace_assertion_object", index, 72, "writer-b"))
        )
        if first["base_version"] != second["base_version"]:
            raise AssertionError("same-key intents were not admitted from the same base version")

        store = MultiIntentStore(db)
        first_drain = _json_stdout(_worker(db, "drain"))
        rows_after_first_drain = store.queue_snapshot()
        statuses = [row["status"] for row in rows_after_first_drain]
        if statuses != [DONE, CONFLICT]:
            raise AssertionError(f"same-key serialization did not surface conflict: {statuses}")
        if store.canonical_value(index) != 71:
            raise AssertionError("conflicting later write silently overwrote earlier committed write")

        third = _json_stdout(
            _worker(db, *_enqueue_args("replace_assertion_object", index, 73, "writer-b-retry"))
        )
        second_drain = _json_stdout(_worker(db, "drain"))
        rows_final = store.queue_snapshot()
        semantic = store.canonical_value(index) == 73
        clean = store.materialization_matches_clean_rebuild()
        if third["base_version"] <= first["base_version"]:
            raise AssertionError("retry did not observe the advanced canonical version")
        if [row["status"] for row in rows_final] != [DONE, CONFLICT, DONE]:
            raise AssertionError(f"retry lifecycle mismatch: {rows_final}")
        if not semantic or not clean:
            raise AssertionError("same-key conflict/retry did not converge to clean state")

        return {
            "entity_count": entity_count,
            "index": index,
            "first_seq": first["seq"],
            "second_seq": second["seq"],
            "first_base_version": first["base_version"],
            "second_base_version": second["base_version"],
            "retry_base_version": third["base_version"],
            "first_drain": first_drain,
            "second_drain": second_drain,
            "statuses": [row["status"] for row in rows_final],
            "final_value": store.canonical_value(index),
            "semantic_check": semantic,
            "materialization_equal": clean,
        }


def run_phase_aware_read_case(
    entity_count: int = 64,
    active_index: int = 5,
    queued_index: int = 17,
) -> dict[str, Any]:
    """Crash with two durable intents and test local stale-read blocking."""

    with tempfile.TemporaryDirectory(prefix="dic-v010-read-") as tmp:
        db = Path(tmp) / "memory.sqlite3"
        _bootstrap(db, entity_count)
        _json_stdout(
            _worker(
                db,
                *_enqueue_args("replace_assertion_object", active_index, 88, "writer-active"),
            )
        )
        _json_stdout(
            _worker(
                db,
                *_enqueue_args("replace_assertion_object", queued_index, 99, "writer-queued"),
            )
        )
        promoted = _json_stdout(_worker(db, "promote"))
        if promoted["status"] != "active":
            raise AssertionError(promoted)

        crashed = _worker(
            db,
            "crash-active",
            "--failpoint",
            "canonical_committed",
            check=False,
        )
        if crashed.returncode != -signal.SIGKILL:
            raise AssertionError(
                f"expected real SIGKILL, got rc={crashed.returncode} stderr={crashed.stderr!r}"
            )

        store = MultiIntentStore(db)
        phase = store.phase_snapshot()
        if phase["phase"] != "canonical_applied":
            raise AssertionError(f"unexpected durable crash phase: {phase}")

        affected_blocked = False
        try:
            store.read_context(subject_id(active_index))
        except RuntimeError:
            affected_blocked = True

        queued_context = store.read_context(subject_id(queued_index))
        unrelated_index = min(entity_count - 1, queued_index + 11)
        if unrelated_index in {active_index, queued_index}:
            unrelated_index = 1
        unrelated_context = store.read_context(subject_id(unrelated_index))
        counts_after_crash = store.queue_counts()

        drain = _json_stdout(_worker(db, "drain"))
        semantic = (
            store.canonical_value(active_index) == 88
            and store.canonical_value(queued_index) == 99
        )
        clean = store.materialization_matches_clean_rebuild()
        final_counts = store.queue_counts()

        if not affected_blocked:
            raise AssertionError("affected read was admitted over stale derived state")
        if queued_context is None or unrelated_context is None:
            raise AssertionError("disjoint derived read was unnecessarily blocked")
        if counts_after_crash["active"] != 1 or counts_after_crash["queued"] != 1:
            raise AssertionError(f"multi-intent crash state mismatch: {counts_after_crash}")
        if final_counts["done"] != 2 or final_counts["conflict"] != 0:
            raise AssertionError(f"queue did not drain after crash: {final_counts}")
        if not semantic or not clean:
            raise AssertionError("phase-aware recovery lost correctness")

        return {
            "entity_count": entity_count,
            "durable_phase": phase["phase"],
            "affected_read_blocked": affected_blocked,
            "queued_read_admitted": queued_context is not None,
            "unrelated_read_admitted": unrelated_context is not None,
            "queue_after_crash": counts_after_crash,
            "queue_final": final_counts,
            "drain": drain,
            "semantic_check": semantic,
            "materialization_equal": clean,
            "process_failure": "SIGKILL",
        }


def run_overlapping_derived_case(entity_count: int = 64, index: int = 9) -> dict[str, Any]:
    """Disjoint canonical keys with overlapping derived targets serialize safely."""

    with tempfile.TemporaryDirectory(prefix="dic-v010-overlap-") as tmp:
        db = Path(tmp) / "memory.sqlite3"
        _bootstrap(db, entity_count)
        evidence_intent = _json_stdout(
            _worker(db, *_enqueue_args("replace_evidence_payload", index, 77, "evidence-writer"))
        )
        assertion_intent = _json_stdout(
            _worker(db, *_enqueue_args("replace_assertion_object", index, 91, "assertion-writer"))
        )
        store = MultiIntentStore(db)
        drain = _json_stdout(_worker(db, "drain"))
        context = store.read_context(subject_id(index)) or ""
        semantic = (
            "Nova" in (store.evidence_payload(index) or "")
            and store.canonical_value(index) == 91
            and "Nova" in context
            and "91" in context
        )
        clean = store.materialization_matches_clean_rebuild()
        counts = store.queue_counts()
        if evidence_intent["write_key"] == assertion_intent["write_key"]:
            raise AssertionError("control did not use disjoint canonical write keys")
        if counts["done"] != 2 or counts["conflict"] != 0:
            raise AssertionError(f"overlapping derived intents did not serialize: {counts}")
        if not semantic or not clean:
            raise AssertionError("overlapping derived maintenance drifted")
        return {
            "entity_count": entity_count,
            "index": index,
            "distinct_write_keys": evidence_intent["write_key"] != assertion_intent["write_key"],
            "shared_read_keys": evidence_intent["read_keys"] == assertion_intent["read_keys"],
            "queue_final": counts,
            "drain": drain,
            "semantic_check": semantic,
            "materialization_equal": clean,
        }


def run_multi_intent_locality_case(entity_count: int) -> dict[str, Any]:
    """Keep three-intent recovery fixed while unrelated lifetime memory grows."""

    if entity_count < 12:
        raise ValueError("entity_count must be >= 12")
    with tempfile.TemporaryDirectory(prefix="dic-v010-locality-") as tmp:
        db = Path(tmp) / "memory.sqlite3"
        bootstrap = _bootstrap(db, entity_count)
        first_index = max(1, entity_count // 4)
        second_index = max(2, entity_count // 2)
        third_index = min(entity_count - 1, max(3, (3 * entity_count) // 4))
        if len({first_index, second_index, third_index}) != 3:
            raise AssertionError("locality indices collided")

        _json_stdout(
            _worker(db, *_enqueue_args("delete_assertion", first_index, 77, "writer-delete"))
        )
        _json_stdout(
            _worker(
                db,
                *_enqueue_args("replace_assertion_object", second_index, 101, "writer-value"),
            )
        )
        _json_stdout(
            _worker(
                db,
                *_enqueue_args("replace_evidence_payload", third_index, 77, "writer-evidence"),
            )
        )
        _json_stdout(_worker(db, "promote"))
        crashed = _worker(
            db,
            "crash-active",
            "--failpoint",
            "partial_rebuild_committed",
            check=False,
        )
        if crashed.returncode != -signal.SIGKILL:
            raise AssertionError(f"locality failpoint was not SIGKILL: {crashed.returncode}")

        store = MultiIntentStore(db)
        drain = _json_stdout(_worker(db, "drain"))
        semantic = (
            store.canonical_value(first_index) is None
            and store.read_context(subject_id(first_index)) is None
            and store.canonical_value(second_index) == 101
            and "Nova" in (store.evidence_payload(third_index) or "")
            and "Nova" in (store.read_context(subject_id(third_index)) or "")
        )
        clean = store.materialization_matches_clean_rebuild()
        counts = store.queue_counts()
        full_rebuild = store.full_rebuild_work()
        queue_work = (
            int(drain["promotions"])
            + int(drain["conflicts"])
            + int(drain["recovery_rounds"])
        )
        total_work = int(drain["trace"]["logical_work"]) + queue_work
        if counts["done"] != 3 or counts["conflict"] != 0:
            raise AssertionError(f"locality queue did not drain: {counts}")
        if not semantic or not clean:
            raise AssertionError("multi-intent locality case drifted from reconstruction")

        return {
            "entity_count": entity_count,
            "intent_count": 3,
            "base_recovery_work": int(drain["trace"]["logical_work"]),
            "queue_logical_work": queue_work,
            "total_logical_work": total_work,
            "full_rebuild_work": full_rebuild,
            "queue_final": counts,
            "semantic_check": semantic,
            "materialization_equal": clean,
            "queue_lookup_uses_index": bootstrap["queue_lookup_uses_index"],
            "affected_traversal_uses_index": bootstrap["affected_traversal_uses_index"],
        }
