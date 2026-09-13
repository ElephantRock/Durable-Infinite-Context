from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable

from storage.fixed_page_primary import PAGE_SIZE
from storage.retirement_descriptor_pool import (
    RETIREMENT_DESCRIPTOR_COPIES,
    RETIREMENT_STATUS_FREE,
    RETIREMENT_STATUS_QUEUED,
    TaggedDescriptorIO,
)
from storage.segregated_retirement_descriptor_primary import (
    SegregatedRetirementDescriptorPrimaryStore,
)


@dataclass(frozen=True)
class PartialTailShrinkTrace:
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
    candidate_relocations: int
    tail_aligned: bool
    tail_page: int | None
    tail_incarnation: int | None
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
            "candidate_relocations": self.candidate_relocations,
            "tail_aligned": self.tail_aligned,
            "tail_page": self.tail_page,
            "tail_incarnation": self.tail_incarnation,
            "retirement_arena_fsyncs": self.retirement_arena_fsyncs,
            "released": self.released,
        }


class PartialTailShrinkingRetirementDescriptorPrimaryStore(
    SegregatedRetirementDescriptorPrimaryStore
):
    """v0.38 bounded partial descriptor-arena tail-shrink experiment.

    v0.37 can reset the entire segregated descriptor arena once the retirement queue
    becomes empty. v0.38 tests a narrower partial-release mechanism while live queue
    state remains: if the committed descriptor free-list head is exactly the highest
    allocated descriptor pair, one bounded maintenance step removes that one free
    descriptor from the authoritative free list, commits a two-page-shorter arena
    frontier, and then truncates the sidecar as derived physical cleanup.

    The candidate deliberately does not search the free list for an arbitrary free
    tail descriptor. A non-head free tail remains outside this version's claim.
    """

    ARENA_SUFFIX = ".retire38"

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
            meta["format"] = 38
            os.pwrite(
                fd,
                self._pack_record(self._super_magic(), epoch, meta),
                self._super_offset(slot),
            )
            os.fsync(fd)
        finally:
            os.close(fd)

    def shrink_retirement_arena_tail_step(
        self,
        failpoint: Callable[[str], None] | None = None,
    ) -> PartialTailShrinkTrace:
        fd = self._open()
        arena_fd = self._open_arena()
        try:
            committed_epoch, meta, _slot = self._read_super(fd)
            meta = dict(meta)
            arena_pages_before = self._committed_arena_pages(meta)
            queue_count = int(meta.get("retirement_queue_count", 0))
            free_count_before = int(meta.get("retirement_descriptor_free_count", 0))
            free_page = meta.get("retirement_descriptor_free_head_page")
            free_incarnation = meta.get("retirement_descriptor_free_head_incarnation")

            if arena_pages_before == 0:
                if free_count_before != 0 or free_page is not None or free_incarnation is not None:
                    raise RuntimeError("zero descriptor arena retains free-list authority")
                return PartialTailShrinkTrace(
                    committed_epoch_before=committed_epoch,
                    committed_epoch_after=committed_epoch,
                    retirement_queue_count=queue_count,
                    retirement_descriptor_free_count_before=0,
                    retirement_descriptor_free_count_after=0,
                    retirement_arena_pages_before=0,
                    retirement_arena_pages_after=0,
                    retirement_arena_pages_released=0,
                    retirement_arena_bytes_released=0,
                    retirement_descriptor_preads=0,
                    retirement_descriptor_pwrites=0,
                    retirement_descriptors_scanned=0,
                    candidate_relocations=0,
                    tail_aligned=False,
                    tail_page=None,
                    tail_incarnation=None,
                    retirement_arena_fsyncs=0,
                )

            if queue_count <= 0:
                raise RuntimeError("non-empty descriptor arena has no live retirement queue")
            if free_count_before == 0:
                if free_page is not None or free_incarnation is not None:
                    raise RuntimeError("empty descriptor free list retains a head identity")
                return PartialTailShrinkTrace(
                    committed_epoch_before=committed_epoch,
                    committed_epoch_after=committed_epoch,
                    retirement_queue_count=queue_count,
                    retirement_descriptor_free_count_before=0,
                    retirement_descriptor_free_count_after=0,
                    retirement_arena_pages_before=arena_pages_before,
                    retirement_arena_pages_after=arena_pages_before,
                    retirement_arena_pages_released=0,
                    retirement_arena_bytes_released=0,
                    retirement_descriptor_preads=0,
                    retirement_descriptor_pwrites=0,
                    retirement_descriptors_scanned=0,
                    candidate_relocations=0,
                    tail_aligned=False,
                    tail_page=None,
                    tail_incarnation=None,
                    retirement_arena_fsyncs=0,
                )
            if free_page is None or free_incarnation is None:
                raise RuntimeError("descriptor free count is nonzero without a head identity")

            tail_base = arena_pages_before - RETIREMENT_DESCRIPTOR_COPIES
            if int(free_page) != tail_base:
                return PartialTailShrinkTrace(
                    committed_epoch_before=committed_epoch,
                    committed_epoch_after=committed_epoch,
                    retirement_queue_count=queue_count,
                    retirement_descriptor_free_count_before=free_count_before,
                    retirement_descriptor_free_count_after=free_count_before,
                    retirement_arena_pages_before=arena_pages_before,
                    retirement_arena_pages_after=arena_pages_before,
                    retirement_arena_pages_released=0,
                    retirement_arena_bytes_released=0,
                    retirement_descriptor_preads=0,
                    retirement_descriptor_pwrites=0,
                    retirement_descriptors_scanned=0,
                    candidate_relocations=0,
                    tail_aligned=False,
                    tail_page=int(free_page),
                    tail_incarnation=int(free_incarnation),
                    retirement_arena_fsyncs=0,
                )

            free_payload, _free_slot, preads = TaggedDescriptorIO.read(
                arena_fd,
                tail_base,
                committed_epoch,
                expected_incarnation=int(free_incarnation),
                expected_status=RETIREMENT_STATUS_FREE,
            )
            next_free_page = free_payload["next_descriptor_page"]
            next_free_incarnation = free_payload["next_descriptor_incarnation"]
            if next_free_page is not None and int(next_free_page) >= tail_base:
                raise RuntimeError("tail free descriptor points outside the retained arena")

            retained_pages = tail_base
            queue_head = meta.get("retirement_queue_head_page")
            queue_tail = meta.get("retirement_queue_tail_page")
            for label, page in (("head", queue_head), ("tail", queue_tail)):
                if page is None:
                    raise RuntimeError(f"live retirement queue lacks {label} identity")
                if int(page) >= retained_pages:
                    raise RuntimeError(f"live retirement queue {label} would be truncated")

            next_meta = dict(meta)
            next_meta["retirement_descriptor_free_head_page"] = next_free_page
            next_meta["retirement_descriptor_free_head_incarnation"] = next_free_incarnation
            next_meta["retirement_descriptor_free_count"] = free_count_before - 1
            next_meta["retirement_descriptor_arena_pages"] = retained_pages
            new_epoch = committed_epoch + 1

            if failpoint is not None:
                failpoint("retirement_tail_release_staged")

            self._write_super(fd, committed_epoch, new_epoch, next_meta)
            os.fsync(fd)
            if failpoint is not None:
                failpoint("committed")
                failpoint("retirement_tail_release_committed")

            os.ftruncate(arena_fd, retained_pages * PAGE_SIZE)
            if failpoint is not None:
                failpoint("retirement_arena_truncated")
            os.fsync(arena_fd)
            if failpoint is not None:
                failpoint("retirement_tail_release_synced")

            return PartialTailShrinkTrace(
                committed_epoch_before=committed_epoch,
                committed_epoch_after=new_epoch,
                retirement_queue_count=queue_count,
                retirement_descriptor_free_count_before=free_count_before,
                retirement_descriptor_free_count_after=free_count_before - 1,
                retirement_arena_pages_before=arena_pages_before,
                retirement_arena_pages_after=retained_pages,
                retirement_arena_pages_released=RETIREMENT_DESCRIPTOR_COPIES,
                retirement_arena_bytes_released=RETIREMENT_DESCRIPTOR_COPIES * PAGE_SIZE,
                retirement_descriptor_preads=preads,
                retirement_descriptor_pwrites=0,
                retirement_descriptors_scanned=0,
                candidate_relocations=0,
                tail_aligned=True,
                tail_page=tail_base,
                tail_incarnation=int(free_incarnation),
                retirement_arena_fsyncs=1,
            )
        finally:
            os.close(arena_fd)
            os.close(fd)

    def retirement_descriptor_reference(
        self,
        base_page: int,
        incarnation: int,
        *,
        expected_status: int = RETIREMENT_STATUS_QUEUED,
    ) -> dict[str, Any]:
        fd = self._open()
        arena_fd = self._open_arena()
        try:
            epoch, meta, _slot = self._read_super(fd)
            arena_pages = self._committed_arena_pages(meta)
            base = int(base_page)
            if base < 0 or base + RETIREMENT_DESCRIPTOR_COPIES > arena_pages:
                raise RuntimeError("descriptor reference lies outside committed arena frontier")
            payload, _slot, _preads = TaggedDescriptorIO.read(
                arena_fd,
                base,
                epoch,
                expected_incarnation=int(incarnation),
                expected_status=int(expected_status),
            )
            return payload
        finally:
            os.close(arena_fd)
            os.close(fd)
