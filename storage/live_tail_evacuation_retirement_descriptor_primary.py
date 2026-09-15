from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from storage.bidirectional_retirement_descriptor_primary import (
    BidirectionalRetirementDescriptorPrimaryStore,
)
from storage.fixed_page_primary import PAGE_SIZE
from storage.retirement_descriptor_pool import (
    RETIREMENT_DESCRIPTOR_COPIES,
    RETIREMENT_STATUS_FREE,
    RETIREMENT_STATUS_QUEUED,
    TaggedDescriptorIO,
)


@dataclass(frozen=True)
class LiveTailEvacuationTrace:
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
    retirement_descriptors_scanned: int
    live_descriptor_relocations: int
    tail_page: int | None
    tail_incarnation: int | None
    tail_was_queued: bool
    tail_was_queue_tail: bool
    predecessor_page: int | None
    predecessor_incarnation: int | None
    destination_page: int | None
    destination_old_incarnation: int | None
    destination_new_incarnation: int | None
    retirement_arena_fsyncs: int

    @property
    def released(self) -> bool:
        return self.retirement_arena_pages_released > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "committed_epoch_before": self.committed_epoch_before,
            "committed_epoch_after": self.committed_epoch_after,
            "retirement_queue_count": self.retirement_queue_count,
            "retirement_descriptor_free_count_before": self.retirement_descriptor_free_count_before,
            "retirement_descriptor_free_count_after": self.retirement_descriptor_free_count_after,
            "retirement_arena_pages_before": self.retirement_arena_pages_before,
            "retirement_arena_pages_after": self.retirement_arena_pages_after,
            "retirement_arena_pages_released": self.retirement_arena_pages_released,
            "retirement_arena_bytes_released": self.retirement_arena_bytes_released,
            "retirement_descriptor_preads": self.retirement_descriptor_preads,
            "retirement_descriptor_pwrites": self.retirement_descriptor_pwrites,
            "retirement_descriptors_scanned": self.retirement_descriptors_scanned,
            "live_descriptor_relocations": self.live_descriptor_relocations,
            "tail_page": self.tail_page,
            "tail_incarnation": self.tail_incarnation,
            "tail_was_queued": self.tail_was_queued,
            "tail_was_queue_tail": self.tail_was_queue_tail,
            "predecessor_page": self.predecessor_page,
            "predecessor_incarnation": self.predecessor_incarnation,
            "destination_page": self.destination_page,
            "destination_old_incarnation": self.destination_old_incarnation,
            "destination_new_incarnation": self.destination_new_incarnation,
            "retirement_arena_fsyncs": self.retirement_arena_fsyncs,
            "released": self.released,
        }


