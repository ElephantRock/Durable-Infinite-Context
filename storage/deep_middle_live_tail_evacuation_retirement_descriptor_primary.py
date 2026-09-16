from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable

from storage.fixed_page_primary import PAGE_SIZE
from storage.middle_live_tail_evacuation_retirement_descriptor_primary import (
    MiddleLiveTailEvacuationRetirementDescriptorPrimaryStore,
)
from storage.retirement_descriptor_pool import (
    RETIREMENT_DESCRIPTOR_COPIES,
    RETIREMENT_STATUS_FREE,
    RETIREMENT_STATUS_QUEUED,
    TaggedDescriptorIO,
)


@dataclass(frozen=True)
class DeepMiddleLiveTailEvacuationTrace:
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

    @property
    def released(self) -> bool:
        return self.retirement_arena_pages_released > 0

    def to_dict(self) -> dict[str, Any]:
        row = dict(self.__dict__)
        row["released"] = self.released
        return row


class DeepMiddleLiveTailEvacuationRetirementDescriptorPrimaryStore(
    MiddleLiveTailEvacuationRetirementDescriptorPrimaryStore
):
    """v0.43 one-extra-reverse-hop interior live-tail experiment.

    v0.42 can relocate an interior live physical tail only when its predecessor is the
    queue head. v0.43 adds one more tagged current-state fact: the predecessor of the
    physical-tail predecessor. That is sufficient for the next bounded geometry where
    the live queue is [PP, P, T, S], T is the physical tail, S is the logical tail,
    and one lower FREE destination exists.

    The candidate deliberately stops there. It does not encode an arbitrary reverse
    chain and refuses deeper prefixes before descriptor I/O.
    """

    ARENA_SUFFIX = ".retire43"

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
                    "format": 43,
                    "retirement_physical_tail_predecessor_predecessor_page": None,
                    "retirement_physical_tail_predecessor_predecessor_incarnation": None,
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
        old_free_count = int(tx.meta.get("retirement_descriptor_free_count", 0))
        old_tail_predecessor_page = tx.meta.get("retirement_queue_tail_predecessor_page")
        old_tail_predecessor_incarnation = tx.meta.get(
            "retirement_queue_tail_predecessor_incarnation"
        )
        old_pp_page = tx.meta.get(
            "retirement_physical_tail_predecessor_predecessor_page"
        )
        old_pp_incarnation = tx.meta.get(
            "retirement_physical_tail_predecessor_predecessor_incarnation"
        )

        if old_count >= 2 and (
            old_tail_predecessor_page is None or old_tail_predecessor_incarnation is None
        ):
            raise RuntimeError("v0.43 multi-node queue lacks tail predecessor authority")

        super()._enqueue_retirement_tx(
            tx,
            generation=generation,
            owner_head_page=owner_head_page,
            owner_count=owner_count,
        )

        if old_count == 0:
            tx.meta["retirement_physical_tail_predecessor_predecessor_page"] = None
            tx.meta["retirement_physical_tail_predecessor_predecessor_incarnation"] = None
        elif old_free_count == 0:
            # A new descriptor was appended at the physical frontier and is therefore
            # both the new logical and physical tail. Its predecessor is the old
            # logical tail; the old logical-tail predecessor is the next reverse hop.
            if old_count >= 2:
                tx.meta["retirement_physical_tail_predecessor_predecessor_page"] = int(
                    old_tail_predecessor_page
                )
                tx.meta[
                    "retirement_physical_tail_predecessor_predecessor_incarnation"
                ] = int(old_tail_predecessor_incarnation)
            else:
                tx.meta["retirement_physical_tail_predecessor_predecessor_page"] = None
                tx.meta[
                    "retirement_physical_tail_predecessor_predecessor_incarnation"
                ] = None
        else:
            # FREE-head reuse occurs below the physical frontier. Preserve the reverse
            # window for the unchanged physical tail.
            tx.meta["retirement_physical_tail_predecessor_predecessor_page"] = (
                None if old_pp_page is None else int(old_pp_page)
            )
            tx.meta["retirement_physical_tail_predecessor_predecessor_incarnation"] = (
                None if old_pp_incarnation is None else int(old_pp_incarnation)
            )

    def retirement_queue_snapshot(self) -> dict[str, Any]:
        row = super().retirement_queue_snapshot()
        fd = self._open()
        try:
            _epoch, meta, _slot = self._read_super(fd)
            row["physical_tail_predecessor_predecessor_page"] = meta.get(
                "retirement_physical_tail_predecessor_predecessor_page"
            )
            row["physical_tail_predecessor_predecessor_incarnation"] = meta.get(
                "retirement_physical_tail_predecessor_predecessor_incarnation"
            )
        finally:
            os.close(fd)
        return row

    @staticmethod
    def _no_deep_release_trace(
        *,
        committed_epoch: int,
        queue_count: int,
        free_count: int,
        arena_pages: int,
        preads: int = 0,
        physical_tail_page: int | None = None,
        physical_tail_incarnation: int | None = None,
    ) -> DeepMiddleLiveTailEvacuationTrace:
        return DeepMiddleLiveTailEvacuationTrace(
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
        )

    def evacuate_deep_middle_live_retirement_arena_tail_step(
        self,
        failpoint: Callable[[str], None] | None = None,
    ) -> DeepMiddleLiveTailEvacuationTrace:
        fd = self._open()
        arena_fd = self._open_arena()
        try:
            committed_epoch, meta, _slot = self._read_super(fd)
            meta = dict(meta)
            arena_pages_before = self._committed_arena_pages(meta)
            queue_count = int(meta.get("retirement_queue_count", 0))
            free_count_before = int(meta.get("retirement_descriptor_free_count", 0))
            if (
                arena_pages_before < RETIREMENT_DESCRIPTOR_COPIES
                or queue_count != 4
                or free_count_before != 1
            ):
                return self._no_deep_release_trace(
                    committed_epoch=committed_epoch,
                    queue_count=queue_count,
                    free_count=free_count_before,
                    arena_pages=arena_pages_before,
                )

            physical_tail_base = arena_pages_before - RETIREMENT_DESCRIPTOR_COPIES
            physical_page = meta.get("retirement_physical_tail_page")
            physical_incarnation = meta.get("retirement_physical_tail_incarnation")
            predecessor_page = meta.get("retirement_physical_tail_predecessor_page")
            predecessor_incarnation = meta.get(
                "retirement_physical_tail_predecessor_incarnation"
            )
            pp_page = meta.get(
                "retirement_physical_tail_predecessor_predecessor_page"
            )
            pp_incarnation = meta.get(
                "retirement_physical_tail_predecessor_predecessor_incarnation"
            )
            if (
                physical_page != physical_tail_base
                or physical_incarnation is None
                or predecessor_page is None
                or predecessor_incarnation is None
                or pp_page is None
                or pp_incarnation is None
            ):
                return self._no_deep_release_trace(
                    committed_epoch=committed_epoch,
                    queue_count=queue_count,
                    free_count=free_count_before,
                    arena_pages=arena_pages_before,
                    physical_tail_page=physical_tail_base,
                    physical_tail_incarnation=(
                        None if physical_incarnation is None else int(physical_incarnation)
                    ),
                )

            queue_head_page = meta.get("retirement_queue_head_page")
            queue_head_incarnation = meta.get("retirement_queue_head_incarnation")
            queue_tail_page = meta.get("retirement_queue_tail_page")
            queue_tail_incarnation = meta.get("retirement_queue_tail_incarnation")
            if (
                pp_page != queue_head_page
                or pp_incarnation != queue_head_incarnation
                or predecessor_page == queue_head_page
                or queue_tail_page == physical_tail_base
            ):
                return self._no_deep_release_trace(
                    committed_epoch=committed_epoch,
                    queue_count=queue_count,
                    free_count=free_count_before,
                    arena_pages=arena_pages_before,
                    physical_tail_page=physical_tail_base,
                    physical_tail_incarnation=int(physical_incarnation),
                )

            tail, _tail_slot, descriptor_preads = TaggedDescriptorIO.read(
                arena_fd,
                physical_tail_base,
                committed_epoch,
                expected_incarnation=int(physical_incarnation),
                expected_status=RETIREMENT_STATUS_QUEUED,
            )
            successor_page = tail["next_descriptor_page"]
            successor_incarnation = tail["next_descriptor_incarnation"]
            if successor_page is None or successor_incarnation is None:
                raise RuntimeError("v0.43 interior physical tail lacks successor")
            if (
                queue_tail_page != successor_page
                or queue_tail_incarnation != successor_incarnation
            ):
                raise RuntimeError("v0.43 bounded geometry requires successor to be queue tail")

            predecessor, predecessor_slot, preads = TaggedDescriptorIO.read(
                arena_fd,
                int(predecessor_page),
                committed_epoch,
                expected_incarnation=int(predecessor_incarnation),
                expected_status=RETIREMENT_STATUS_QUEUED,
            )
            descriptor_preads += preads
            if (
                predecessor["next_descriptor_page"] != physical_tail_base
                or predecessor["next_descriptor_incarnation"] != int(physical_incarnation)
            ):
                raise RuntimeError("v0.43 predecessor does not reference physical tail")

            pp, _pp_slot, preads = TaggedDescriptorIO.read(
                arena_fd,
                int(pp_page),
                committed_epoch,
                expected_incarnation=int(pp_incarnation),
                expected_status=RETIREMENT_STATUS_QUEUED,
            )
            descriptor_preads += preads
            if (
                pp["next_descriptor_page"] != int(predecessor_page)
                or pp["next_descriptor_incarnation"] != int(predecessor_incarnation)
            ):
                raise RuntimeError("v0.43 predecessor-predecessor authority is stale")

            successor, _successor_slot, preads = TaggedDescriptorIO.read(
                arena_fd,
                int(successor_page),
                committed_epoch,
                expected_incarnation=int(successor_incarnation),
                expected_status=RETIREMENT_STATUS_QUEUED,
            )
            descriptor_preads += preads
            if (
                successor["next_descriptor_page"] is not None
                or successor["next_descriptor_incarnation"] is not None
            ):
                raise RuntimeError("v0.43 logical queue tail unexpectedly has successor")

            free_page = meta.get("retirement_descriptor_free_head_page")
            free_incarnation = meta.get("retirement_descriptor_free_head_incarnation")
            if free_page is None or free_incarnation is None or int(free_page) >= physical_tail_base:
                return self._no_deep_release_trace(
                    committed_epoch=committed_epoch,
                    queue_count=queue_count,
                    free_count=free_count_before,
                    arena_pages=arena_pages_before,
                    preads=descriptor_preads,
                    physical_tail_page=physical_tail_base,
                    physical_tail_incarnation=int(physical_incarnation),
                )

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
                raise RuntimeError("v0.43 sole FREE destination retains predecessor")
            if (
                destination["next_descriptor_page"] is not None
                or destination["next_descriptor_incarnation"] is not None
            ):
                raise RuntimeError("v0.43 sole FREE destination unexpectedly has successor")

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
                next_descriptor_page=int(successor_page),
                next_descriptor_incarnation=int(successor_incarnation),
            )
            descriptor_preads += extra_reads
            descriptor_pwrites += writes
            if failpoint is not None:
                failpoint("retirement_deep_tail_destination_staged")

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
                failpoint("retirement_deep_tail_predecessor_staged")

            next_meta["retirement_queue_tail_predecessor_page"] = int(free_page)
            next_meta["retirement_queue_tail_predecessor_incarnation"] = (
                destination_new_incarnation
            )
            next_meta["retirement_descriptor_free_head_page"] = None
            next_meta["retirement_descriptor_free_head_incarnation"] = None
            next_meta["retirement_descriptor_free_count"] = 0
            next_meta["retirement_descriptor_arena_pages"] = physical_tail_base
            next_meta["retirement_physical_tail_page"] = int(predecessor_page)
            next_meta["retirement_physical_tail_incarnation"] = int(predecessor_incarnation)
            next_meta["retirement_physical_tail_predecessor_page"] = int(pp_page)
            next_meta["retirement_physical_tail_predecessor_incarnation"] = int(pp_incarnation)
            next_meta["retirement_physical_tail_predecessor_predecessor_page"] = None
            next_meta[
                "retirement_physical_tail_predecessor_predecessor_incarnation"
            ] = None

            os.fsync(arena_fd)
            if failpoint is not None:
                failpoint("retirement_deep_tail_dependencies_synced")

            self._write_super(fd, committed_epoch, new_epoch, next_meta)
            os.fsync(fd)
            if failpoint is not None:
                failpoint("committed")
                failpoint("retirement_deep_tail_relocation_committed")

            os.ftruncate(arena_fd, physical_tail_base * PAGE_SIZE)
            if failpoint is not None:
                failpoint("retirement_arena_truncated")
            os.fsync(arena_fd)
            if failpoint is not None:
                failpoint("retirement_deep_tail_relocation_synced")

            return DeepMiddleLiveTailEvacuationTrace(
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
                retirement_descriptors_scanned=0,
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
            )
        finally:
            os.close(arena_fd)
            os.close(fd)


LiveTailEvacuationRetirementDescriptorPrimaryStore = (
    DeepMiddleLiveTailEvacuationRetirementDescriptorPrimaryStore
)
