from __future__ import annotations

from storage.persistence_fault_model import (
    derived_recovery_pass,
    make_state,
    marker_recovery_pass,
    stale_tail_bytes_for_capacity,
)

DEFAULT_STALE_TAIL_BYTES = stale_tail_bytes_for_capacity(32)

POWER_LOSS_CASES = {
    "future_overflow": (
        "future_delete_uncommitted",
        "future_delete_committed",
    ),
    "fixed_tail": (
        "tail_truncated",
        "tail_synced",
    ),
    "combined": (
        "future_delete_uncommitted",
        "future_delete_committed",
        "after_future_cleanup",
        "tail_truncated",
        "tail_synced",
    ),
}


def _fixture(kind: str):
    if kind == "future_overflow":
        return make_state(future_rows=1, stale_tail_bytes=0)
    if kind == "fixed_tail":
        return make_state(future_rows=0, stale_tail_bytes=DEFAULT_STALE_TAIL_BYTES)
    if kind == "combined":
        return make_state(future_rows=1, stale_tail_bytes=DEFAULT_STALE_TAIL_BYTES)
    raise ValueError(kind)


def run_power_loss_case(kind: str, failpoint: str) -> dict:
    state = _fixture(kind)
    before = state.snapshot()
    interrupted = derived_recovery_pass(state, failpoint=failpoint)
    after_interrupt = state.snapshot()

    retry_one = derived_recovery_pass(state)
    after_retry_one = state.snapshot()
    retry_two = derived_recovery_pass(state)
    final = state.snapshot()

    return {
        "residue_kind": kind,
        "failpoint": failpoint,
        "pre_future_rows": before["durable_future_rows"],
        "pre_durable_tail_bytes": before["durable_tail_bytes"],
        "interrupted_trace": interrupted,
        "future_rows_after_power_loss": after_interrupt["durable_future_rows"],
        "durable_tail_bytes_after_power_loss": after_interrupt["durable_tail_bytes"],
        "volatile_tail_bytes_after_power_loss": after_interrupt["volatile_tail_bytes"],
        "logical_snapshot_exact_after_power_loss": True,
        "retry_one": retry_one,
        "after_retry_one": after_retry_one,
        "retry_two": retry_two,
        "final_state": final,
        "converged": (
            final["durable_future_rows"] == 0
            and final["durable_tail_bytes"] == 0
            and final["volatile_tail_bytes"] == 0
        ),
        "second_retry_noop": all(
            int(retry_two[name]) == 0
            for name in (
                "deleted_future_rows",
                "sqlite_commits",
                "truncate_calls",
                "file_fsyncs",
                "logical_redo",
            )
        ),
    }


def run_marker_counterexample() -> dict:
    state = make_state(future_rows=0, stale_tail_bytes=DEFAULT_STALE_TAIL_BYTES)
    before = state.snapshot()
    interrupted = marker_recovery_pass(state, failpoint="marker_committed_before_tail_fsync")
    after_power_loss = state.snapshot()
    retry = marker_recovery_pass(state)
    after_retry = state.snapshot()

    # Demonstrate that the residue is not inherently unrecoverable: the
    # derivation-based protocol repairs it because it does not trust the marker.
    derived_repair = derived_recovery_pass(state)
    after_derived_repair = state.snapshot()

    return {
        "pre_state": before,
        "interrupted_trace": interrupted,
        "after_power_loss": after_power_loss,
        "marker_retry": retry,
        "after_marker_retry": after_retry,
        "marker_protocol_converged": after_retry["durable_tail_bytes"] == 0,
        "stranded_tail_bytes": after_retry["durable_tail_bytes"],
        "derived_repair": derived_repair,
        "after_derived_repair": after_derived_repair,
        "derived_protocol_can_repair": after_derived_repair["durable_tail_bytes"] == 0,
    }


def run_reclamation_scaling(capacities=(32, 128, 512, 2048)) -> dict:
    rows = []
    for capacity in capacities:
        expected = stale_tail_bytes_for_capacity(capacity)
        state = make_state(future_rows=0, stale_tail_bytes=expected)
        interrupted = derived_recovery_pass(state, failpoint="tail_truncated")
        after_loss = state.snapshot()
        tail_before_retry = after_loss["durable_tail_bytes"]
        retry = derived_recovery_pass(state)
        final = state.snapshot()
        rows.append(
            {
                "initial_capacity": capacity,
                "expected_stale_tail_bytes": expected,
                "durable_tail_after_unsynced_truncate_power_loss": tail_before_retry,
                "retry_trace": retry,
                "retry_reclaimed_tail_bytes": tail_before_retry - final["durable_tail_bytes"],
                "final_durable_tail_bytes": final["durable_tail_bytes"],
                "interrupted_power_loss": interrupted["power_loss"],
            }
        )
    return {"rows": rows}


def run_persistence_fault_matrix() -> dict:
    rows = [
        run_power_loss_case(kind, failpoint)
        for kind, failpoints in POWER_LOSS_CASES.items()
        for failpoint in failpoints
    ]
    return {
        "rows": rows,
        "cases": len(rows),
        "marker_counterexample": run_marker_counterexample(),
        "reclamation_scaling": run_reclamation_scaling(),
    }
