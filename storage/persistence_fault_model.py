from __future__ import annotations

from dataclasses import dataclass

PAGE_SIZE = 4096


@dataclass
class FileDurabilityState:
    """Minimal volatile-vs-durable file-length model.

    `ftruncate` changes the process-visible/volatile frontier. Only `fsync`
    advances the durable media frontier. A modeled power loss discards the
    volatile frontier and reopens at the durable frontier.
    """

    reachable_size: int
    durable_size: int
    volatile_size: int

    @classmethod
    def with_stale_tail(cls, reachable_size: int, stale_tail_bytes: int) -> "FileDurabilityState":
        size = reachable_size + stale_tail_bytes
        return cls(reachable_size=reachable_size, durable_size=size, volatile_size=size)

    @property
    def durable_tail_bytes(self) -> int:
        return max(0, self.durable_size - self.reachable_size)

    @property
    def volatile_tail_bytes(self) -> int:
        return max(0, self.volatile_size - self.reachable_size)

    def ftruncate_to_reachable(self) -> int:
        reclaimed = self.volatile_tail_bytes
        self.volatile_size = self.reachable_size
        return reclaimed

    def fsync(self) -> None:
        self.durable_size = self.volatile_size

    def power_loss(self) -> None:
        self.volatile_size = self.durable_size


@dataclass
class OverflowDurabilityState:
    """Abstract durable transaction model for hidden future overflow rows."""

    durable_future_rows: int
    pending_delete: bool = False

    def begin_delete_future_rows(self) -> None:
        self.pending_delete = self.durable_future_rows > 0

    def commit(self) -> int:
        if not self.pending_delete:
            return 0
        deleted = self.durable_future_rows
        self.durable_future_rows = 0
        self.pending_delete = False
        return deleted

    def power_loss(self) -> None:
        self.pending_delete = False


@dataclass
class RecoveryFaultState:
    file: FileDurabilityState
    overflow: OverflowDurabilityState
    durable_tail_clean_marker: bool = False

    def power_loss(self) -> None:
        self.file.power_loss()
        self.overflow.power_loss()

    def snapshot(self) -> dict:
        return {
            "durable_future_rows": self.overflow.durable_future_rows,
            "durable_tail_bytes": self.file.durable_tail_bytes,
            "volatile_tail_bytes": self.file.volatile_tail_bytes,
            "durable_tail_clean_marker": self.durable_tail_clean_marker,
        }


def stale_tail_bytes_for_capacity(capacity: int) -> int:
    """Same v0.25/v0.26 eager-generation stale file-length range."""

    return PAGE_SIZE * (capacity + 2)


def make_state(*, future_rows: int, stale_tail_bytes: int, reachable_size: int = PAGE_SIZE * 10) -> RecoveryFaultState:
    return RecoveryFaultState(
        file=FileDurabilityState.with_stale_tail(reachable_size, stale_tail_bytes),
        overflow=OverflowDurabilityState(durable_future_rows=future_rows),
    )


def derived_recovery_pass(state: RecoveryFaultState, failpoint: str | None = None) -> dict:
    """Run one recovery pass using only durable residue derived from state.

    This deliberately has no durable cleanup-complete marker. On every restart,
    the protocol asks two questions again: are hidden future rows still durable,
    and is the durable file-length frontier beyond the committed reachable
    frontier?
    """

    allowed = {
        None,
        "future_delete_uncommitted",
        "future_delete_committed",
        "after_future_cleanup",
        "tail_truncated",
        "tail_synced",
    }
    if failpoint not in allowed:
        raise ValueError(f"unsupported failpoint: {failpoint}")

    trace = {
        "deleted_future_rows": 0,
        "sqlite_commits": 0,
        "truncate_calls": 0,
        "file_fsyncs": 0,
        "logical_redo": 0,
        "power_loss": False,
    }

    if state.overflow.durable_future_rows > 0:
        state.overflow.begin_delete_future_rows()
        if failpoint == "future_delete_uncommitted":
            state.power_loss()
            trace["power_loss"] = True
            return trace
        trace["deleted_future_rows"] = state.overflow.commit()
        trace["sqlite_commits"] = 1
        if failpoint == "future_delete_committed":
            state.power_loss()
            trace["power_loss"] = True
            return trace

    if failpoint == "after_future_cleanup":
        state.power_loss()
        trace["power_loss"] = True
        return trace

    if state.file.volatile_tail_bytes > 0:
        state.file.ftruncate_to_reachable()
        trace["truncate_calls"] = 1
        if failpoint == "tail_truncated":
            state.power_loss()
            trace["power_loss"] = True
            return trace
        state.file.fsync()
        trace["file_fsyncs"] = 1
        if failpoint == "tail_synced":
            state.power_loss()
            trace["power_loss"] = True
            return trace

    return trace


def marker_recovery_pass(state: RecoveryFaultState, failpoint: str | None = None) -> dict:
    """Negative control: persist a cleanup marker before the file fsync.

    If power is lost after the marker becomes durable but before the file length
    does, restart trusts the marker and skips the still-required truncate. The
    stale tail is therefore stranded.
    """

    if failpoint not in {None, "marker_committed_before_tail_fsync"}:
        raise ValueError(f"unsupported failpoint: {failpoint}")

    trace = {
        "marker_commits": 0,
        "truncate_calls": 0,
        "file_fsyncs": 0,
        "logical_redo": 0,
        "power_loss": False,
    }

    if state.durable_tail_clean_marker:
        return trace

    if state.file.volatile_tail_bytes > 0:
        state.file.ftruncate_to_reachable()
        trace["truncate_calls"] = 1
        state.durable_tail_clean_marker = True
        trace["marker_commits"] = 1
        if failpoint == "marker_committed_before_tail_fsync":
            state.power_loss()
            trace["power_loss"] = True
            return trace
        state.file.fsync()
        trace["file_fsyncs"] = 1

    return trace
