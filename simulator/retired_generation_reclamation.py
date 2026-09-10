from __future__ import annotations

import errno
import json
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from storage.fixed_page_primary import PAGE_MAGIC, FixedPagePrimaryStore, PAGE_SIZE
from storage.reclaiming_radix_primary import (
    SEGMENT_DATA_PAGES,
    ReclaimingRadixPrimaryStore,
)
from storage.scrubbing_reclaiming_radix_primary import (
    ScrubbingReclaimingRadixPrimaryStore,
)
from storage.segmented_fixed_page_primary import SEGMENT_BUCKET_PAGES

ROOT = Path(__file__).resolve().parent.parent
WORKER = ROOT / "reclaiming_radix_worker.py"
PREFIX = 0x0102030405060700
BUDGET = 3
BACKLOG_COUNTS = (1, 8, 32, 64)
CAPACITY_CONTROLS = (1 << 10, 1 << 20, 1 << 30, 1 << 40)
RECLAIM_FAILPOINTS = (
    "mapping_unlinked",
    "free_header_written",
    "dependencies_synced",
    "committed",
)
REUSE_FAILPOINTS = (
    "data_scrubbed",
    "mapping_written",
    "owner_header_written",
    "dependencies_synced",
    "committed",
)
STALE_MARKER = "stale-retired-payload"


def _segment_ids(count: int, *, offset: int = 0) -> tuple[int, ...]:
    if count <= 0 or offset < 0 or offset + count > 256:
        raise ValueError("fixture ids must fit one shared radix leaf")
    return tuple(PREFIX + offset + index for index in range(count))


def _semantic_snapshot(
    store: ReclaimingRadixPrimaryStore,
    segment_ids: tuple[int, ...] | list[int],
) -> dict[str, Any]:
    snapshot = store.reclamation_snapshot(segment_ids)
    return {
        "committed_epoch": int(snapshot["committed_epoch"]),
        "owner_generation": int(snapshot["owner_generation"]),
        "owner_count": int(snapshot["owner_count"]),
        "retire_generation": snapshot["retire_generation"],
        "retire_remaining": int(snapshot["retire_remaining"]),
        "free_count": int(snapshot["free_count"]),
        "heads": snapshot["heads"],
        "mappings": snapshot["mappings"],
        "committed_physical_frontier_bytes": int(
            snapshot["committed_physical_frontier_bytes"]
        ),
    }


def _seed_stale_payload(
    store: ReclaimingRadixPrimaryStore,
    segment_id: int,
) -> int:
    snapshot = store.reclamation_snapshot([segment_id])
    segment_base = snapshot["mappings"][str(segment_id)]
    if segment_base is None:
        raise AssertionError("cannot seed stale payload into an unmapped segment")
    fd = store._open()
    try:
        record = store._pack_record(
            PAGE_MAGIC,
            int(snapshot["committed_epoch"]),
            {"keys": [STALE_MARKER, None, None, None]},
        )
        written = os.pwrite(
            fd,
            record,
            store._physical_data_offset(int(segment_base), 0, 0),
        )
        if written != len(record):
            raise IOError("short stale-payload fixture write")
        os.fsync(fd)
    finally:
        os.close(fd)
    return int(segment_base)


def _read_first_logical_page(
    store: ReclaimingRadixPrimaryStore,
    segment_id: int,
) -> list[str | None]:
    store._reset_operation_state()
    fd = store._open()
    try:
        epoch, _meta, _slot = FixedPagePrimaryStore._read_super(store, fd)
        keys, _reads = store._read_logical_page(
            fd,
            int(segment_id) * SEGMENT_BUCKET_PAGES,
            epoch,
            4,
        )
        return keys
    finally:
        store._active_counters = None
        os.close(fd)


