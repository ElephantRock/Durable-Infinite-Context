from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from storage.deep_middle_live_tail_evacuation_retirement_descriptor_primary import (
    DeepMiddleLiveTailEvacuationRetirementDescriptorPrimaryStore,
)
from storage.fixed_page_primary import PAGE_SIZE
from storage.queued_predecessor_index import QueuedPredecessorIO
from storage.retirement_descriptor_pool import (
    RETIREMENT_DESCRIPTOR_COPIES,
    RETIREMENT_STATUS_FREE,
    RETIREMENT_STATUS_QUEUED,
    TaggedDescriptorIO,
)


@dataclass(frozen=True)
class QueuedPredecessorInsertTrace:
    base: Any
    queued_predecessor_preads: int
    queued_predecessor_pwrites: int
    queued_predecessor_fsyncs: int

    def __getattr__(self, name: str) -> Any:
        return getattr(self.base, name)

    def to_dict(self) -> dict[str, Any]:
        row = self.base.to_dict()
        row.update(
            {
                "queued_predecessor_preads": self.queued_predecessor_preads,
                "queued_predecessor_pwrites": self.queued_predecessor_pwrites,
                "queued_predecessor_fsyncs": self.queued_predecessor_fsyncs,
            }
        )
        return row


@dataclass(frozen=True)
class QueuedPredecessorReclaimTrace:
    base: Any
    queued_predecessor_preads: int
    queued_predecessor_pwrites: int
    queued_predecessor_fsyncs: int
    queued_predecessor_sidecar_truncated_bytes: int

    def __getattr__(self, name: str) -> Any:
        return getattr(self.base, name)

    def to_dict(self) -> dict[str, Any]:
        row = self.base.to_dict()
        row.update(
            {
                "queued_predecessor_preads": self.queued_predecessor_preads,
                "queued_predecessor_pwrites": self.queued_predecessor_pwrites,
                "queued_predecessor_fsyncs": self.queued_predecessor_fsyncs,
                "queued_predecessor_sidecar_truncated_bytes": (
                    self.queued_predecessor_sidecar_truncated_bytes
                ),
            }
        )
        return row


@dataclass(frozen=True)
class BidirectionalQueuedTailEvacuationTrace:
    committed_epoch_before: int
    committed_epoch_after: int
    retirement_queue_count: int
    retirement_descriptor_free_count_before: int
    retirement_descriptor_free_count_after: int
    retirement_arena_pages_before: int
    retirement_arena_pages_after: int
    retirement_arena_pages_released: int
    retirement_arena_bytes_released: int
    retirement_descriptor_preads: int
    retirement_descriptor_pwrites: int
    queued_predecessor_preads: int
    queued_predecessor_pwrites: int
    retirement_descriptors_scanned: int
    queued_predecessors_scanned: int
    live_descriptor_relocations: int
    physical_tail_page: int | None
    physical_tail_incarnation: int | None
    predecessor_page: int | None
    predecessor_incarnation: int | None
    predecessor_predecessor_page: int | None
    predecessor_predecessor_incarnation: int | None
    successor_page: int | None
    successor_incarnation: int | None
    destination_page: int | None
    destination_old_incarnation: int | None
    destination_new_incarnation: int | None
    new_physical_tail_page: int | None
    new_physical_tail_incarnation: int | None
    new_physical_tail_predecessor_page: int | None
    new_physical_tail_predecessor_incarnation: int | None
    retirement_arena_fsyncs: int
    queued_predecessor_fsyncs: int

    @property
    def released(self) -> bool:
        return self.retirement_arena_pages_released > 0

    def to_dict(self) -> dict[str, Any]:
        row = dict(self.__dict__)
        row["released"] = self.released
        return row


