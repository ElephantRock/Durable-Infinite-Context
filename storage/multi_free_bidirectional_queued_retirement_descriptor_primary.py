from __future__ import annotations

import os
from typing import Callable

from storage.bidirectional_queued_retirement_descriptor_primary import (
    BidirectionalQueuedRetirementDescriptorPrimaryStore,
    BidirectionalQueuedTailEvacuationTrace,
)
from storage.fixed_page_primary import PAGE_SIZE
from storage.queued_predecessor_index import QueuedPredecessorIO
from storage.retirement_descriptor_pool import (
    RETIREMENT_DESCRIPTOR_COPIES,
    RETIREMENT_STATUS_FREE,
    RETIREMENT_STATUS_QUEUED,
    TaggedDescriptorIO,
)


class MultiFreeBidirectionalQueuedRetirementDescriptorPrimaryStore(
    BidirectionalQueuedRetirementDescriptorPrimaryStore
):
    """v0.45 composition of v0.44 QUEUED-prev and v0.41 FREE-head repair.

    The claim-bearing geometry is the v0.44 deeper interior live physical-tail
    relocation, except the lower relocation destination is the head of a
    non-singleton FREE chain. Consuming that destination additionally requires a
    direct rewrite of its FREE successor so the new FREE head has null predecessor.
    No queue walk, free-chain walk, descriptor-history scan, or relocation search is
    permitted.
    """

    ARENA_SUFFIX = ".retire45"
    PREDECESSOR_SUFFIX = ".retire45prev"

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
            next_meta = dict(meta)
            next_meta["format"] = 45
            os.pwrite(
                fd,
                self._pack_record(self._super_magic(), epoch, next_meta),
                self._super_offset(slot),
            )
            os.fsync(fd)
        finally:
            os.close(fd)

    def evacuate_multi_free_bidirectional_queued_live_retirement_arena_tail_step(
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
                or free_count_before < 2
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
            descriptor_pwrites = 0
            predecessor_preads = 0
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
                raise RuntimeError("v0.45 physical tail lacks tagged QUEUED predecessor")

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
                raise RuntimeError("v0.45 predecessor does not reference physical tail")
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
                raise RuntimeError("v0.45 deeper physical predecessor lacks predecessor")
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
                raise RuntimeError("v0.45 predecessor-predecessor forward edge is stale")

            successor_page = tail["next_descriptor_page"]
            successor_incarnation = tail["next_descriptor_incarnation"]
            if successor_page is None or successor_incarnation is None:
                raise RuntimeError("v0.45 interior physical tail lacks successor")
            if (
                meta.get("retirement_queue_tail_page") != successor_page
                or meta.get("retirement_queue_tail_incarnation") != successor_incarnation
            ):
                raise RuntimeError("v0.45 bounded target requires physical-tail successor as logical tail")
            successor, _successor_slot, reads = TaggedDescriptorIO.read(
                arena_fd,
                int(successor_page),
                committed_epoch,
                expected_incarnation=int(successor_incarnation),
                expected_status=RETIREMENT_STATUS_QUEUED,
            )
            descriptor_preads += reads
            if successor["next_descriptor_page"] is not None:
                raise RuntimeError("v0.45 logical queue tail unexpectedly has successor")
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
                raise RuntimeError("v0.45 committed FREE head retains predecessor")

            free_successor_page = destination["next_descriptor_page"]
            free_successor_incarnation = destination["next_descriptor_incarnation"]
            if free_successor_page is None or free_successor_incarnation is None:
                raise RuntimeError("v0.45 multiple-FREE destination lacks successor")
            if int(free_successor_page) >= physical_tail_base:
                raise RuntimeError("v0.45 new FREE head lies beyond retained arena frontier")
            free_successor, free_successor_slot, reads = TaggedDescriptorIO.read(
                arena_fd,
                int(free_successor_page),
                committed_epoch,
                expected_incarnation=int(free_successor_incarnation),
                expected_status=RETIREMENT_STATUS_FREE,
            )
            descriptor_preads += reads
            free_successor_prev_page, free_successor_prev_incarnation = self._free_prev(
                free_successor
            )
            if (
                free_successor_prev_page != int(free_page)
                or free_successor_prev_incarnation != int(free_incarnation)
            ):
                raise RuntimeError("v0.45 FREE successor does not reference current FREE head")

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
                failpoint("retirement_mfbq_destination_staged")

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
                failpoint("retirement_mfbq_predecessor_staged")

            extra_reads, writes = TaggedDescriptorIO.write_existing(
                arena_fd,
                int(free_successor_page),
                committed_epoch,
                new_epoch,
                committed_slot=free_successor_slot,
                **self._free_write_payload(
                    free_successor,
                    prev_page=None,
                    prev_incarnation=None,
                ),
            )
            descriptor_preads += extra_reads
            descriptor_pwrites += writes
            if failpoint is not None:
                failpoint("retirement_mfbq_free_successor_staged")

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
                failpoint("retirement_mfbq_destination_prev_staged")

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
                failpoint("retirement_mfbq_successor_prev_staged")

            next_meta["retirement_queue_tail_predecessor_page"] = int(free_page)
            next_meta["retirement_queue_tail_predecessor_incarnation"] = destination_new_incarnation
            next_meta["retirement_descriptor_free_head_page"] = int(free_successor_page)
            next_meta["retirement_descriptor_free_head_incarnation"] = int(
                free_successor_incarnation
            )
            next_meta["retirement_descriptor_free_count"] = free_count_before - 1
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
                failpoint("retirement_mfbq_dependencies_synced")

            self._write_super(fd, committed_epoch, new_epoch, next_meta)
            os.fsync(fd)
            if failpoint is not None:
                failpoint("committed")
                failpoint("retirement_mfbq_relocation_committed")

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
                failpoint("retirement_mfbq_relocation_synced")

            return BidirectionalQueuedTailEvacuationTrace(
                committed_epoch_before=committed_epoch,
                committed_epoch_after=new_epoch,
                retirement_queue_count=queue_count,
                retirement_descriptor_free_count_before=free_count_before,
                retirement_descriptor_free_count_after=free_count_before - 1,
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


LiveTailEvacuationRetirementDescriptorPrimaryStore = (
    MultiFreeBidirectionalQueuedRetirementDescriptorPrimaryStore
)
