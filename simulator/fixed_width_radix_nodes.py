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

from storage.fixed_page_primary import MAX_PAYLOAD, PAGE_SIZE
from storage.fixed_width_radix_primary import (
    FIXED_NODE_USED_BYTES,
    FixedWidthRadixPrimaryStore,
    decode_fixed_radix_node,
    encode_fixed_radix_node,
)
from storage.segmented_fixed_page_primary import RADIX_FANOUT, RADIX_LEVELS

ROOT = Path(__file__).resolve().parent.parent
WORKER = ROOT / "fixed_width_radix_worker.py"
FAILPOINTS = (
    "allocated",
    "children_written",
    "parent_written",
    "dependencies_synced",
    "committed",
)
POINTER_BASES = (
    1_000,
    1_000_000,
    1_000_000_000,
    1_000_000_000_000,
    1_000_000_000_000_000,
    1_000_000_000_000_000_000,
    (1 << 64) - RADIX_FANOUT,
)
TARGET_SEGMENT_ID = (RADIX_FANOUT - 1) << (8 * (RADIX_LEVELS - 1))
DENSE_ROOT_SEGMENT_IDS = tuple(
    digit << (8 * (RADIX_LEVELS - 1)) for digit in range(RADIX_FANOUT - 1)
)
EXPECTED_FRESH_PATH_PAGES = 2 * 16 + 2 * (RADIX_LEVELS - 1)
EXPECTED_FRESH_PATH_BYTES = EXPECTED_FRESH_PATH_PAGES * PAGE_SIZE