def _sparse_copy(source: Path, destination: Path) -> None:
    source_fd = os.open(source, os.O_RDONLY)
    destination_fd = os.open(
        destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
    )
    try:
        size = os.fstat(source_fd).st_size
        os.ftruncate(destination_fd, size)
        if not hasattr(os, "SEEK_DATA") or not hasattr(os, "SEEK_HOLE"):
            raise OSError(errno.EINVAL, "sparse seek unsupported")
        position = 0
        while position < size:
            try:
                data_start = os.lseek(source_fd, position, os.SEEK_DATA)
            except OSError as exc:
                if exc.errno == errno.ENXIO:
                    break
                raise
            hole_start = os.lseek(source_fd, data_start, os.SEEK_HOLE)
            remaining = hole_start - data_start
            os.lseek(source_fd, data_start, os.SEEK_SET)
            os.lseek(destination_fd, data_start, os.SEEK_SET)
            while remaining:
                chunk = os.read(source_fd, min(1 << 20, remaining))
                if not chunk:
                    raise IOError("unexpected EOF during sparse fixture copy")
                view = memoryview(chunk)
                while view:
                    written = os.write(destination_fd, view)
                    view = view[written:]
                remaining -= len(chunk)
            position = hole_start
        os.fsync(destination_fd)
    except OSError:
        os.close(destination_fd)
        os.close(source_fd)
        destination.unlink(missing_ok=True)
        import shutil

        shutil.copyfile(source, destination)
        return
    finally:
        try:
            os.close(destination_fd)
        except OSError:
            pass
        try:
            os.close(source_fd)
        except OSError:
            pass


