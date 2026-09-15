from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable

from storage.fixed_page_primary import PAGE_SIZE
from storage.multi_free_live_tail_evacuation_retirement_descriptor_primary import (
    MultiFreeLiveTailEvacuationRetirementDescriptorPrimaryStore,
)
from storage.retirement_descriptor_pool import (
    RETIREMENT_DESCRIPTOR_COPIES,
    RETIREMENT_STATUS_FREE,
    RETIREMENT_STATUS_QUEUED,
    TaggedDescriptorIO,
)


@dataclass(frozen=True)
class MiddleLiveTailEvacuationTrace:
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
    successor_page: int | None
    successor_incarnation: int | None
    destination_page: int | None
    destination_old_incarnation: int | None
    destination_new_incarnation: int | None
    new_physical_tail_page: int | None
    new_physical_tail_incarnation: int | None
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
            "physical_tail_page": self.physical_tail_page,
            "physical_tail_incarnation": self.physical_tail_incarnation,
            "predecessor_page": self.predecessor_page,
            "predecessor_incarnation": self.predecessor_incarnation,
            "successor_page": self.successor_page,
            "successor_incarnation": self.successor_incarnation,
            "destination_page": self.destination_page,
            "destination_old_incarnation": self.destination_old_incarnation,
            "destination_new_incarnation": self.destination_new_incarnation,
            "new_physical_tail_page": self.new_physical_tail_page,
            "new_physical_tail_incarnation": self.new_physical_tail_incarnation,
            "retirement_arena_fsyncs": self.retirement_arena_fsyncs,
            "released": self.released,
        }