def _json_payload_size(depth: int, entries: dict[str, int]) -> int:
    return len(
        json.dumps(
            {"depth": depth, "entries": entries},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def json_control_row(pointer_base: int) -> dict[str, Any]:
    max_entries = 0
    max_payload = 0
    first_failing_payload: int | None = None
    for count in range(RADIX_FANOUT + 1):
        entries = {str(i): pointer_base + i for i in range(count)}
        payload_bytes = _json_payload_size(RADIX_LEVELS - 1, entries)
        if payload_bytes <= MAX_PAYLOAD:
            max_entries = count
            max_payload = payload_bytes
        else:
            first_failing_payload = payload_bytes
            break
    full_entries = {str(i): pointer_base + i for i in range(RADIX_FANOUT)}
    full_payload = _json_payload_size(RADIX_LEVELS - 1, full_entries)
    return {
        "pointer_base": pointer_base,
        "max_entries_that_fit": max_entries,
        "max_fitting_payload_bytes": max_payload,
        "first_failing_payload_bytes": first_failing_payload,
        "full_fanout_payload_bytes": full_payload,
        "full_fanout_fits": full_payload <= MAX_PAYLOAD,
    }


def fixed_codec_row(pointer_base: int) -> dict[str, Any]:
    entries = {str(i): pointer_base + i for i in range(RADIX_FANOUT)}
    encoded = encode_fixed_radix_node(
        7, {"depth": RADIX_LEVELS - 1, "entries": entries}
    )
    parsed = decode_fixed_radix_node(encoded)
    if parsed is None:
        raise AssertionError("v0.31 fixed node failed to decode")
    epoch, payload = parsed
    if epoch != 7 or payload["entries"] != entries:
        raise AssertionError("v0.31 fixed node round-trip drifted")
    return {
        "pointer_base": pointer_base,
        "entry_count": len(entries),
        "page_bytes": len(encoded),
        "used_bytes": FIXED_NODE_USED_BYTES,
        "round_trip_exact": True,
    }


def run_encoding_sweep() -> dict[str, Any]:
    json_rows = [json_control_row(pointer_base) for pointer_base in POINTER_BASES]
    fixed_rows = [fixed_codec_row(pointer_base) for pointer_base in POINTER_BASES]
    if not all(row["entry_count"] == RADIX_FANOUT for row in fixed_rows):
        raise AssertionError("v0.31 fixed-width node lost semantic fanout")
    if not all(row["page_bytes"] == PAGE_SIZE for row in fixed_rows):
        raise AssertionError("v0.31 fixed-width node ceased to be one page")
    if not any(not row["full_fanout_fits"] for row in json_rows):
        raise AssertionError("v0.31 JSON control did not expose variable-width fanout loss")
    return {
        "max_json_payload_bytes": MAX_PAYLOAD,
        "radix_fanout": RADIX_FANOUT,
        "pointer_bases": list(POINTER_BASES),
        "json_control": json_rows,
        "fixed_width": fixed_rows,
        "json_min_effective_fanout": min(
            int(row["max_entries_that_fit"]) for row in json_rows
        ),
        "fixed_effective_fanout": RADIX_FANOUT,
        "fixed_node_used_bytes": FIXED_NODE_USED_BYTES,
        "fixed_node_padding_bytes": PAGE_SIZE - FIXED_NODE_USED_BYTES,
    }


def _semantic_snapshot(snapshot: dict[str, Any], target_segment_id: int) -> dict[str, Any]:
    return {
        "committed_epoch": int(snapshot["committed_epoch"]),
        "root_entry_count": int(snapshot["root_entry_count"]),
        "committed_physical_frontier_bytes": int(
            snapshot["committed_physical_frontier_bytes"]
        ),
        "target_mapping": snapshot["mappings"][str(target_segment_id)],
    }


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


def _worker(path: Path, failpoint: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(WORKER),
            "--file",
            str(path),
            "crash-mapping",
            "--segment-id",
            str(TARGET_SEGMENT_ID),
            "--failpoint",
            failpoint,
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def prepare_dense_root_fixture(path: Path) -> dict[str, Any]:
    store = FixedWidthRadixPrimaryStore(path)
    store.initialize(initial_capacity=32)
    trace = store.materialize_segment_mappings(DENSE_ROOT_SEGMENT_IDS)
    snapshot = store.mapping_snapshot([TARGET_SEGMENT_ID])
    if snapshot["root_entry_count"] != RADIX_FANOUT - 1:
        raise AssertionError("v0.31 dense-root fixture did not reach 255 entries")
    if snapshot["mappings"][str(TARGET_SEGMENT_ID)] is not None:
        raise AssertionError("v0.31 target mapping already exists in dense-root fixture")
    if trace.new_segments_allocated != RADIX_FANOUT - 1:
        raise AssertionError("v0.31 dense-root fixture allocation count drifted")
    stat = path.stat()
    return {
        "trace": trace.to_dict(),
        "snapshot": snapshot,
        "file_size_bytes": stat.st_size,
        "allocated_bytes": stat.st_blocks * 512,
    }


def run_dense_root_and_crash_matrix() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dic-v031-") as tmp:
        directory = Path(tmp)
        base_path = directory / "dense-root.pages"
        fixture = prepare_dense_root_fixture(base_path)
        base_store = FixedWidthRadixPrimaryStore(base_path)
        pre = _semantic_snapshot(
            base_store.mapping_snapshot([TARGET_SEGMENT_ID]), TARGET_SEGMENT_ID
        )

        clean_path = directory / "clean-post.pages"
        _sparse_copy(base_path, clean_path)
        clean_store = FixedWidthRadixPrimaryStore(clean_path)
        final_trace = clean_store.materialize_segment_mappings([TARGET_SEGMENT_ID])
        post_snapshot = clean_store.mapping_snapshot([TARGET_SEGMENT_ID])
        post = _semantic_snapshot(post_snapshot, TARGET_SEGMENT_ID)
        if int(post["root_entry_count"]) != RADIX_FANOUT:
            raise AssertionError("v0.31 final root did not reach full 256-way fanout")
        if post["target_mapping"] is None:
            raise AssertionError("v0.31 final mapping is not visible")
        if final_trace.new_segments_allocated != 1:
            raise AssertionError("v0.31 final insertion did not allocate exactly one segment")
        if final_trace.physical_pages_appended != EXPECTED_FRESH_PATH_PAGES:
            raise AssertionError("v0.31 final fresh path append changed")
        if final_trace.radix_node_pwrites != RADIX_LEVELS:
            raise AssertionError("v0.31 final fresh path radix writes changed")
        if final_trace.fsyncs != 2:
            raise AssertionError("v0.31 final mapping commit barrier count changed")

        rows: list[dict[str, Any]] = []
        for failpoint in FAILPOINTS:
            case_path = directory / f"case-{failpoint}.pages"
            _sparse_copy(base_path, case_path)
            result = _worker(case_path, failpoint)
            if result.returncode != -signal.SIGKILL:
                raise AssertionError(
                    f"v0.31 worker did not SIGKILL at {failpoint}: "
                    f"rc={result.returncode}, out={result.stdout!r}, err={result.stderr!r}"
                )
            crashed = FixedWidthRadixPrimaryStore(case_path)
            before_recovery = crashed.mapping_snapshot([TARGET_SEGMENT_ID])
            crash_semantic = _semantic_snapshot(before_recovery, TARGET_SEGMENT_ID)
            expected_committed = failpoint == "committed"
            expected = post if expected_committed else pre
            exact = crash_semantic == expected
            if not exact:
                raise AssertionError(
                    f"v0.31 crash state mismatch at {failpoint}: "
                    f"got={crash_semantic}, expected={expected}"
                )
            tail_before = int(before_recovery["uncommitted_tail_bytes"])
            if tail_before > EXPECTED_FRESH_PATH_BYTES:
                raise AssertionError("v0.31 crash tail exceeded one-fresh-path bound")

            recovery_one = crashed.recover()
            after_one = crashed.mapping_snapshot([TARGET_SEGMENT_ID])
            recovery_two = crashed.recover()
            after_two = crashed.mapping_snapshot([TARGET_SEGMENT_ID])
            if _semantic_snapshot(after_one, TARGET_SEGMENT_ID) != expected:
                raise AssertionError("v0.31 recovery changed committed mapping semantics")
            if _semantic_snapshot(after_two, TARGET_SEGMENT_ID) != expected:
                raise AssertionError("v0.31 second recovery changed committed mapping semantics")
            if int(after_one["uncommitted_tail_bytes"]) != 0:
                raise AssertionError("v0.31 recovery left an uncommitted tail")
            if int(recovery_two["physical_truncated_bytes"]) != 0:
                raise AssertionError("v0.31 second recovery pass was not idempotent")
            if int(recovery_one["generation_pages_scanned"]) != 0:
                raise AssertionError("v0.31 recovery scanned generation pages")
            if int(recovery_one["mapping_nodes_scanned"]) != 0:
                raise AssertionError("v0.31 recovery scanned mapping nodes")
            if int(recovery_one["logical_work"]) != 0:
                raise AssertionError("v0.31 recovery performed logical redo")

            rows.append(
                {
                    "failpoint": failpoint,
                    "expected_committed": expected_committed,
                    "exact_committed_state_match": exact,
                    "crash_epoch": crash_semantic["committed_epoch"],
                    "crash_root_entry_count": crash_semantic["root_entry_count"],
                    "target_mapping_visible": crash_semantic["target_mapping"] is not None,
                    "uncommitted_tail_before_recovery_bytes": tail_before,
                    "uncommitted_tail_after_recovery_bytes": int(
                        after_one["uncommitted_tail_bytes"]
                    ),
                    "recovery_one": recovery_one,
                    "recovery_two": recovery_two,
                }
            )
            case_path.unlink(missing_ok=True)

        return {
            "target_segment_id": TARGET_SEGMENT_ID,
            "dense_root_fixture": fixture,
            "pre_semantic_state": pre,
            "post_semantic_state": post,
            "final_mapping_trace": final_trace.to_dict(),
            "root_entry_count_before": RADIX_FANOUT - 1,
            "root_entry_count_after": RADIX_FANOUT,
            "node_splits": 0,
            "recursive_split_depth": 0,
            "expected_fresh_path_pages": EXPECTED_FRESH_PATH_PAGES,
            "expected_fresh_path_bytes": EXPECTED_FRESH_PATH_BYTES,
            "failpoints": list(FAILPOINTS),
            "case_count": len(rows),
            "rows": rows,
            "all_exact_committed_state_match": all(
                bool(row["exact_committed_state_match"]) for row in rows
            ),
            "max_uncommitted_tail_before_recovery_bytes": max(
                int(row["uncommitted_tail_before_recovery_bytes"]) for row in rows
            ),
            "max_uncommitted_tail_after_recovery_bytes": max(
                int(row["uncommitted_tail_after_recovery_bytes"]) for row in rows
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