def _worker(
    path: Path,
    command: str,
    *,
    failpoint: str,
    budget: int | None = None,
    segment_id: int | None = None,
) -> subprocess.CompletedProcess[str]:
    argv = [sys.executable, str(WORKER), "--file", str(path), command]
    if budget is not None:
        argv.extend(["--budget", str(budget)])
    if segment_id is not None:
        argv.extend(["--segment-id", str(segment_id)])
    argv.extend(["--failpoint", failpoint])
    return subprocess.run(
        argv,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def run_manifest_and_capacity_controls() -> dict[str, Any]:
    manifest_rows: list[dict[str, Any]] = []
    for count in BACKLOG_COUNTS:
        ids = _segment_ids(count)
        encoded = json.dumps(
            {"retired_segments": list(ids)},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        manifest_rows.append(
            {
                "retired_segments": count,
                "flat_manifest_bytes": len(encoded),
            }
        )
    if not all(
        manifest_rows[index]["flat_manifest_bytes"]
        < manifest_rows[index + 1]["flat_manifest_bytes"]
        for index in range(len(manifest_rows) - 1)
    ):
        raise AssertionError("flat manifest control did not grow with retirement backlog")

    capacity_rows = [
        {
            "logical_generation_capacity": capacity,
            "naive_capacity_walk_slots": capacity,
            "candidate_reclaim_visits_per_step": BUDGET,
        }
        for capacity in CAPACITY_CONTROLS
    ]
    return {
        "manifest_rows": manifest_rows,
        "capacity_rows": capacity_rows,
        "candidate_superblock_retirement_fields": [
            "retire_generation",
            "retire_cursor_page",
            "retire_remaining",
            "free_head_page",
            "free_count",
        ],
    }


def run_budget_sweep() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="dic-v032-budget-") as tmp:
        directory = Path(tmp)
        for count in BACKLOG_COUNTS:
            path = directory / f"backlog-{count}.pages"
            ids = _segment_ids(count)
            store = ScrubbingReclaimingRadixPrimaryStore(path)
            store.initialize(initial_capacity=32)
            materialize = store.materialize_owned_segment_mappings(ids)
            retirement = store.retire_owner_generation(new_generation=1)
            if retirement.retired_segments != count:
                raise AssertionError("retirement count drifted")

            step_rows: list[dict[str, Any]] = []
            while True:
                before = store.reclamation_snapshot(ids)
                if int(before["retire_remaining"]) == 0:
                    break
                trace = store.reclaim_step(budget=BUDGET)
                expected = min(BUDGET, int(before["retire_remaining"]))
                if trace.reclaimed_segments != expected:
                    raise AssertionError("reclaim step exceeded or underused fixed budget")
                if trace.physical_pages_appended != 0:
                    raise AssertionError("reclaim step appended physical pages")
                if trace.lifecycle_header_preads != 4 * trace.reclaimed_segments:
                    raise AssertionError("lifecycle read work is not fixed per reclaimed segment")
                if trace.lifecycle_header_pwrites != trace.reclaimed_segments:
                    raise AssertionError("lifecycle write work is not fixed per reclaimed segment")
                if trace.radix_node_pwrites > trace.reclaimed_segments:
                    raise AssertionError("radix rewrite work exceeded one leaf rewrite per segment")
                if trace.fsyncs != 2:
                    raise AssertionError("reclaim publication barrier count drifted")
                if trace.generation_pages_scanned or trace.mapping_nodes_scanned or trace.logical_redo:
                    raise AssertionError("reclaim introduced scan/redo work")
                step_rows.append(trace.to_dict())

            final = store.reclamation_snapshot(ids)
            if int(final["retire_remaining"]) != 0 or int(final["free_count"]) != count:
                raise AssertionError("reclaim sweep did not drain retirement backlog")
            if any(value is not None for value in final["mappings"].values()):
                raise AssertionError("reclaim sweep left retired mappings reachable")

            rows.append(
                {
                    "retired_segments": count,
                    "materialize": materialize.to_dict(),
                    "retirement": retirement.to_dict(),
                    "steps": step_rows,
                    "step_count": len(step_rows),
                    "max_reclaimed_per_step": max(
                        int(step["reclaimed_segments"]) for step in step_rows
                    ),
                    "max_lifecycle_preads_per_step": max(
                        int(step["lifecycle_header_preads"]) for step in step_rows
                    ),
                    "max_lifecycle_pwrites_per_step": max(
                        int(step["lifecycle_header_pwrites"]) for step in step_rows
                    ),
                    "max_radix_pwrites_per_step": max(
                        int(step["radix_node_pwrites"]) for step in step_rows
                    ),
                    "max_physical_pages_appended_per_step": max(
                        int(step["physical_pages_appended"]) for step in step_rows
                    ),
                    "final_free_count": int(final["free_count"]),
                }
            )
            path.unlink(missing_ok=True)
    return {
        "budget": BUDGET,
        "backlog_counts": list(BACKLOG_COUNTS),
        "rows": rows,
        "global_max_reclaimed_per_step": max(row["max_reclaimed_per_step"] for row in rows),
        "global_max_lifecycle_preads_per_step": max(
            row["max_lifecycle_preads_per_step"] for row in rows
        ),
        "global_max_lifecycle_pwrites_per_step": max(
            row["max_lifecycle_pwrites_per_step"] for row in rows
        ),
        "global_max_radix_pwrites_per_step": max(
            row["max_radix_pwrites_per_step"] for row in rows
        ),
        "global_max_physical_pages_appended_per_step": max(
            row["max_physical_pages_appended_per_step"] for row in rows
        ),
    }


def _prepare_one_free_extent(
    path: Path,
    store_cls: type[ReclaimingRadixPrimaryStore],
    *,
    old_id: int,
) -> tuple[ReclaimingRadixPrimaryStore, int]:
    store = store_cls(path)
    store.initialize(initial_capacity=32)
    store.materialize_owned_segment_mappings([old_id])
    old_base = _seed_stale_payload(store, old_id)
    store.retire_owner_generation(new_generation=1)
    reclaim = store.reclaim_step(budget=1)
    if reclaim.reclaimed_segments != 1 or reclaim.free_count != 1:
        raise AssertionError("failed to prepare one reusable free extent")
    return store, old_base


def run_stale_payload_control() -> dict[str, Any]:
    old_id = PREFIX + 100
    new_id = PREFIX + 101
    with tempfile.TemporaryDirectory(prefix="dic-v032-stale-") as tmp:
        directory = Path(tmp)

        unsafe_path = directory / "unsafe.pages"
        unsafe, unsafe_old_base = _prepare_one_free_extent(
            unsafe_path,
            ReclaimingRadixPrimaryStore,
            old_id=old_id,
        )
        unsafe_reuse = unsafe.reuse_one_mapping(new_id)
        unsafe_keys = _read_first_logical_page(unsafe, new_id)
        unsafe_exposed = bool(unsafe_keys and unsafe_keys[0] == STALE_MARKER)
        if not unsafe_exposed:
            raise AssertionError("unscrubbed reuse control did not expose seeded stale payload")

        safe_path = directory / "safe.pages"
        safe, safe_old_base = _prepare_one_free_extent(
            safe_path,
            ScrubbingReclaimingRadixPrimaryStore,
            old_id=old_id,
        )
        safe_reuse = safe.reuse_one_mapping(new_id)
        safe_keys = _read_first_logical_page(safe, new_id)
        safe_exposed = bool(safe_keys and safe_keys[0] == STALE_MARKER)
        if safe_exposed or any(key is not None for key in safe_keys):
            raise AssertionError("scrubbed reuse exposed retired payload")
        if safe_reuse.data_page_scrub_pwrites != SEGMENT_DATA_PAGES:
            raise AssertionError("safe reuse did not scrub the fixed data footprint")
        if safe_reuse.physical_pages_appended != 0:
            raise AssertionError("shared-path reuse unexpectedly appended metadata pages")
        if safe_reuse.radix_node_pwrites != 1:
            raise AssertionError("shared-path reuse exceeded one leaf rewrite")
        if safe_old_base != safe_reuse.reused_segment_base_page:
            raise AssertionError("safe reuse did not reuse the reclaimed data extent")

        safe_snapshot = safe.reclamation_snapshot([old_id, new_id])
        if safe_snapshot["mappings"][str(old_id)] is not None:
            raise AssertionError("retired mapping resurrected during reuse")
        if int(safe_snapshot["mappings"][str(new_id)]) != safe_old_base:
            raise AssertionError("new mapping does not point at reclaimed extent")

        return {
            "old_segment_id": old_id,
            "new_segment_id": new_id,
            "unsafe_reused_segment_base_page": unsafe_reuse.reused_segment_base_page,
            "unsafe_old_segment_base_page": unsafe_old_base,
            "unsafe_stale_payload_visible": unsafe_exposed,
            "safe_reused_segment_base_page": safe_reuse.reused_segment_base_page,
            "safe_old_segment_base_page": safe_old_base,
            "safe_stale_payload_visible": safe_exposed,
            "safe_reuse_trace": safe_reuse.to_dict(),
            "safe_new_page_keys": safe_keys,
            "safe_old_mapping_visible": safe_snapshot["mappings"][str(old_id)] is not None,
        }


def run_reclaim_crash_matrix() -> dict[str, Any]:
    ids = _segment_ids(4, offset=120)
    with tempfile.TemporaryDirectory(prefix="dic-v032-reclaim-crash-") as tmp:
        directory = Path(tmp)
        base_path = directory / "base.pages"
        base = ScrubbingReclaimingRadixPrimaryStore(base_path)
        base.initialize(initial_capacity=32)
        base.materialize_owned_segment_mappings(ids)
        base.retire_owner_generation(new_generation=1)
        pre = _semantic_snapshot(base, ids)

        clean_path = directory / "clean.pages"
        _sparse_copy(base_path, clean_path)
        clean = ScrubbingReclaimingRadixPrimaryStore(clean_path)
        clean_trace = clean.reclaim_step(budget=2)
        post = _semantic_snapshot(clean, ids)
        if clean_trace.reclaimed_segments != 2:
            raise AssertionError("clean reclaim fixture did not reclaim exactly two segments")

        rows: list[dict[str, Any]] = []
        for failpoint in RECLAIM_FAILPOINTS:
            case_path = directory / f"case-{failpoint}.pages"
            _sparse_copy(base_path, case_path)
            result = _worker(
                case_path,
                "crash-reclaim",
                failpoint=failpoint,
                budget=2,
            )
            if result.returncode != -signal.SIGKILL:
                raise AssertionError(
                    f"reclaim worker did not SIGKILL at {failpoint}: "
                    f"rc={result.returncode}, out={result.stdout!r}, err={result.stderr!r}"
                )
            crashed = ScrubbingReclaimingRadixPrimaryStore(case_path)
            before_recovery = _semantic_snapshot(crashed, ids)
            expected_committed = failpoint == "committed"
            expected = post if expected_committed else pre
            exact = before_recovery == expected
            if not exact:
                raise AssertionError(
                    f"reclaim crash state mismatch at {failpoint}: "
                    f"got={before_recovery}, expected={expected}"
                )
            recovery_one = crashed.recover()
            recovery_two = crashed.recover()
            after_recovery = _semantic_snapshot(crashed, ids)
            if after_recovery != expected:
                raise AssertionError("reclaim recovery changed committed lifecycle semantics")
            if recovery_one["generation_pages_scanned"] or recovery_one["mapping_nodes_scanned"]:
                raise AssertionError("reclaim recovery introduced a scan")
            if recovery_one["logical_work"]:
                raise AssertionError("reclaim recovery performed logical redo")
            if recovery_two["physical_truncated_bytes"]:
                raise AssertionError("second reclaim recovery was not idempotent")
            rows.append(
                {
                    "failpoint": failpoint,
                    "expected_committed": expected_committed,
                    "exact_committed_state_match": exact,
                    "recovery_one": recovery_one,
                    "recovery_two": recovery_two,
                }
            )
            case_path.unlink(missing_ok=True)

        return {
            "segment_ids": list(ids),
            "budget": 2,
            "failpoints": list(RECLAIM_FAILPOINTS),
            "clean_trace": clean_trace.to_dict(),
            "pre_semantic_state": pre,
            "post_semantic_state": post,
            "rows": rows,
            "all_exact_committed_state_match": all(
                bool(row["exact_committed_state_match"]) for row in rows
            ),
            "all_recovery_scan_free": all(
                int(row["recovery_one"]["generation_pages_scanned"]) == 0
                and int(row["recovery_one"]["mapping_nodes_scanned"]) == 0
                and int(row["recovery_one"]["logical_work"]) == 0
                for row in rows
            ),
            "all_second_recovery_idempotent": all(
                int(row["recovery_two"]["physical_truncated_bytes"]) == 0
                for row in rows
            ),
        }


def run_reuse_crash_matrix() -> dict[str, Any]:
    ids = _segment_ids(2, offset=140)
    retired_id = ids[-1]
    new_id = PREFIX + 200
    observed_ids = (*ids, new_id)
    with tempfile.TemporaryDirectory(prefix="dic-v032-reuse-crash-") as tmp:
        directory = Path(tmp)
        base_path = directory / "base.pages"
        base = ScrubbingReclaimingRadixPrimaryStore(base_path)
        base.initialize(initial_capacity=32)
        base.materialize_owned_segment_mappings(ids)
        retired_base = _seed_stale_payload(base, retired_id)
        base.retire_owner_generation(new_generation=1)
        first_reclaim = base.reclaim_step(budget=1)
        if first_reclaim.reclaimed_segments != 1:
            raise AssertionError("reuse fixture did not create one free extent")
        pre = _semantic_snapshot(base, observed_ids)

        clean_path = directory / "clean.pages"
        _sparse_copy(base_path, clean_path)
        clean = ScrubbingReclaimingRadixPrimaryStore(clean_path)
        clean_trace = clean.reuse_one_mapping(new_id)
        post = _semantic_snapshot(clean, observed_ids)
        clean_keys = _read_first_logical_page(clean, new_id)
        if any(key is not None for key in clean_keys):
            raise AssertionError("clean reuse exposed stale payload")
        if clean_trace.reused_segment_base_page != retired_base:
            raise AssertionError("clean reuse did not reuse retired physical segment")
        if clean_trace.physical_pages_appended != 0:
            raise AssertionError("clean shared-path reuse appended pages")
        if clean_trace.data_page_scrub_pwrites != SEGMENT_DATA_PAGES:
            raise AssertionError("clean reuse scrub count drifted")

        rows: list[dict[str, Any]] = []
        for failpoint in REUSE_FAILPOINTS:
            case_path = directory / f"case-{failpoint}.pages"
            _sparse_copy(base_path, case_path)
            result = _worker(
                case_path,
                "crash-reuse",
                failpoint=failpoint,
                segment_id=new_id,
            )
            if result.returncode != -signal.SIGKILL:
                raise AssertionError(
                    f"reuse worker did not SIGKILL at {failpoint}: "
                    f"rc={result.returncode}, out={result.stdout!r}, err={result.stderr!r}"
                )
            crashed = ScrubbingReclaimingRadixPrimaryStore(case_path)
            before_recovery = _semantic_snapshot(crashed, observed_ids)
            expected_committed = failpoint == "committed"
            expected = post if expected_committed else pre
            exact = before_recovery == expected
            if not exact:
                raise AssertionError(
                    f"reuse crash state mismatch at {failpoint}: "
                    f"got={before_recovery}, expected={expected}"
                )
            recovery_one = crashed.recover()
            recovery_two = crashed.recover()
            after_recovery = _semantic_snapshot(crashed, observed_ids)
            if after_recovery != expected:
                raise AssertionError("reuse recovery changed committed lifecycle semantics")
            if recovery_one["generation_pages_scanned"] or recovery_one["mapping_nodes_scanned"]:
                raise AssertionError("reuse recovery introduced a scan")
            if recovery_one["logical_work"]:
                raise AssertionError("reuse recovery performed logical redo")
            if recovery_two["physical_truncated_bytes"]:
                raise AssertionError("second reuse recovery was not idempotent")

            retry_reused = expected_committed
            if not expected_committed:
                retry_trace = crashed.reuse_one_mapping(new_id)
                retry_keys = _read_first_logical_page(crashed, new_id)
                if any(key is not None for key in retry_keys):
                    raise AssertionError("retry after pre-commit crash exposed stale payload")
                if retry_trace.reused_segment_base_page != retired_base:
                    raise AssertionError("retry did not preserve reclaimed extent identity")
                retry_reused = True

            rows.append(
                {
                    "failpoint": failpoint,
                    "expected_committed": expected_committed,
                    "exact_committed_state_match": exact,
                    "recovery_one": recovery_one,
                    "recovery_two": recovery_two,
                    "retry_or_committed_reuse_exact": retry_reused,
                }
            )
            case_path.unlink(missing_ok=True)

        return {
            "retired_segment_id": retired_id,
            "new_segment_id": new_id,
            "retired_segment_base_page": retired_base,
            "failpoints": list(REUSE_FAILPOINTS),
            "clean_reuse_trace": clean_trace.to_dict(),
            "clean_new_page_keys": clean_keys,
            "pre_semantic_state": pre,
            "post_semantic_state": post,
            "rows": rows,
            "all_exact_committed_state_match": all(
                bool(row["exact_committed_state_match"]) for row in rows
            ),
            "all_recovery_scan_free": all(
                int(row["recovery_one"]["generation_pages_scanned"]) == 0
                and int(row["recovery_one"]["mapping_nodes_scanned"]) == 0
                and int(row["recovery_one"]["logical_work"]) == 0
                for row in rows
            ),
            "all_second_recovery_idempotent": all(
                int(row["recovery_two"]["physical_truncated_bytes"]) == 0
                for row in rows
            ),
            "all_retry_or_committed_reuse_exact": all(
                bool(row["retry_or_committed_reuse_exact"]) for row in rows
            ),
        }
