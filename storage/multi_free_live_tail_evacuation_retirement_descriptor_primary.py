from __future__ import annotations

import os
from typing import Callable

from storage.fixed_page_primary import PAGE_SIZE
from storage.live_tail_evacuation_retirement_descriptor_primary import (
    LiveTailEvacuationRetirementDescriptorPrimaryStore as V040LiveTailEvacuationStore,
    LiveTailEvacuationTrace,
)
from storage.retirement_descriptor_pool import (
    RETIREMENT_DESCRIPTOR_COPIES,
    RETIREMENT_STATUS_FREE,
    RETIREMENT_STATUS_QUEUED,
    TaggedDescriptorIO,
)


class MultiFreeLiveTailEvacuationRetirementDescriptorPrimaryStore(
    V040LiveTailEvacuationStore
):
    """v0.41 bounded multiple-FREE live queue-tail evacuation experiment.

    v0.40 proves the first live-tail relocation geometry only when the relocation
    destination is the sole committed FREE descriptor. If the free-list head has a
    successor, consuming that head as the QUEUED relocation destination must also
    clear the successor's predecessor before publishing it as the new free-list head.

    v0.41 performs exactly that additional current-topology repair. The destination
    already names its successor, and v0.39 FREE topology requires that successor to
    name the destination as predecessor. The relocation therefore reads at most one
    additional dual-copy descriptor and rewrites that one neighbor. No free-list walk,
    queue walk, descriptor-history scan, or relocation-candidate search is required.

    The experiment still requires the physical descriptor tail to be the logical queue
    tail. A live physical tail in the middle of the queue remains outside this claim.
    """

    ARENA_SUFFIX = ".retire41"

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
            meta["format"] = 41
            os.pwrite(
                fd,
                self._pack_record(self._super_magic(), epoch, meta),
                self._super_offset(slot),
            )
            os.fsync(fd)
        finally:
            os.close(fd)

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

            tail, _tail_slot, descriptor_preads = TaggedDescriptorIO.read(
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

            successor_page = destination["next_descriptor_page"]
            successor_incarnation = destination["next_descriptor_incarnation"]
            successor = None
            successor_slot = None
            if free_count_before == 1:
                if successor_page is not None or successor_incarnation is not None:
                    raise RuntimeError("sole FREE descriptor unexpectedly has a successor")
            else:
                if successor_page is None or successor_incarnation is None:
                    raise RuntimeError("multiple-FREE list lacks successor identity")
                if int(successor_page) >= tail_base:
                    raise RuntimeError("new free-list head would lie beyond retained arena frontier")
                successor, successor_slot, preads = TaggedDescriptorIO.read(
                    arena_fd,
                    int(successor_page),
                    committed_epoch,
                    expected_incarnation=int(successor_incarnation),
                    expected_status=RETIREMENT_STATUS_FREE,
                )
                descriptor_preads += preads
                successor_prev_page, successor_prev_incarnation = self._free_prev(successor)
                if (
                    successor_prev_page != int(free_page)
                    or successor_prev_incarnation != int(free_incarnation)
                ):
                    raise RuntimeError("free-head successor does not reference current free head")

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

            if successor is not None:
                if successor_slot is None or successor_page is None or successor_incarnation is None:
                    raise RuntimeError("free successor staging lacks tagged identity")
                extra_reads, writes = TaggedDescriptorIO.write_existing(
                    arena_fd,
                    int(successor_page),
                    committed_epoch,
                    new_epoch,
                    committed_slot=successor_slot,
                    **self._free_write_payload(
                        successor,
                        prev_page=None,
                        prev_incarnation=None,
                    ),
                )
                descriptor_preads += extra_reads
                descriptor_pwrites += writes
                if failpoint is not None:
                    failpoint("retirement_live_tail_free_successor_staged")

            next_meta["retirement_queue_tail_page"] = int(free_page)
            next_meta["retirement_queue_tail_incarnation"] = destination_new_incarnation
            next_meta["retirement_queue_tail_predecessor_page"] = int(predecessor_page)
            next_meta["retirement_queue_tail_predecessor_incarnation"] = int(
                predecessor_incarnation
            )
            next_meta["retirement_descriptor_free_head_page"] = successor_page
            next_meta["retirement_descriptor_free_head_incarnation"] = successor_incarnation
            next_meta["retirement_descriptor_free_count"] = free_count_before - 1
            next_meta["retirement_descriptor_arena_pages"] = tail_base
            if next_meta["retirement_descriptor_free_count"] == 0:
                if (
                    next_meta["retirement_descriptor_free_head_page"] is not None
                    or next_meta["retirement_descriptor_free_head_incarnation"] is not None
                ):
                    raise RuntimeError("empty free list retains a head after live-tail evacuation")
            else:
                if (
                    next_meta["retirement_descriptor_free_head_page"] is None
                    or next_meta["retirement_descriptor_free_head_incarnation"] is None
                ):
                    raise RuntimeError("non-empty free list lost its head after live-tail evacuation")

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


LiveTailEvacuationRetirementDescriptorPrimaryStore = (
    MultiFreeLiveTailEvacuationRetirementDescriptorPrimaryStore
)
