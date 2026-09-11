from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import run_fixed_page_primary_experiment


ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "fixed_page_primary_results.json"
EXPECTED_RESULT_SHA256 = "acb136b0da7dac9665bdc861492ab5366363d86404536a9211d0418057183812"
EXPECTED_ARTIFACT_SHA256 = "97710fc5df92cd44326d4e8f19a3ff0df05000947f76cd4d2235958a695e7a8c"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _committed_result_bytes() -> bytes:
    """Read the immutable HEAD ledger even if an earlier CI step rewrote the workspace copy."""
    try:
        return subprocess.check_output(
            ["git", "show", "HEAD:fixed_page_primary_results.json"],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return RESULTS_PATH.read_bytes()


def _without_allocated_bytes(value: Any) -> Any:
    """Remove only host/filesystem-dependent st_blocks observations."""
    if isinstance(value, dict):
        return {
            key: _without_allocated_bytes(item)
            for key, item in value.items()
            if key != "allocated_bytes"
        }
    if isinstance(value, list):
        return [_without_allocated_bytes(item) for item in value]
    return value


def _allocated_measurements(value: Any, path: tuple[Any, ...] = ()) -> dict[tuple[Any, ...], tuple[int, int]]:
    rows: dict[tuple[Any, ...], tuple[int, int]] = {}
    if isinstance(value, dict):
        if "allocated_bytes" in value:
            if "file_size_bytes" not in value:
                raise AssertionError(f"allocated_bytes lacks file_size_bytes at {path}")
            allocated = value["allocated_bytes"]
            file_size = value["file_size_bytes"]
            if type(allocated) is not int or type(file_size) is not int:
                raise AssertionError(
                    f"v0.24 allocation observations must be integer byte counts at {path}: "
                    f"allocated_bytes={allocated!r}, file_size_bytes={file_size!r}"
                )
            rows[path] = (allocated, file_size)
        for key, item in value.items():
            rows.update(_allocated_measurements(item, path + (key,)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            rows.update(_allocated_measurements(item, path + (index,)))
    return rows


def _require_portable_allocation_observations(recorded: dict, reproduced: dict) -> int:
    expected = _allocated_measurements(recorded)
    observed = _allocated_measurements(reproduced)
    if set(expected) != set(observed):
        raise AssertionError("v0.24 allocated-byte observation locations drifted")
    drifted = 0
    for path, (allocated, file_size) in observed.items():
        if allocated < 0 or allocated % 512:
            raise AssertionError(f"v0.24 allocated bytes are not stat-block aligned at {path}: {allocated}")
        if file_size < 0 or allocated > file_size:
            raise AssertionError(
                f"v0.24 allocated bytes exceed logical file size at {path}: {allocated}>{file_size}"
            )
        if allocated != expected[path][0]:
            drifted += 1
    return drifted


def _require_semantic_guard(out: dict) -> None:
    required = (
        "membership_equal",
        "materialization_equal",
        "head_index_equal",
        "all_derived_fresh",
        "full_assembly_equal",
        "partial_assembly_equal",
    )
    if not all(bool(out["semantic_guard"][name]) for name in required):
        raise AssertionError("v0.24 semantic guard failed")


def _require_crash_matrix(out: dict) -> None:
    rows = out["crash_rows"]
    expected_scenarios = {"ordinary_insert", "migration_start", "migration_progress"}
    expected_failpoints = {"pages_written", "data_synced", "committed"}
    expected_pairs = {
        (scenario, failpoint)
        for scenario in expected_scenarios
        for failpoint in expected_failpoints
    }
    observed_pairs = {(row["scenario"], row["failpoint"]) for row in rows}
    if len(rows) != 9 or observed_pairs != expected_pairs:
        raise AssertionError("v0.24 crash-matrix coverage drifted")

    for row in rows:
        committed = row["failpoint"] == "committed"
        if bool(row["expected_committed"]) != committed:
            raise AssertionError("v0.24 commit expectation disagrees with failpoint")
        if bool(row["target_visible_after_crash"]) != committed:
            raise AssertionError("v0.24 crash visibility disagrees with commit boundary")
        if not row["exact_snapshot_match"]:
            raise AssertionError("v0.24 SIGKILL exposed a torn logical snapshot")
        if not row["existing_keys_found"] or not row["audit_valid"]:
            raise AssertionError("v0.24 SIGKILL lost or duplicated primary membership")
        if not row["recovery_idempotent"]:
            raise AssertionError("v0.24 recovery was not idempotent")
        for pass_name in ("recovery_one", "recovery_two"):
            recovery = row[pass_name]
            if int(recovery["logical_work"]) != 0 or not recovery["audit_valid"]:
                raise AssertionError("v0.24 fixed-page recovery required logical repair")
        expected_epoch = int(row["post_epoch"] if committed else row["pre_epoch"])
        if int(row["crash_epoch"]) != expected_epoch:
            raise AssertionError("v0.24 committed epoch disagrees with crash boundary")


def _require_common_envelope(out: dict) -> None:
    common = out["common_envelope"]
    rows = common["rows"]
    if [int(row["membership_rows"]) for row in rows] != [256, 1024, 4096, 16384]:
        raise AssertionError("v0.24 common-envelope checkpoints drifted")
    if int(common["global_max_source_slots_scanned"]) > 8:
        raise AssertionError("v0.24 source migration exceeded eight-slot budget")
    if int(common["global_max_rows_moved"]) > 8:
        raise AssertionError("v0.24 migration moved more than eight source rows")
    if int(common["migration_starts"]) <= 0 or int(common["migration_starts"]) != int(common["migration_completions"]):
        raise AssertionError("v0.24 common envelope did not complete every migration")

    address = common["address_formula"]
    if int(address["page_size"]) != 4096:
        raise AssertionError("v0.24 fixed page size drifted")
    if int(address["superblock_copies"]) != 2 or int(address["logical_page_copies"]) != 2:
        raise AssertionError("v0.24 two-copy durability geometry drifted")
    if address["primary_index_structure"] != "none":
        raise AssertionError("v0.24 primary path gained a comparison index")
    if address["byte_offset"] != "PAGE_SIZE * (2 + 2*logical_page_id + copy_slot)":
        raise AssertionError("v0.24 arithmetic address formula drifted")

    for row in rows:
        if int(row["interval_max_source_slots_scanned"]) > 8:
            raise AssertionError("v0.24 interval migration source scan exceeded budget")
        if int(row["interval_max_rows_moved"]) > 8:
            raise AssertionError("v0.24 interval migrated rows exceeded budget")
        if int(row["lookup_total_pread_max"]) > 8:
            raise AssertionError("v0.24 single-generation successful lookup exceeded pread envelope")
        if int(row["missing_total_preads"]) != 8:
            raise AssertionError("v0.24 single-generation missing lookup envelope drifted")
        if not row["audit_valid"]:
            raise AssertionError("v0.24 common-envelope audit failed")


def _require_active_migration(out: dict) -> None:
    row = out["active_migration_lookup_envelope"]
    if int(row["source_slots_scanned_on_start"]) > 8 or int(row["rows_moved_on_start"]) > 8:
        raise AssertionError("v0.24 migration-start source work exceeded budget")
    if int(row["successful_lookup_generations_max"]) != 2:
        raise AssertionError("v0.24 active migration did not exercise two generations")
    if int(row["successful_lookup_total_pread_max"]) > 14:
        raise AssertionError("v0.24 active-migration successful lookup exceeded pread envelope")
    if int(row["missing_total_preads"]) != 14:
        raise AssertionError("v0.24 active-migration missing lookup envelope drifted")
    if not row["audit_valid"]:
        raise AssertionError("v0.24 active-migration audit failed")


def _require_collision_rejection(out: dict) -> None:
    row = out["collision_rejection"]
    if int(row["admitted_keys"]) != 16 or int(row["first_rejected_ordinal"]) != 17:
        raise AssertionError("v0.24 concentrated-capacity control drifted")
    if not row["rejected"] or not row["snapshot_unchanged"] or not row["audit_valid"]:
        raise AssertionError("v0.24 bounded rejection mutated committed logical state")


def main() -> None:
    workspace_text = RESULTS_PATH.read_text()
    committed_bytes = _committed_result_bytes()
    committed_sha = _sha256_bytes(committed_bytes)
    if committed_sha != EXPECTED_RESULT_SHA256:
        raise AssertionError(
            "v0.24 immutable committed evidence drifted from the initial successful CI artifact: "
            f"expected {EXPECTED_RESULT_SHA256}, observed {committed_sha}"
        )
    recorded = json.loads(committed_bytes)

    try:
        out = run_fixed_page_primary_experiment.run()
        _require_semantic_guard(out)
        _require_crash_matrix(out)
        _require_common_envelope(out)
        _require_active_migration(out)
        _require_collision_rejection(out)

        if _without_allocated_bytes(out) != _without_allocated_bytes(recorded):
            raise AssertionError(
                "v0.24 executable evidence drifted outside filesystem-dependent allocated_bytes"
            )
        allocation_drifts = _require_portable_allocation_observations(recorded, out)

        parsed = json.loads(RESULTS_PATH.read_text())
        if parsed != out:
            raise AssertionError("serialized v0.24 result differs from returned experiment object")
        reproduced_sha = _sha256_bytes(RESULTS_PATH.read_bytes())

        print("RECORDED_FIXED_PAGE_PRIMARY_RESULTS_MATCH_EXECUTABLE_SEMANTICS")
        print(f"IMMUTABLE_RESULT_SHA256={committed_sha}")
        print(f"REPRODUCED_RAW_SHA256={reproduced_sha}")
        print(f"FILESYSTEM_ALLOCATION_OBSERVATIONS_DRIFTED={allocation_drifts}")
        print(f"INITIAL_ARTIFACT_SHA256={EXPECTED_ARTIFACT_SHA256}")
    finally:
        RESULTS_PATH.write_text(workspace_text)


if __name__ == "__main__":
    main()