class LiveTailEvacuationRetirementDescriptorPrimaryStore(
    BidirectionalRetirementDescriptorPrimaryStore
):
    """v0.40 bounded live physical queue-tail evacuation experiment.

    v0.39 can reclaim a directly addressed physical tail only while that descriptor is
    FREE. v0.40 tests the next narrower obstruction: the physical descriptor tail is a
    live QUEUED descriptor, is also the logical queue tail, and at least one lower
    descriptor pair is free.

    The queue tail has no successor. The only queue edge that must be discovered before
    moving it is therefore its predecessor. v0.40 keeps that one tagged predecessor as
    primary-superblock current-state authority whenever queue depth is at least two.
    Enqueue can maintain it in O(1): the previous queue tail is exactly the predecessor
    of the newly appended tail. Head reclamation does not change this authority while
    depth remains at least two; the 2 -> 1 transition makes it irrelevant, and the next
    enqueue overwrites it from the then-current sole tail.

    Evacuation consumes the current FREE-list head as a lower destination, rewrites the
    named queue predecessor to point to the relocated descriptor, publishes the new
    queue-tail identity plus a shorter arena frontier, then truncates the old physical
    tail pair as derived cleanup. The candidate deliberately does not solve a live
    physical tail that is in the middle of the queue or destination discovery outside
    the current free-list head.
    """

    ARENA_SUFFIX = ".retire40"

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
        super().initialize(
            initial_capacity=initial_capacity,
            max_load=max_load,
            bucket_size=bucket_size,
            max_kicks=max_kicks,
            stash_capacity=stash_capacity,
            migration_slot_budget=migration_slot_budget,
            force_same_pair=force_same_pair,
        )
        fd = self._open()
        try:
            epoch, meta, slot = self._read_super(fd)
            meta = dict(meta)
            meta.update(
                {
                    "format": 40,
                    "retirement_queue_tail_predecessor_page": None,
                    "retirement_queue_tail_predecessor_incarnation": None,
                }
            )
            os.pwrite(
                fd,
                self._pack_record(self._super_magic(), epoch, meta),
                self._super_offset(slot),
            )
            os.fsync(fd)
        finally:
            os.close(fd)

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
        old_count = int(tx.meta.get("retirement_queue_count", 0))
        old_tail_page = tx.meta.get("retirement_queue_tail_page")
        old_tail_incarnation = tx.meta.get("retirement_queue_tail_incarnation")
        if old_count == 0:
            if old_tail_page is not None or old_tail_incarnation is not None:
                raise RuntimeError("empty retirement queue retains a tail identity")
        elif old_tail_page is None or old_tail_incarnation is None:
            raise RuntimeError("non-empty retirement queue lacks a tagged tail identity")

        super()._enqueue_retirement_tx(
            tx,
            generation=generation,
            owner_head_page=owner_head_page,
            owner_count=owner_count,
        )

        if old_count == 0:
            tx.meta["retirement_queue_tail_predecessor_page"] = None
            tx.meta["retirement_queue_tail_predecessor_incarnation"] = None
        else:
            tx.meta["retirement_queue_tail_predecessor_page"] = int(old_tail_page)
            tx.meta["retirement_queue_tail_predecessor_incarnation"] = int(
                old_tail_incarnation
            )

    def retirement_queue_snapshot(self) -> dict[str, Any]:
        row = super().retirement_queue_snapshot()
        fd = self._open()
        try:
            _epoch, meta, _slot = self._read_super(fd)
            count = int(meta.get("retirement_queue_count", 0))
            if count >= 2:
                page = meta.get("retirement_queue_tail_predecessor_page")
                incarnation = meta.get("retirement_queue_tail_predecessor_incarnation")
                if page is None or incarnation is None:
                    raise RuntimeError("multi-node queue lacks tail predecessor authority")
                row["tail_predecessor_page"] = int(page)
                row["tail_predecessor_incarnation"] = int(incarnation)
            else:
                row["tail_predecessor_page"] = None
                row["tail_predecessor_incarnation"] = None
        finally:
            os.close(fd)
        return row

    @staticmethod
    def _no_release_trace(
        *,
        committed_epoch: int,
        queue_count: int,
        free_count: int,
        arena_pages: int,
        preads: int,
        tail_page: int | None,
        tail_incarnation: int | None,
        tail_was_queued: bool,
        tail_was_queue_tail: bool,
    ) -> LiveTailEvacuationTrace:
        return LiveTailEvacuationTrace(
            committed_epoch_before=committed_epoch,
            committed_epoch_after=committed_epoch,
            retirement_queue_count=queue_count,
            retirement_descriptor_free_count_before=free_count,
            retirement_descriptor_free_count_after=free_count,
            retirement_arena_pages_before=arena_pages,
            retirement_arena_pages_after=arena_pages,
            retirement_arena_pages_released=0,
            retirement_arena_bytes_released=0,
            retirement_descriptor_preads=preads,
            retirement_descriptor_pwrites=0,
            retirement_descriptors_scanned=0,
            live_descriptor_relocations=0,
            tail_page=tail_page,
            tail_incarnation=tail_incarnation,
            tail_was_queued=tail_was_queued,
            tail_was_queue_tail=tail_was_queue_tail,
            predecessor_page=None,
            predecessor_incarnation=None,
            destination_page=None,
            destination_old_incarnation=None,
            destination_new_incarnation=None,
            retirement_arena_fsyncs=0,
        )

    def evacuate_live_retirement_arena_tail_step(
        self,
        failpoint: Callable[[str], None] | None = None,
    ) -> LiveTailEvacuationTrace:
        fd = self._open()
        arena_fd = self._open_arena()
        try:
            committed_epoch, meta, _slot = self._read_super(fd)
            meta = dict(meta)
            arena_pages_before = self._committed_arena_pages(meta)
            queue_count = int(meta.get("retirement_queue_count", 0))
            free_count_before = int(meta.get("retirement_descriptor_free_count", 0))

            if arena_pages_before == 0:
                return self._no_release_trace(
                    committed_epoch=committed_epoch,
                    queue_count=queue_count,
                    free_count=free_count_before,
                    arena_pages=0,
                    preads=0,
                    tail_page=None,
                    tail_incarnation=None,
                    tail_was_queued=False,
                    tail_was_queue_tail=False,
                )
            if queue_count < 2:
                return self._no_release_trace(
                    committed_epoch=committed_epoch,
                    queue_count=queue_count,
                    free_count=free_count_before,
                    arena_pages=arena_pages_before,
                    preads=0,
                    tail_page=arena_pages_before - RETIREMENT_DESCRIPTOR_COPIES,
                    tail_incarnation=None,
                    tail_was_queued=False,
                    tail_was_queue_tail=False,
                )

            tail_base = arena_pages_before - RETIREMENT_DESCRIPTOR_COPIES
            queue_tail_page = meta.get("retirement_queue_tail_page")
            queue_tail_incarnation = meta.get("retirement_queue_tail_incarnation")
            if queue_tail_page != tail_base or queue_tail_incarnation is None:
                return self._no_release_trace(
                    committed_epoch=committed_epoch,
                    queue_count=queue_count,
                    free_count=free_count_before,
                    arena_pages=arena_pages_before,
                    preads=0,
                    tail_page=tail_base,
                    tail_incarnation=None,
                    tail_was_queued=False,
                    tail_was_queue_tail=False,
                )

            tail, tail_slot, descriptor_preads = TaggedDescriptorIO.read(
                arena_fd,
                tail_base,
                committed_epoch,
                expected_incarnation=int(queue_tail_incarnation),
                expected_status=RETIREMENT_STATUS_QUEUED,
            )
            tail_incarnation = int(tail["incarnation"])
            if tail["next_descriptor_page"] is not None or tail["next_descriptor_incarnation"] is not None:
                raise RuntimeError("committed queue tail unexpectedly has a successor")

            free_page = meta.get("retirement_descriptor_free_head_page")
            free_incarnation = meta.get("retirement_descriptor_free_head_incarnation")
            if free_count_before <= 0 or free_page is None or free_incarnation is None:
                return self._no_release_trace(
                    committed_epoch=committed_epoch,
                    queue_count=queue_count,
                    free_count=free_count_before,
                    arena_pages=arena_pages_before,
                    preads=descriptor_preads,
                    tail_page=tail_base,
                    tail_incarnation=tail_incarnation,
                    tail_was_queued=True,
                    tail_was_queue_tail=True,
                )
            if int(free_page) >= tail_base:
                return self._no_release_trace(
                    committed_epoch=committed_epoch,
                    queue_count=queue_count,
                    free_count=free_count_before,
                    arena_pages=arena_pages_before,
                    preads=descriptor_preads,
                    tail_page=tail_base,
                    tail_incarnation=tail_incarnation,
                    tail_was_queued=True,
                    tail_was_queue_tail=True,
                )

            predecessor_page = meta.get("retirement_queue_tail_predecessor_page")
            predecessor_incarnation = meta.get(
                "retirement_queue_tail_predecessor_incarnation"
            )
            if predecessor_page is None or predecessor_incarnation is None:
                raise RuntimeError("live queue tail lacks predecessor authority")

            predecessor, predecessor_slot, preads = TaggedDescriptorIO.read(
                arena_fd,
                int(predecessor_page),
                committed_epoch,
                expected_incarnation=int(predecessor_incarnation),
                expected_status=RETIREMENT_STATUS_QUEUED,
            )
            descriptor_preads += preads
            if (
                predecessor["next_descriptor_page"] != tail_base
                or predecessor["next_descriptor_incarnation"] != tail_incarnation
            ):
                raise RuntimeError("tail predecessor does not reference the physical queue tail")

            destination, destination_slot, preads = TaggedDescriptorIO.read(
                arena_fd,
                int(free_page),
                committed_epoch,
                expected_incarnation=int(free_incarnation),
                expected_status=RETIREMENT_STATUS_FREE,
            )
            descriptor_preads += preads
            destination_prev_page, destination_prev_incarnation = self._free_prev(destination)
            if destination_prev_page is not None or destination_prev_incarnation is not None:
                raise RuntimeError("committed free-list head retains a predecessor")

            next_meta = dict(meta)
            destination_new_incarnation = self._take_incarnation(next_meta)
            new_epoch = committed_epoch + 1
            descriptor_pwrites = 0

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
                next_descriptor_page=None,
                next_descriptor_incarnation=None,
            )
            descriptor_preads += extra_reads
            descriptor_pwrites += writes
            if failpoint is not None:
                failpoint("retirement_live_tail_destination_staged")

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
                failpoint("retirement_live_tail_predecessor_staged")

            next_meta["retirement_queue_tail_page"] = int(free_page)
            next_meta["retirement_queue_tail_incarnation"] = destination_new_incarnation
            next_meta["retirement_queue_tail_predecessor_page"] = int(predecessor_page)
            next_meta["retirement_queue_tail_predecessor_incarnation"] = int(
                predecessor_incarnation
            )
            next_meta["retirement_descriptor_free_head_page"] = destination[
                "next_descriptor_page"
            ]
            next_meta["retirement_descriptor_free_head_incarnation"] = destination[
                "next_descriptor_incarnation"
            ]
            next_meta["retirement_descriptor_free_count"] = free_count_before - 1
            next_meta["retirement_descriptor_arena_pages"] = tail_base
            if next_meta["retirement_descriptor_free_count"] == 0:
                if next_meta["retirement_descriptor_free_head_page"] is not None:
                    raise RuntimeError("empty free list retains a head after live-tail evacuation")

            os.fsync(arena_fd)
            if failpoint is not None:
                failpoint("retirement_live_tail_dependencies_synced")

            self._write_super(fd, committed_epoch, new_epoch, next_meta)
            os.fsync(fd)
            if failpoint is not None:
                failpoint("committed")
                failpoint("retirement_live_tail_relocation_committed")

            os.ftruncate(arena_fd, tail_base * PAGE_SIZE)
            if failpoint is not None:
                failpoint("retirement_arena_truncated")
            os.fsync(arena_fd)
            if failpoint is not None:
                failpoint("retirement_live_tail_relocation_synced")

            return LiveTailEvacuationTrace(
                committed_epoch_before=committed_epoch,
                committed_epoch_after=new_epoch,
                retirement_queue_count=queue_count,
                retirement_descriptor_free_count_before=free_count_before,
                retirement_descriptor_free_count_after=free_count_before - 1,
                retirement_arena_pages_before=arena_pages_before,
                retirement_arena_pages_after=tail_base,
                retirement_arena_pages_released=RETIREMENT_DESCRIPTOR_COPIES,
                retirement_arena_bytes_released=RETIREMENT_DESCRIPTOR_COPIES * PAGE_SIZE,
                retirement_descriptor_preads=descriptor_preads,
                retirement_descriptor_pwrites=descriptor_pwrites,
                retirement_descriptors_scanned=0,
                live_descriptor_relocations=1,
                tail_page=tail_base,
                tail_incarnation=tail_incarnation,
                tail_was_queued=True,
                tail_was_queue_tail=True,
                predecessor_page=int(predecessor_page),
                predecessor_incarnation=int(predecessor_incarnation),
                destination_page=int(free_page),
                destination_old_incarnation=int(destination["incarnation"]),
                destination_new_incarnation=destination_new_incarnation,
                retirement_arena_fsyncs=2,
            )
        finally:
            os.close(arena_fd)
            os.close(fd)