class MiddleLiveTailEvacuationRetirementDescriptorPrimaryStore(
    MultiFreeLiveTailEvacuationRetirementDescriptorPrimaryStore
):
    """v0.42 first bounded interior-live-physical-tail evacuation candidate.

    v0.40-v0.41 require the physical retirement-descriptor tail to also be the
    logical queue tail. v0.42 tests the first interior geometry: the physical tail
    is a QUEUED descriptor whose predecessor is the queue head and whose successor
    is the logical queue tail. A lower sole FREE descriptor is the relocation
    destination.

    The candidate publishes tagged current-state authority for the live physical
    tail and its queue predecessor. When an append allocates a new descriptor, the
    new logical tail is also the new physical tail. When a later enqueue reuses a
    lower FREE descriptor while the old logical tail is the physical tail, the old
    physical tail becomes interior and its previous queue-tail predecessor remains
    the directly named inbound edge.

    The relocation reads exactly four directly named descriptors: physical tail,
    predecessor, successor, and sole FREE destination. It copies the physical
    tail's outbound successor edge into the relocated descriptor, rewrites the
    predecessor's inbound edge, updates logical-tail predecessor authority when the
    successor is that tail, publishes the shorter arena frontier, then truncates the
    old physical pair as derived cleanup.

    This first candidate deliberately requires the physical-tail predecessor to be
    the logical queue head. That makes the next physical tail after truncation the
    queue head with null predecessor authority. Maintenance after later reclamation
    of that predecessor is outside the v0.42 claim.
    """

    ARENA_SUFFIX = ".retire42"

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
                    "format": 42,
                    "retirement_physical_tail_page": None,
                    "retirement_physical_tail_incarnation": None,
                    "retirement_physical_tail_predecessor_page": None,
                    "retirement_physical_tail_predecessor_incarnation": None,
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
        old_tail_page = tx.meta.get("retirement_queue_tail_page")
        old_tail_incarnation = tx.meta.get("retirement_queue_tail_incarnation")
        old_tail_predecessor_page = tx.meta.get("retirement_queue_tail_predecessor_page")
        old_tail_predecessor_incarnation = tx.meta.get(
            "retirement_queue_tail_predecessor_incarnation"
        )
        old_physical_page = tx.meta.get("retirement_physical_tail_page")
        old_physical_incarnation = tx.meta.get("retirement_physical_tail_incarnation")
        old_physical_predecessor_page = tx.meta.get(
            "retirement_physical_tail_predecessor_page"
        )
        old_physical_predecessor_incarnation = tx.meta.get(
            "retirement_physical_tail_predecessor_incarnation"
        )

        super()._enqueue_retirement_tx(
            tx,
            generation=generation,
            owner_head_page=owner_head_page,
            owner_count=owner_count,
        )

        new_tail_page = tx.meta.get("retirement_queue_tail_page")
        new_tail_incarnation = tx.meta.get("retirement_queue_tail_incarnation")
        if new_tail_page is None or new_tail_incarnation is None:
            raise RuntimeError("v0.42 enqueue did not publish a tagged queue tail")

        if old_count == 0:
            tx.meta["retirement_physical_tail_page"] = int(new_tail_page)
            tx.meta["retirement_physical_tail_incarnation"] = int(new_tail_incarnation)
            tx.meta["retirement_physical_tail_predecessor_page"] = None
            tx.meta["retirement_physical_tail_predecessor_incarnation"] = None
            return

        if old_tail_page is None or old_tail_incarnation is None:
            raise RuntimeError("v0.42 non-empty pre-enqueue queue lacks tagged tail")
        if old_physical_page is None or old_physical_incarnation is None:
            raise RuntimeError("v0.42 non-empty queue lacks physical-tail authority")

        if old_free_count == 0:
            # No reusable descriptor existed, so the new logical tail was appended at
            # the physical arena frontier and is the new physical tail.
            tx.meta["retirement_physical_tail_page"] = int(new_tail_page)
            tx.meta["retirement_physical_tail_incarnation"] = int(new_tail_incarnation)
            tx.meta["retirement_physical_tail_predecessor_page"] = int(old_tail_page)
            tx.meta["retirement_physical_tail_predecessor_incarnation"] = int(
                old_tail_incarnation
            )
            return

        # Reuse is below the committed physical frontier. If the old logical tail was
        # also the physical tail, it becomes interior and keeps the predecessor that
        # was authoritative immediately before this enqueue. Otherwise the existing
        # interior physical-tail authority is unaffected by appending through reuse.
        tx.meta["retirement_physical_tail_page"] = int(old_physical_page)
        tx.meta["retirement_physical_tail_incarnation"] = int(old_physical_incarnation)
        if (
            int(old_tail_page) == int(old_physical_page)
            and int(old_tail_incarnation) == int(old_physical_incarnation)
        ):
            tx.meta["retirement_physical_tail_predecessor_page"] = (
                None
                if old_tail_predecessor_page is None
                else int(old_tail_predecessor_page)
            )
            tx.meta["retirement_physical_tail_predecessor_incarnation"] = (
                None
                if old_tail_predecessor_incarnation is None
                else int(old_tail_predecessor_incarnation)
            )
        else:
            tx.meta["retirement_physical_tail_predecessor_page"] = (
                None
                if old_physical_predecessor_page is None
                else int(old_physical_predecessor_page)
            )
            tx.meta["retirement_physical_tail_predecessor_incarnation"] = (
                None
                if old_physical_predecessor_incarnation is None
                else int(old_physical_predecessor_incarnation)
            )

    def retirement_queue_snapshot(self) -> dict[str, Any]:
        row = super().retirement_queue_snapshot()
        fd = self._open()
        try:
            _epoch, meta, _slot = self._read_super(fd)
            row["physical_tail_page"] = meta.get("retirement_physical_tail_page")
            row["physical_tail_incarnation"] = meta.get(
                "retirement_physical_tail_incarnation"
            )
            row["physical_tail_predecessor_page"] = meta.get(
                "retirement_physical_tail_predecessor_page"
            )
            row["physical_tail_predecessor_incarnation"] = meta.get(
                "retirement_physical_tail_predecessor_incarnation"
            )
        finally:
            os.close(fd)
        return row

    @staticmethod
    def _no_middle_release_trace(
        *,
        committed_epoch: int,
        queue_count: int,
        free_count: int,
        arena_pages: int,
        preads: int = 0,
        physical_tail_page: int | None = None,
        physical_tail_incarnation: int | None = None,
    ) -> MiddleLiveTailEvacuationTrace:
        return MiddleLiveTailEvacuationTrace(
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
            successor_page=None,
            successor_incarnation=None,
            destination_page=None,
            destination_old_incarnation=None,
            destination_new_incarnation=None,
            new_physical_tail_page=None,
            new_physical_tail_incarnation=None,
            retirement_arena_fsyncs=0,
        )

    def evacuate_middle_live_retirement_arena_tail_step(
        self,
        failpoint: Callable[[str], None] | None = None,
    ) -> MiddleLiveTailEvacuationTrace:
        fd = self._open()
        arena_fd = self._open_arena()
        try:
            committed_epoch, meta, _slot = self._read_super(fd)
            meta = dict(meta)
            arena_pages_before = self._committed_arena_pages(meta)
            queue_count = int(meta.get("retirement_queue_count", 0))
            free_count_before = int(meta.get("retirement_descriptor_free_count", 0))
            if arena_pages_before < RETIREMENT_DESCRIPTOR_COPIES or queue_count != 3:
                return self._no_middle_release_trace(
                    committed_epoch=committed_epoch,
                    queue_count=queue_count,
                    free_count=free_count_before,
                    arena_pages=arena_pages_before,
                )
            if free_count_before != 1:
                return self._no_middle_release_trace(
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
            if (
                physical_page != physical_tail_base
                or physical_incarnation is None
                or predecessor_page is None
                or predecessor_incarnation is None
            ):
                return self._no_middle_release_trace(
                    committed_epoch=committed_epoch,
                    queue_count=queue_count,
                    free_count=free_count_before,
                    arena_pages=arena_pages_before,
                    physical_tail_page=physical_tail_base,
                    physical_tail_incarnation=(
                        None if physical_incarnation is None else int(physical_incarnation)
                    ),
                )

            queue_tail_page = meta.get("retirement_queue_tail_page")
            queue_tail_incarnation = meta.get("retirement_queue_tail_incarnation")
            if queue_tail_page == physical_tail_base:
                return self._no_middle_release_trace(
                    committed_epoch=committed_epoch,
                    queue_count=queue_count,
                    free_count=free_count_before,
                    arena_pages=arena_pages_before,
                    physical_tail_page=physical_tail_base,
                    physical_tail_incarnation=int(physical_incarnation),
                )

            queue_head_page = meta.get("retirement_queue_head_page")
            queue_head_incarnation = meta.get("retirement_queue_head_incarnation")
            if (
                queue_head_page != predecessor_page
                or queue_head_incarnation != predecessor_incarnation
            ):
                # First v0.42 geometry only: after truncation this predecessor becomes
                # the new physical tail and must be the queue head, so its predecessor
                # authority is known to be null without another reverse lookup.
                return self._no_middle_release_trace(
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
                raise RuntimeError("v0.42 interior physical tail lacks tagged successor")
            if (
                queue_tail_page != successor_page
                or queue_tail_incarnation != successor_incarnation
            ):
                raise RuntimeError("v0.42 first interior geometry requires successor to be queue tail")

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
                raise RuntimeError("v0.42 predecessor does not reference physical tail")

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
                raise RuntimeError("v0.42 logical queue tail unexpectedly has a successor")

            free_page = meta.get("retirement_descriptor_free_head_page")
            free_incarnation = meta.get("retirement_descriptor_free_head_incarnation")
            if free_page is None or free_incarnation is None or int(free_page) >= physical_tail_base:
                return self._no_middle_release_trace(
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
                raise RuntimeError("v0.42 sole FREE destination retains predecessor")
            if (
                destination["next_descriptor_page"] is not None
                or destination["next_descriptor_incarnation"] is not None
            ):
                raise RuntimeError("v0.42 sole FREE destination unexpectedly has successor")

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
                failpoint("retirement_middle_tail_destination_staged")

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
                failpoint("retirement_middle_tail_predecessor_staged")

            next_meta["retirement_queue_tail_predecessor_page"] = int(free_page)
            next_meta["retirement_queue_tail_predecessor_incarnation"] = (
                destination_new_incarnation
            )
            next_meta["retirement_descriptor_free_head_page"] = None
            next_meta["retirement_descriptor_free_head_incarnation"] = None
            next_meta["retirement_descriptor_free_count"] = 0
            next_meta["retirement_descriptor_arena_pages"] = physical_tail_base
            next_meta["retirement_physical_tail_page"] = int(predecessor_page)
            next_meta["retirement_physical_tail_incarnation"] = int(
                predecessor_incarnation
            )
            next_meta["retirement_physical_tail_predecessor_page"] = None
            next_meta["retirement_physical_tail_predecessor_incarnation"] = None

            os.fsync(arena_fd)
            if failpoint is not None:
                failpoint("retirement_middle_tail_dependencies_synced")

            self._write_super(fd, committed_epoch, new_epoch, next_meta)
            os.fsync(fd)
            if failpoint is not None:
                failpoint("committed")
                failpoint("retirement_middle_tail_relocation_committed")

            os.ftruncate(arena_fd, physical_tail_base * PAGE_SIZE)
            if failpoint is not None:
                failpoint("retirement_arena_truncated")
            os.fsync(arena_fd)
            if failpoint is not None:
                failpoint("retirement_middle_tail_relocation_synced")

            return MiddleLiveTailEvacuationTrace(
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
                successor_page=int(successor_page),
                successor_incarnation=int(successor_incarnation),
                destination_page=int(free_page),
                destination_old_incarnation=int(destination["incarnation"]),
                destination_new_incarnation=destination_new_incarnation,
                new_physical_tail_page=int(predecessor_page),
                new_physical_tail_incarnation=int(predecessor_incarnation),
                retirement_arena_fsyncs=2,
            )
        finally:
            os.close(arena_fd)
            os.close(fd)


LiveTailEvacuationRetirementDescriptorPrimaryStore = (
    MiddleLiveTailEvacuationRetirementDescriptorPrimaryStore
)
