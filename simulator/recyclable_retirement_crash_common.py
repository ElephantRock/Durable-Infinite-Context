from __future__ import annotations

import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

from storage.recyclable_retirement_descriptor_primary import (
    RecyclableRetirementDescriptorPrimaryStore,
)

ROOT = Path(__file__).resolve().parent.parent
WORKER = ROOT / "recyclable_retirement_descriptor_worker.py"


def worker(
    path: Path,
    command: str,
    *,
    failpoint: str,
    key: str | None = None,
    budget: int | None = None,
) -> subprocess.CompletedProcess[str]:
    argv = [sys.executable, str(WORKER), "--file", str(path), command]
    if key is not None:
        argv.extend(["--key", key])
    if budget is not None:
        argv.extend(["--budget", str(budget)])
    argv.extend(["--failpoint", failpoint])
    return subprocess.run(
        argv,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def require_sigkill(proc: subprocess.CompletedProcess[str], label: str) -> None:
    if proc.returncode != -signal.SIGKILL:
        raise AssertionError(
            f"{label} did not SIGKILL: {proc.returncode} {proc.stderr}"
        )


def semantic_state(
    store: RecyclableRetirementDescriptorPrimaryStore,
    keys: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "state": store.committed_state(keys),
        "queue": store.retirement_queue_snapshot(),
    }


def require_scan_free(recovery: dict[str, Any]) -> None:
    if int(recovery["logical_work"]) != 0:
        raise AssertionError("v0.35 recovery required logical redo")
    if int(recovery["generation_pages_scanned"]) != 0:
        raise AssertionError("v0.35 recovery scanned generation pages")
    if int(recovery["mapping_nodes_scanned"]) != 0:
        raise AssertionError("v0.35 recovery scanned radix nodes")
    if int(recovery.get("retirement_descriptors_scanned", 0)) != 0:
        raise AssertionError("v0.35 recovery scanned retirement descriptors")


def recover_twice(store: RecyclableRetirementDescriptorPrimaryStore) -> tuple[dict[str, Any], dict[str, Any]]:
    one = store.recover()
    two = store.recover()
    require_scan_free(one)
    require_scan_free(two)
    if int(two["physical_truncated_bytes"]) != 0:
        raise AssertionError("v0.35 second recovery is not physically idempotent")
    return one, two


def drain_all(store: RecyclableRetirementDescriptorPrimaryStore) -> None:
    while store.retirement_queue_snapshot()["queue_count"]:
        store.reclaim_step(budget=2)


def build_empty_reuse_fixture(path: Path) -> None:
    store = RecyclableRetirementDescriptorPrimaryStore(str(path))
    store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=512)
    for index in range(65):
        store.insert(f"k-{index:03d}")
    drain_all(store)
    for index in range(65, 128):
        store.insert(f"k-{index:03d}")
    queue = store.retirement_queue_snapshot()
    if queue["queue_count"] != 0 or queue["descriptor_free_count"] != 3:
        raise AssertionError("empty reuse fixture lacks three free descriptors")


def build_nonempty_reuse_fixture(path: Path) -> None:
    build_empty_reuse_fixture(path)
    store = RecyclableRetirementDescriptorPrimaryStore(str(path))
    store.insert("k-128")
    for index in range(129, 256):
        store.insert(f"k-{index:03d}")
    queue = store.retirement_queue_snapshot()
    if queue["queue_count"] != 1 or queue["descriptor_free_count"] != 2:
        raise AssertionError("non-empty reuse fixture has wrong queue/free depth")


def build_partial_reclaim_fixture(path: Path) -> None:
    store = RecyclableRetirementDescriptorPrimaryStore(str(path))
    store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=512)
    for index in range(65):
        store.insert(f"k-{index:03d}")
    while True:
        queue = store.retirement_queue_snapshot()
        if queue["queue_count"] <= 0:
            raise AssertionError("could not find multi-segment v0.35 retirement head")
        remaining = int(queue["descriptors"][0]["remaining_segments"])
        if remaining > 1:
            return
        store.reclaim_step(budget=remaining)


def build_dequeue_fixture(path: Path) -> None:
    store = RecyclableRetirementDescriptorPrimaryStore(str(path))
    store.initialize(initial_capacity=32, max_load=0.50, migration_slot_budget=512)
    for index in range(17):
        store.insert(f"k-{index:03d}")
    queue = store.retirement_queue_snapshot()
    if queue["queue_count"] != 1:
        raise AssertionError("dequeue fixture lacks one queued descriptor")
    remaining = int(queue["descriptors"][0]["remaining_segments"])
    if remaining > 1:
        store.reclaim_step(budget=remaining - 1)
    queue = store.retirement_queue_snapshot()
    if int(queue["descriptors"][0]["remaining_segments"]) != 1:
        raise AssertionError("dequeue fixture did not reach one remaining segment")
    if queue["descriptor_free_count"] != 0:
        raise AssertionError("dequeue fixture unexpectedly has free descriptors")