class BidirectionalQueuedRetirementDescriptorPrimaryStore(
    DeepMiddleLiveTailEvacuationRetirementDescriptorPrimaryStore
):
    """v0.44 page-addressed tagged predecessor authority for QUEUED descriptors.

    v0.43 carries a fixed two-hop physical-tail reverse window in superblock metadata.
    v0.44 instead stores one tagged predecessor identity for every QUEUED descriptor in
    a dual-copy page-addressed sidecar keyed by descriptor base page. The superblock
    retains only the directly named physical-tail anchor inherited from v0.42.

    The claim-bearing relocation path does not consult v0.43's second reverse-hop field.
    It reads T.prev -> P and P.prev -> PP directly, validates the corresponding forward
    edges, rewires P.next and S.prev around a relocated T, and publishes the shorter
    arena frontier. Prefix depth is therefore outside the direct-work count.
    """

    ARENA_SUFFIX = ".retire44"
    PREDECESSOR_SUFFIX = ".retire44prev"

    def __init__(self, path: str | Path) -> None:
        super().__init__(str(path))
        self._pending_queued_predecessor_preads = 0
        self._pending_queued_predecessor_pwrites = 0
        self._pending_queued_predecessor_fsyncs = 0

    @property
    def predecessor_path(self) -> Path:
        return Path(f"{self.path}{self.PREDECESSOR_SUFFIX}")

    @classmethod
    def predecessor_path_for(cls, path: str | Path) -> Path:
        return Path(f"{path}{cls.PREDECESSOR_SUFFIX}")

    def _open_predecessors(self) -> int:
        return os.open(self.predecessor_path, os.O_RDWR)

    def _reset_operation_state(self) -> None:
        super()._reset_operation_state()
        self._pending_queued_predecessor_preads = 0
        self._pending_queued_predecessor_pwrites = 0
        self._pending_queued_predecessor_fsyncs = 0

    def initialize(
        self,
        *,
        initial_capacity: int = 128,
        max_load: float = 0.50,
        bucket_size: int = 4,
        max_kicks: int = 32,
        stash_capacity: int = 8,
        migration_slot_budget: int = 8,
        force_same_pair: bool = False,
    ) -> None:
        if self.predecessor_path.exists():
            raise RuntimeError("v0.44 queued predecessor sidecar already exists")
        super().initialize(
            initial_capacity=initial_capacity,
            max_load=max_load,
            bucket_size=bucket_size,
            max_kicks=max_kicks,
            stash_capacity=stash_capacity,
            migration_slot_budget=migration_slot_budget,
            force_same_pair=force_same_pair,
        )
        predecessor_fd = os.open(
            self.predecessor_path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600
        )
        try:
            os.fsync(predecessor_fd)
        finally:
            os.close(predecessor_fd)

        fd = self._open()
        try:
            epoch, meta, slot = self._read_super(fd)
            next_meta = dict(meta)
            next_meta["format"] = 44
            os.pwrite(
                fd,
                self._pack_record(self._super_magic(), epoch, next_meta),
                self._super_offset(slot),
            )
            os.fsync(fd)
        finally:
            os.close(fd)

    @staticmethod
    def _require_prev(
        payload: dict[str, Any],
        *,
        page: int | None,
        incarnation: int | None,
        label: str,
    ) -> None:
        if payload.get("prev_page") != page or payload.get("prev_incarnation") != incarnation:
            raise RuntimeError(
                f"v0.44 {label} predecessor drifted: "
                f"{payload.get('prev_page')}/{payload.get('prev_incarnation')} "
                f"!= {page}/{incarnation}"
            )

    def _enqueue_retirement_tx(
        self,
        tx,
        *,
        generation: int,
        owner_head_page: int | None,
        owner_count: int,
    ) -> None:  # type: ignore[no-untyped-def]
        if owner_count <= 0:
            return
        committed_epoch = int(tx.epoch)
        new_epoch = committed_epoch + 1
        old_count = int(tx.meta.get("retirement_queue_count", 0))
        old_tail_page = tx.meta.get("retirement_queue_tail_page")
        old_tail_incarnation = tx.meta.get("retirement_queue_tail_incarnation")
        old_free_count = int(tx.meta.get("retirement_descriptor_free_count", 0))
        old_free_page = tx.meta.get("retirement_descriptor_free_head_page")
        old_free_incarnation = tx.meta.get("retirement_descriptor_free_head_incarnation")

        super()._enqueue_retirement_tx(
            tx,
            generation=generation,
            owner_head_page=owner_head_page,
            owner_count=owner_count,
        )

        new_tail_page = tx.meta.get("retirement_queue_tail_page")
        new_tail_incarnation = tx.meta.get("retirement_queue_tail_incarnation")
        if new_tail_page is None or new_tail_incarnation is None:
            raise RuntimeError("v0.44 enqueue did not publish tagged queue tail")
        expected_prev_page = None if old_count == 0 else int(old_tail_page)
        expected_prev_incarnation = None if old_count == 0 else int(old_tail_incarnation)

        predecessor_fd = self._open_predecessors()
        try:
            if old_free_count > 0:
                if old_free_page is None or old_free_incarnation is None:
                    raise RuntimeError("v0.44 FREE reuse lacks old head identity")
                if int(new_tail_page) != int(old_free_page):
                    raise RuntimeError("v0.44 FREE reuse did not consume old free head")
                _old_prev, old_slot, preads = QueuedPredecessorIO.read(
                    predecessor_fd,
                    int(old_free_page),
                    committed_epoch,
                    expected_incarnation=int(old_free_incarnation),
                )
                self._pending_queued_predecessor_preads += preads
                self._pending_queued_predecessor_pwrites += QueuedPredecessorIO.write_existing(
                    predecessor_fd,
                    int(new_tail_page),
                    new_epoch,
                    committed_slot=old_slot,
                    incarnation=int(new_tail_incarnation),
                    prev_page=expected_prev_page,
                    prev_incarnation=expected_prev_incarnation,
                )
            else:
                self._pending_queued_predecessor_pwrites += QueuedPredecessorIO.write_new(
                    predecessor_fd,
                    int(new_tail_page),
                    new_epoch,
                    incarnation=int(new_tail_incarnation),
                    prev_page=expected_prev_page,
                    prev_incarnation=expected_prev_incarnation,
                )
            os.fsync(predecessor_fd)
            self._pending_queued_predecessor_fsyncs += 1
            if self._active_failpoint is not None:
                self._active_failpoint("retirement_queued_predecessor_synced")
        finally:
            os.close(predecessor_fd)

    def insert(
        self,
        key: str,
        failpoint: Callable[[str], None] | None = None,
    ) -> QueuedPredecessorInsertTrace:
        base = super().insert(key, failpoint=failpoint)
        return QueuedPredecessorInsertTrace(
            base=base,
            queued_predecessor_preads=int(self._pending_queued_predecessor_preads),
            queued_predecessor_pwrites=int(self._pending_queued_predecessor_pwrites),
            queued_predecessor_fsyncs=int(self._pending_queued_predecessor_fsyncs),
        )

    def reclaim_step(
        self,
        *,
        budget: int,
        failpoint: Callable[[str], None] | None = None,
    ) -> QueuedPredecessorReclaimTrace:
        fd = self._open()
        try:
            committed_epoch, meta, _slot = self._read_super(fd)
            old_queue_count = int(meta.get("retirement_queue_count", 0))
            old_head_page = meta.get("retirement_queue_head_page")
            old_head_incarnation = meta.get("retirement_queue_head_incarnation")
        finally:
            os.close(fd)

        predecessor_preads = 0
        predecessor_pwrites = 0
        predecessor_fsyncs = 0

        def topology_failpoint(name: str) -> None:
            nonlocal predecessor_preads, predecessor_pwrites, predecessor_fsyncs
            if failpoint is not None:
                failpoint(name)
            if name != "retirement_descriptor_freed" or old_queue_count <= 1:
                return
            if old_head_page is None or old_head_incarnation is None:
                raise RuntimeError("v0.44 reclaim lacks old queue head identity")
            arena_fd = self._open_arena()
            predecessor_fd = self._open_predecessors()
            try:
                old_head, _old_head_slot, descriptor_preads = TaggedDescriptorIO.read(
                    arena_fd,
                    int(old_head_page),
                    committed_epoch,
                    expected_incarnation=int(old_head_incarnation),
                    expected_status=RETIREMENT_STATUS_QUEUED,
                )
                successor_page = old_head["next_descriptor_page"]
                successor_incarnation = old_head["next_descriptor_incarnation"]
                if successor_page is None or successor_incarnation is None:
                    raise RuntimeError("v0.44 non-final queue head lacks successor")
                successor_prev, successor_slot, preads = QueuedPredecessorIO.read(
                    predecessor_fd,
                    int(successor_page),
                    committed_epoch,
                    expected_incarnation=int(successor_incarnation),
                )
                predecessor_preads += preads
                self._require_prev(
                    successor_prev,
                    page=int(old_head_page),
                    incarnation=int(old_head_incarnation),
                    label="new queue head",
                )
                predecessor_pwrites += QueuedPredecessorIO.write_existing(
                    predecessor_fd,
                    int(successor_page),
                    committed_epoch + 1,
                    committed_slot=successor_slot,
                    incarnation=int(successor_incarnation),
                    prev_page=None,
                    prev_incarnation=None,
                )
                os.fsync(predecessor_fd)
                predecessor_fsyncs += 1
                if failpoint is not None:
                    failpoint("retirement_queued_head_predecessor_synced")
                if int(descriptor_preads) != 2:
                    raise AssertionError("v0.44 queued head descriptor read count drifted")
            finally:
                os.close(predecessor_fd)
                os.close(arena_fd)

        base = super().reclaim_step(budget=budget, failpoint=topology_failpoint)

        sidecar_truncated = 0
        fd = self._open()
        try:
            _epoch, committed_meta, _slot = self._read_super(fd)
            target = self._committed_arena_pages(committed_meta) * PAGE_SIZE
        finally:
            os.close(fd)
        predecessor_fd = self._open_predecessors()
        try:
            before = os.fstat(predecessor_fd).st_size
            if before > target:
                os.ftruncate(predecessor_fd, target)
                os.fsync(predecessor_fd)
                predecessor_fsyncs += 1
                sidecar_truncated = before - target
        finally:
            os.close(predecessor_fd)

        return QueuedPredecessorReclaimTrace(
            base=base,
            queued_predecessor_preads=predecessor_preads,
            queued_predecessor_pwrites=predecessor_pwrites,
            queued_predecessor_fsyncs=predecessor_fsyncs,
            queued_predecessor_sidecar_truncated_bytes=sidecar_truncated,
        )

    def retirement_queue_snapshot(self, *, max_descriptors: int = 1024) -> dict[str, Any]:
        if max_descriptors <= 0:
            raise ValueError("max_descriptors must be positive")
        row = super().retirement_queue_snapshot()
        if (
            int(row["queue_count"]) > max_descriptors
            or int(row["descriptor_free_count"]) > max_descriptors
        ):
            raise RuntimeError("diagnostic descriptor chain exceeds snapshot limit")
        fd = self._open()
        predecessor_fd = self._open_predecessors()
        try:
            committed_epoch, _meta, _slot = self._read_super(fd)
            expected_prev_page: int | None = None
            expected_prev_incarnation: int | None = None
            preads = 0
            for descriptor in row["descriptors"]:
                page = int(descriptor["descriptor_page"])
                incarnation = int(descriptor["descriptor_incarnation"])
                prev, _prev_slot, reads = QueuedPredecessorIO.read(
                    predecessor_fd,
                    page,
                    committed_epoch,
                    expected_incarnation=incarnation,
                )
                preads += reads
                self._require_prev(
                    prev,
                    page=expected_prev_page,
                    incarnation=expected_prev_incarnation,
                    label=f"descriptor {page}",
                )
                descriptor["queued_prev_page"] = prev.get("prev_page")
                descriptor["queued_prev_incarnation"] = prev.get("prev_incarnation")
                expected_prev_page = page
                expected_prev_incarnation = incarnation
            row["queued_predecessor_consistent"] = True
            row["diagnostic_queued_predecessor_preads"] = preads
            row["queued_predecessor_sidecar_bytes"] = os.fstat(predecessor_fd).st_size
            return row
        finally:
            os.close(predecessor_fd)
            os.close(fd)

    def queued_predecessor_reference(
        self,
        base_page: int,
        incarnation: int,
    ) -> dict[str, Any]:
        fd = self._open()
        predecessor_fd = self._open_predecessors()
        try:
            epoch, _meta, _slot = self._read_super(fd)
            payload, _slot, _preads = QueuedPredecessorIO.read(
                predecessor_fd,
                int(base_page),
                epoch,
                expected_incarnation=int(incarnation),
            )
            return payload
        finally:
            os.close(predecessor_fd)
            os.close(fd)

    @staticmethod
    def _no_bidirectional_release_trace(
        *,
        committed_epoch: int,
        queue_count: int,
        free_count: int,
        arena_pages: int,
        physical_tail_page: int | None = None,
        physical_tail_incarnation: int | None = None,
    ) -> BidirectionalQueuedTailEvacuationTrace:
        return BidirectionalQueuedTailEvacuationTrace(
            committed_epoch_before=committed_epoch,
            committed_epoch_after=committed_epoch,
            retirement_queue_count=queue_count,
            retirement_descriptor_free_count_before=free_count,
            retirement_descriptor_free_count_after=free_count,
            retirement_arena_pages_before=arena_pages,
            retirement_arena_pages_after=arena_pages,
            retirement_arena_pages_released=0,
            retirement_arena_bytes_released=0,
            retirement_descriptor_preads=0,
            retirement_descriptor_pwrites=0,
            queued_predecessor_preads=0,
            queued_predecessor_pwrites=0,
            retirement_descriptors_scanned=0,
            queued_predecessors_scanned=0,
            live_descriptor_relocations=0,
            physical_tail_page=physical_tail_page,
            physical_tail_incarnation=physical_tail_incarnation,
            predecessor_page=None,
            predecessor_incarnation=None,
            predecessor_predecessor_page=None,
            predecessor_predecessor_incarnation=None,
            successor_page=None,
            successor_incarnation=None,
            destination_page=None,
            destination_old_incarnation=None,
            destination_new_incarnation=None,
            new_physical_tail_page=None,
            new_physical_tail_incarnation=None,
            new_physical_tail_predecessor_page=None,
            new_physical_tail_predecessor_incarnation=None,
            retirement_arena_fsyncs=0,
            queued_predecessor_fsyncs=0,
        )

    def evacuate_bidirectional_queued_live_retirement_arena_tail_step(
        self,
        failpoint: Callable[[str], None] | None = None,
    ) -> BidirectionalQueuedTailEvacuationTrace:
        fd = self._open()
        arena_fd = self._open_arena()
        predecessor_fd = self._open_predecessors()
        try:
            committed_epoch, meta, _slot = self._read_super(fd)
            meta = dict(meta)
            arena_pages_before = self._committed_arena_pages(meta)
            queue_count = int(meta.get("retirement_queue_count", 0))
            free_count_before = int(meta.get("retirement_descriptor_free_count", 0))
            if (
                arena_pages_before < RETIREMENT_DESCRIPTOR_COPIES
                or queue_count < 5
                or free_count_before != 1
            ):
                return self._no_bidirectional_release_trace(
                    committed_epoch=committed_epoch,
                    queue_count=queue_count,
                    free_count=free_count_before,
                    arena_pages=arena_pages_before,
                )

            physical_tail_base = arena_pages_before - RETIREMENT_DESCRIPTOR_COPIES
            physical_page = meta.get("retirement_physical_tail_page")
            physical_incarnation = meta.get("retirement_physical_tail_incarnation")
            if physical_page != physical_tail_base or physical_incarnation is None:
                return self._no_bidirectional_release_trace(
                    committed_epoch=committed_epoch,
                    queue_count=queue_count,
                    free_count=free_count_before,
                    arena_pages=arena_pages_before,
                    physical_tail_page=physical_tail_base,
                    physical_tail_incarnation=(
                        None if physical_incarnation is None else int(physical_incarnation)
                    ),
                )

            descriptor_preads = 0
            predecessor_preads = 0
            descriptor_pwrites = 0
            predecessor_pwrites = 0

            tail, _tail_slot, reads = TaggedDescriptorIO.read(
                arena_fd,
                physical_tail_base,
                committed_epoch,
                expected_incarnation=int(physical_incarnation),
                expected_status=RETIREMENT_STATUS_QUEUED,
            )
            descriptor_preads += reads
            tail_prev, _tail_prev_slot, reads = QueuedPredecessorIO.read(
                predecessor_fd,
                physical_tail_base,
                committed_epoch,
                expected_incarnation=int(physical_incarnation),
            )
            predecessor_preads += reads
            predecessor_page = tail_prev.get("prev_page")
            predecessor_incarnation = tail_prev.get("prev_incarnation")
            if predecessor_page is None or predecessor_incarnation is None:
                raise RuntimeError("v0.44 physical tail lacks tagged QUEUED predecessor")

            predecessor, predecessor_slot, reads = TaggedDescriptorIO.read(
                arena_fd,
                int(predecessor_page),
                committed_epoch,
                expected_incarnation=int(predecessor_incarnation),
                expected_status=RETIREMENT_STATUS_QUEUED,
            )
            descriptor_preads += reads
            if (
                predecessor["next_descriptor_page"] != physical_tail_base
                or predecessor["next_descriptor_incarnation"] != int(physical_incarnation)
            ):
                raise RuntimeError("v0.44 predecessor does not reference physical tail")
            predecessor_prev, _predecessor_prev_slot, reads = QueuedPredecessorIO.read(
                predecessor_fd,
                int(predecessor_page),
                committed_epoch,
                expected_incarnation=int(predecessor_incarnation),
            )
            predecessor_preads += reads
            pp_page = predecessor_prev.get("prev_page")
            pp_incarnation = predecessor_prev.get("prev_incarnation")
            if pp_page is None or pp_incarnation is None:
                raise RuntimeError("v0.44 deeper physical predecessor lacks predecessor")
            queue_head_page = meta.get("retirement_queue_head_page")
            queue_head_incarnation = meta.get("retirement_queue_head_incarnation")
            if pp_page == queue_head_page and pp_incarnation == queue_head_incarnation:
                return self._no_bidirectional_release_trace(
                    committed_epoch=committed_epoch,
                    queue_count=queue_count,
                    free_count=free_count_before,
                    arena_pages=arena_pages_before,
                    physical_tail_page=physical_tail_base,
                    physical_tail_incarnation=int(physical_incarnation),
                )

            pp, _pp_slot, reads = TaggedDescriptorIO.read(
                arena_fd,
                int(pp_page),
                committed_epoch,
                expected_incarnation=int(pp_incarnation),
                expected_status=RETIREMENT_STATUS_QUEUED,
            )
            descriptor_preads += reads
            if (
                pp["next_descriptor_page"] != int(predecessor_page)
                or pp["next_descriptor_incarnation"] != int(predecessor_incarnation)
            ):
                raise RuntimeError("v0.44 predecessor-predecessor forward edge is stale")

            successor_page = tail["next_descriptor_page"]
            successor_incarnation = tail["next_descriptor_incarnation"]
            if successor_page is None or successor_incarnation is None:
                raise RuntimeError("v0.44 interior physical tail lacks successor")
            if (
                meta.get("retirement_queue_tail_page") != successor_page
                or meta.get("retirement_queue_tail_incarnation") != successor_incarnation
            ):
                raise RuntimeError("v0.44 bounded target requires physical-tail successor as logical tail")
            successor, _successor_slot, reads = TaggedDescriptorIO.read(
                arena_fd,
                int(successor_page),
                committed_epoch,
                expected_incarnation=int(successor_incarnation),
                expected_status=RETIREMENT_STATUS_QUEUED,
            )
            descriptor_preads += reads
            if successor["next_descriptor_page"] is not None:
                raise RuntimeError("v0.44 logical queue tail unexpectedly has successor")
            successor_prev, successor_prev_slot, reads = QueuedPredecessorIO.read(
                predecessor_fd,
                int(successor_page),
                committed_epoch,
                expected_incarnation=int(successor_incarnation),
            )
            predecessor_preads += reads
            self._require_prev(
                successor_prev,
                page=physical_tail_base,
                incarnation=int(physical_incarnation),
                label="logical tail",
            )

            free_page = meta.get("retirement_descriptor_free_head_page")
            free_incarnation = meta.get("retirement_descriptor_free_head_incarnation")
            if free_page is None or free_incarnation is None or int(free_page) >= physical_tail_base:
                return self._no_bidirectional_release_trace(
                    committed_epoch=committed_epoch,
                    queue_count=queue_count,
                    free_count=free_count_before,
                    arena_pages=arena_pages_before,
                    physical_tail_page=physical_tail_base,
                    physical_tail_incarnation=int(physical_incarnation),
                )
            destination, destination_slot, reads = TaggedDescriptorIO.read(
                arena_fd,
                int(free_page),
                committed_epoch,
                expected_incarnation=int(free_incarnation),
                expected_status=RETIREMENT_STATUS_FREE,
            )
            descriptor_preads += reads
            destination_prev_page, destination_prev_incarnation = self._free_prev(destination)
            if destination_prev_page is not None or destination_prev_incarnation is not None:
                raise RuntimeError("v0.44 sole FREE destination retains predecessor")
            if destination["next_descriptor_page"] is not None:
                raise RuntimeError("v0.44 sole FREE destination unexpectedly has successor")
            _destination_old_prev, destination_prev_slot, reads = QueuedPredecessorIO.read(
                predecessor_fd,
                int(free_page),
                committed_epoch,
                expected_incarnation=int(free_incarnation),
            )
            predecessor_preads += reads

            next_meta = dict(meta)
            destination_new_incarnation = self._take_incarnation(next_meta)
            new_epoch = committed_epoch + 1

            extra_reads, writes = TaggedDescriptorIO.write_existing(
                arena_fd,
                int(free_page),
                committed_epoch,
                new_epoch,
                committed_slot=destination_slot,
                incarnation=destination_new_incarnation,
                status=RETIREMENT_STATUS_QUEUED,
                generation=int(tail["generation"]),
                cursor_header_page=int(tail["cursor_header_page"]),
                remaining_segments=int(tail["remaining_segments"]),
                next_descriptor_page=int(successor_page),
                next_descriptor_incarnation=int(successor_incarnation),
            )
            descriptor_preads += extra_reads
            descriptor_pwrites += writes
            if failpoint is not None:
                failpoint("retirement_biqueue_destination_staged")

            extra_reads, writes = TaggedDescriptorIO.write_existing(
                arena_fd,
                int(predecessor_page),
                committed_epoch,
                new_epoch,
                committed_slot=predecessor_slot,
                incarnation=int(predecessor["incarnation"]),
                status=RETIREMENT_STATUS_QUEUED,
                generation=int(predecessor["generation"]),
                cursor_header_page=int(predecessor["cursor_header_page"]),
                remaining_segments=int(predecessor["remaining_segments"]),
                next_descriptor_page=int(free_page),
                next_descriptor_incarnation=destination_new_incarnation,
            )
            descriptor_preads += extra_reads
            descriptor_pwrites += writes
            if failpoint is not None:
                failpoint("retirement_biqueue_predecessor_staged")

            predecessor_pwrites += QueuedPredecessorIO.write_existing(
                predecessor_fd,
                int(free_page),
                new_epoch,
                committed_slot=destination_prev_slot,
                incarnation=destination_new_incarnation,
                prev_page=int(predecessor_page),
                prev_incarnation=int(predecessor_incarnation),
            )
            if failpoint is not None:
                failpoint("retirement_biqueue_destination_prev_staged")

            predecessor_pwrites += QueuedPredecessorIO.write_existing(
                predecessor_fd,
                int(successor_page),
                new_epoch,
                committed_slot=successor_prev_slot,
                incarnation=int(successor_incarnation),
                prev_page=int(free_page),
                prev_incarnation=destination_new_incarnation,
            )
            if failpoint is not None:
                failpoint("retirement_biqueue_successor_prev_staged")

            next_meta["retirement_queue_tail_predecessor_page"] = int(free_page)
            next_meta["retirement_queue_tail_predecessor_incarnation"] = destination_new_incarnation
            next_meta["retirement_descriptor_free_head_page"] = None
            next_meta["retirement_descriptor_free_head_incarnation"] = None
            next_meta["retirement_descriptor_free_count"] = 0
            next_meta["retirement_descriptor_arena_pages"] = physical_tail_base
            next_meta["retirement_physical_tail_page"] = int(predecessor_page)
            next_meta["retirement_physical_tail_incarnation"] = int(predecessor_incarnation)
            next_meta["retirement_physical_tail_predecessor_page"] = int(pp_page)
            next_meta["retirement_physical_tail_predecessor_incarnation"] = int(pp_incarnation)
            next_meta["retirement_physical_tail_predecessor_predecessor_page"] = None
            next_meta["retirement_physical_tail_predecessor_predecessor_incarnation"] = None

            os.fsync(arena_fd)
            os.fsync(predecessor_fd)
            if failpoint is not None:
                failpoint("retirement_biqueue_dependencies_synced")

            self._write_super(fd, committed_epoch, new_epoch, next_meta)
            os.fsync(fd)
            if failpoint is not None:
                failpoint("committed")
                failpoint("retirement_biqueue_relocation_committed")

            target_bytes = physical_tail_base * PAGE_SIZE
            os.ftruncate(arena_fd, target_bytes)
            if failpoint is not None:
                failpoint("retirement_arena_truncated")
            os.ftruncate(predecessor_fd, target_bytes)
            if failpoint is not None:
                failpoint("retirement_queued_predecessor_truncated")
            os.fsync(arena_fd)
            os.fsync(predecessor_fd)
            if failpoint is not None:
                failpoint("retirement_biqueue_relocation_synced")

            return BidirectionalQueuedTailEvacuationTrace(
                committed_epoch_before=committed_epoch,
                committed_epoch_after=new_epoch,
                retirement_queue_count=queue_count,
                retirement_descriptor_free_count_before=free_count_before,
                retirement_descriptor_free_count_after=0,
                retirement_arena_pages_before=arena_pages_before,
                retirement_arena_pages_after=physical_tail_base,
                retirement_arena_pages_released=RETIREMENT_DESCRIPTOR_COPIES,
                retirement_arena_bytes_released=RETIREMENT_DESCRIPTOR_COPIES * PAGE_SIZE,
                retirement_descriptor_preads=descriptor_preads,
                retirement_descriptor_pwrites=descriptor_pwrites,
                queued_predecessor_preads=predecessor_preads,
                queued_predecessor_pwrites=predecessor_pwrites,
                retirement_descriptors_scanned=0,
                queued_predecessors_scanned=0,
                live_descriptor_relocations=1,
                physical_tail_page=physical_tail_base,
                physical_tail_incarnation=int(physical_incarnation),
                predecessor_page=int(predecessor_page),
                predecessor_incarnation=int(predecessor_incarnation),
                predecessor_predecessor_page=int(pp_page),
                predecessor_predecessor_incarnation=int(pp_incarnation),
                successor_page=int(successor_page),
                successor_incarnation=int(successor_incarnation),
                destination_page=int(free_page),
                destination_old_incarnation=int(destination["incarnation"]),
                destination_new_incarnation=destination_new_incarnation,
                new_physical_tail_page=int(predecessor_page),
                new_physical_tail_incarnation=int(predecessor_incarnation),
                new_physical_tail_predecessor_page=int(pp_page),
                new_physical_tail_predecessor_incarnation=int(pp_incarnation),
                retirement_arena_fsyncs=2,
                queued_predecessor_fsyncs=2,
            )
        finally:
            os.close(predecessor_fd)
            os.close(arena_fd)
            os.close(fd)

    def recover(self) -> dict[str, Any]:
        primary = dict(super().recover())
        fd = self._open()
        try:
            epoch, meta, _slot = self._read_super(fd)
            target_bytes = self._committed_arena_pages(meta) * PAGE_SIZE
        finally:
            os.close(fd)

        if not self.predecessor_path.exists():
            if target_bytes:
                raise RuntimeError("committed v0.44 queued predecessor sidecar is missing")
            predecessor_fd = os.open(
                self.predecessor_path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600
            )
        else:
            predecessor_fd = self._open_predecessors()
        try:
            before = os.fstat(predecessor_fd).st_size
            if before < target_bytes:
                raise RuntimeError("v0.44 queued predecessor sidecar is shorter than committed frontier")
            if before > target_bytes:
                os.ftruncate(predecessor_fd, target_bytes)
                os.fsync(predecessor_fd)
            after = os.fstat(predecessor_fd).st_size
        finally:
            os.close(predecessor_fd)

        primary.update(
            {
                "committed_epoch": int(epoch),
                "queued_predecessors_scanned": 0,
                "queued_predecessor_sidecar_target_bytes": target_bytes,
                "queued_predecessor_sidecar_tail_before_bytes": max(0, before - target_bytes),
                "queued_predecessor_sidecar_tail_after_bytes": max(0, after - target_bytes),
                "queued_predecessor_sidecar_truncated_bytes": max(0, before - after),
            }
        )
        return primary


LiveTailEvacuationRetirementDescriptorPrimaryStore = (
    BidirectionalQueuedRetirementDescriptorPrimaryStore
)
