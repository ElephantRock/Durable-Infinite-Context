from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from storage.fixed_page_primary import PAGE_SIZE
from storage.partial_tail_shrinking_retirement_descriptor_primary import (
    PartialTailShrinkingRetirementDescriptorPrimaryStore,
)
from storage.retirement_descriptor_pool import (
    RETIREMENT_DESCRIPTOR_COPIES,
    RETIREMENT_STATUS_FREE,
    RETIREMENT_STATUS_QUEUED,
    TaggedDescriptorIO,
)


@dataclass(frozen=True)
class BidirectionalReclaimTrace:
    base: Any
    free_predecessor_preads: int
    free_predecessor_pwrites: int

    def __getattr__(self, name: str) -> Any:
        return getattr(self.base, name)

    def to_dict(self) -> dict[str, Any]:
        row = self.base.to_dict()
        row.update(
            {
                "free_predecessor_preads": self.free_predecessor_preads,
                "free_predecessor_pwrites": self.free_predecessor_pwrites,
            }
        )
        return row


@dataclass(frozen=True)
class BidirectionalTailShrinkTrace:
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
    tail_page: int | None
    tail_incarnation: int | None
    tail_was_free: bool
    tail_was_free_head: bool
    predecessor_page: int | None
    predecessor_incarnation: int | None
    successor_page: int | None
    successor_incarnation: int | None
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
            "tail_page": self.tail_page,
            "tail_incarnation": self.tail_incarnation,
            "tail_was_free": self.tail_was_free,
            "tail_was_free_head": self.tail_was_free_head,
            "predecessor_page": self.predecessor_page,
            "predecessor_incarnation": self.predecessor_incarnation,
            "successor_page": self.successor_page,
            "successor_incarnation": self.successor_incarnation,
            "retirement_arena_fsyncs": self.retirement_arena_fsyncs,
            "released": self.released,
        }


class BidirectionalRetirementDescriptorPrimaryStore(
    PartialTailShrinkingRetirementDescriptorPrimaryStore
):
    """v0.39 bounded non-head free-tail unlink experiment.

    FREE descriptors retain the singly-linked successor used by v0.35-v0.38 and add
    one predecessor identity in fields that are unused while FREE. The free-list head
    still has a null predecessor. Reclaim and reuse maintain only the immediately
    affected predecessor link at existing bounded transaction failpoints.

    A maintenance step can therefore address the physical descriptor tail directly,
    read its predecessor/successor identities, rewrite at most those two neighboring
    FREE descriptors to bypass the tail, commit a shorter authoritative arena frontier,
    and truncate the sidecar. No free-list walk or live-descriptor relocation is needed.
    """

    ARENA_SUFFIX = ".retire39"

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
            meta["format"] = 39
            os.pwrite(
                fd,
                self._pack_record(self._super_magic(), epoch, meta),
                self._super_offset(slot),
            )
            os.fsync(fd)
        finally:
            os.close(fd)

    @staticmethod
    def _free_prev(payload: dict[str, Any]) -> tuple[int | None, int | None]:
        page = payload.get("prev_descriptor_page")
        incarnation = payload.get("prev_descriptor_incarnation")
        if page is None:
            if incarnation is not None:
                raise RuntimeError("null free predecessor page retains incarnation")
            return None, None
        if incarnation is None:
            raise RuntimeError("free predecessor page lacks incarnation")
        return int(page), int(incarnation)

    @classmethod
    def _free_write_payload(
        cls,
        payload: dict[str, Any],
        *,
        prev_page: int | None,
        prev_incarnation: int | None,
        next_page: int | None = None,
        next_incarnation: int | None = None,
        preserve_next: bool = True,
    ) -> dict[str, Any]:
        if preserve_next:
            next_page = payload["next_descriptor_page"]
            next_incarnation = payload["next_descriptor_incarnation"]
        return {
            "incarnation": int(payload["incarnation"]),
            "status": RETIREMENT_STATUS_FREE,
            "generation": None,
            "cursor_header_page": None,
            "remaining_segments": 0,
            "next_descriptor_page": next_page,
            "next_descriptor_incarnation": next_incarnation,
            "prev_descriptor_page": prev_page,
            "prev_descriptor_incarnation": prev_incarnation,
        }

    def _enqueue_retirement_tx(
        self,
        tx,
        *,
        generation: int,
        owner_head_page: int | None,
        owner_count: int,
    ) -> None:  # type: ignore[no-untyped-def]
        old_free_count = int(tx.meta.get("retirement_descriptor_free_count", 0))
        old_head_page = tx.meta.get("retirement_descriptor_free_head_page")
        old_head_incarnation = tx.meta.get("retirement_descriptor_free_head_incarnation")
        committed_epoch = int(tx.epoch)
        new_epoch = committed_epoch + 1
        original_failpoint = self._active_failpoint

        def topology_failpoint(name: str) -> None:
            if name == "retirement_descriptor_reused" and old_free_count > 1:
                new_head_page = tx.meta.get("retirement_descriptor_free_head_page")
                new_head_incarnation = tx.meta.get("retirement_descriptor_free_head_incarnation")
                if new_head_page is None or new_head_incarnation is None:
                    raise RuntimeError("reused free head lost its successor identity")
                arena_fd = self._open_arena()
                try:
                    payload, slot, preads = TaggedDescriptorIO.read(
                        arena_fd,
                        int(new_head_page),
                        committed_epoch,
                        expected_incarnation=int(new_head_incarnation),
                        expected_status=RETIREMENT_STATUS_FREE,
                    )
                    prev_page, prev_incarnation = self._free_prev(payload)
                    if prev_page != int(old_head_page) or prev_incarnation != int(old_head_incarnation):
                        raise RuntimeError("new free head predecessor does not reference reused head")
                    extra_reads, writes = TaggedDescriptorIO.write_existing(
                        arena_fd,
                        int(new_head_page),
                        committed_epoch,
                        new_epoch,
                        committed_slot=slot,
                        **self._free_write_payload(
                            payload,
                            prev_page=None,
                            prev_incarnation=None,
                        ),
                    )
                    self._pending_retirement_descriptor_preads += preads + extra_reads
                    self._pending_retirement_descriptor_pwrites += writes
                finally:
                    os.close(arena_fd)
            if original_failpoint is not None:
                original_failpoint(name)

        self._active_failpoint = topology_failpoint
        try:
            super()._enqueue_retirement_tx(
                tx,
                generation=generation,
                owner_head_page=owner_head_page,
                owner_count=owner_count,
            )
        finally:
            self._active_failpoint = original_failpoint

    def reclaim_step(
        self,
        *,
        budget: int,
        failpoint: Callable[[str], None] | None = None,
    ) -> BidirectionalReclaimTrace:
        fd = self._open()
        try:
            committed_epoch, meta, _slot = self._read_super(fd)
            meta = dict(meta)
        finally:
            os.close(fd)
        old_head_page = meta.get("retirement_queue_head_page")
        old_head_incarnation = meta.get("retirement_queue_head_incarnation")
        old_free_page = meta.get("retirement_descriptor_free_head_page")
        old_free_incarnation = meta.get("retirement_descriptor_free_head_incarnation")
        topology_preads = 0
        topology_pwrites = 0

        def topology_failpoint(name: str) -> None:
            nonlocal topology_preads, topology_pwrites
            if name == "retirement_descriptor_freed" and old_free_page is not None:
                if old_free_incarnation is None or old_head_page is None or old_head_incarnation is None:
                    raise RuntimeError("free-list predecessor maintenance lacks tagged identity")
                arena_fd = self._open_arena()
                try:
                    payload, slot, preads = TaggedDescriptorIO.read(
                        arena_fd,
                        int(old_free_page),
                        committed_epoch,
                        expected_incarnation=int(old_free_incarnation),
                        expected_status=RETIREMENT_STATUS_FREE,
                    )
                    prev_page, prev_incarnation = self._free_prev(payload)
                    if prev_page is not None or prev_incarnation is not None:
                        raise RuntimeError("committed free-list head already has a predecessor")
                    extra_reads, writes = TaggedDescriptorIO.write_existing(
                        arena_fd,
                        int(old_free_page),
                        committed_epoch,
                        committed_epoch + 1,
                        committed_slot=slot,
                        **self._free_write_payload(
                            payload,
                            prev_page=int(old_head_page),
                            prev_incarnation=int(old_head_incarnation),
                        ),
                    )
                    topology_preads += preads + extra_reads
                    topology_pwrites += writes
                finally:
                    os.close(arena_fd)
            if failpoint is not None:
                failpoint(name)

        base = super().reclaim_step(budget=budget, failpoint=topology_failpoint)
        return BidirectionalReclaimTrace(
            base=base,
            free_predecessor_preads=topology_preads,
            free_predecessor_pwrites=topology_pwrites,
        )

    def shrink_retirement_arena_tail_step(
        self,
        failpoint: Callable[[str], None] | None = None,
    ) -> BidirectionalTailShrinkTrace:
        fd = self._open()
        arena_fd = self._open_arena()
        try:
            committed_epoch, meta, _slot = self._read_super(fd)
            meta = dict(meta)
            arena_pages_before = self._committed_arena_pages(meta)
            queue_count = int(meta.get("retirement_queue_count", 0))
            free_count_before = int(meta.get("retirement_descriptor_free_count", 0))

            if arena_pages_before == 0:
                return BidirectionalTailShrinkTrace(
                    committed_epoch, committed_epoch, queue_count,
                    free_count_before, free_count_before,
                    0, 0, 0, 0, 0, 0, 0, 0,
                    None, None, False, False, None, None, None, None, 0,
                )
            if queue_count <= 0:
                raise RuntimeError("non-empty descriptor arena has no live retirement queue")

            tail_base = arena_pages_before - RETIREMENT_DESCRIPTOR_COPIES
            tail_payload, tail_slot, descriptor_preads = TaggedDescriptorIO.read(
                arena_fd,
                tail_base,
                committed_epoch,
            )
            tail_incarnation = int(tail_payload["incarnation"])
            if int(tail_payload["status"]) != RETIREMENT_STATUS_FREE:
                return BidirectionalTailShrinkTrace(
                    committed_epoch, committed_epoch, queue_count,
                    free_count_before, free_count_before,
                    arena_pages_before, arena_pages_before, 0, 0,
                    descriptor_preads, 0, 0, 0,
                    tail_base, tail_incarnation, False, False,
                    None, None, None, None, 0,
                )
            if free_count_before <= 0:
                raise RuntimeError("physical free tail is absent from committed free count")

            prev_page, prev_incarnation = self._free_prev(tail_payload)
            next_page = tail_payload["next_descriptor_page"]
            next_incarnation = tail_payload["next_descriptor_incarnation"]
            free_head_page = meta.get("retirement_descriptor_free_head_page")
            free_head_incarnation = meta.get("retirement_descriptor_free_head_incarnation")
            tail_was_free_head = prev_page is None
            if tail_was_free_head:
                if free_head_page != tail_base or free_head_incarnation != tail_incarnation:
                    raise RuntimeError("null predecessor tail is not the committed free-list head")
            elif free_head_page == tail_base:
                raise RuntimeError("non-null predecessor tail is incorrectly the free-list head")

            retained_pages = tail_base
            for label, page in (
                ("queue head", meta.get("retirement_queue_head_page")),
                ("queue tail", meta.get("retirement_queue_tail_page")),
            ):
                if page is None or int(page) >= retained_pages:
                    raise RuntimeError(f"live retirement {label} would be truncated")

            new_epoch = committed_epoch + 1
            descriptor_pwrites = 0
            if prev_page is not None:
                predecessor, predecessor_slot, preads = TaggedDescriptorIO.read(
                    arena_fd,
                    int(prev_page),
                    committed_epoch,
                    expected_incarnation=int(prev_incarnation),
                    expected_status=RETIREMENT_STATUS_FREE,
                )
                descriptor_preads += preads
                if (
                    predecessor["next_descriptor_page"] != tail_base
                    or predecessor["next_descriptor_incarnation"] != tail_incarnation
                ):
                    raise RuntimeError("tail predecessor does not link to physical tail")
                extra_reads, writes = TaggedDescriptorIO.write_existing(
                    arena_fd,
                    int(prev_page),
                    committed_epoch,
                    new_epoch,
                    committed_slot=predecessor_slot,
                    **self._free_write_payload(
                        predecessor,
                        prev_page=predecessor.get("prev_descriptor_page"),
                        prev_incarnation=predecessor.get("prev_descriptor_incarnation"),
                        next_page=next_page,
                        next_incarnation=next_incarnation,
                        preserve_next=False,
                    ),
                )
                descriptor_preads += extra_reads
                descriptor_pwrites += writes

            if next_page is not None:
                if next_incarnation is None:
                    raise RuntimeError("tail successor page lacks incarnation")
                successor, successor_slot, preads = TaggedDescriptorIO.read(
                    arena_fd,
                    int(next_page),
                    committed_epoch,
                    expected_incarnation=int(next_incarnation),
                    expected_status=RETIREMENT_STATUS_FREE,
                )
                descriptor_preads += preads
                successor_prev_page, successor_prev_incarnation = self._free_prev(successor)
                if successor_prev_page != tail_base or successor_prev_incarnation != tail_incarnation:
                    raise RuntimeError("tail successor does not point back to physical tail")
                extra_reads, writes = TaggedDescriptorIO.write_existing(
                    arena_fd,
                    int(next_page),
                    committed_epoch,
                    new_epoch,
                    committed_slot=successor_slot,
                    **self._free_write_payload(
                        successor,
                        prev_page=prev_page,
                        prev_incarnation=prev_incarnation,
                    ),
                )
                descriptor_preads += extra_reads
                descriptor_pwrites += writes

            next_meta = dict(meta)
            if tail_was_free_head:
                next_meta["retirement_descriptor_free_head_page"] = next_page
                next_meta["retirement_descriptor_free_head_incarnation"] = next_incarnation
            next_meta["retirement_descriptor_free_count"] = free_count_before - 1
            next_meta["retirement_descriptor_arena_pages"] = retained_pages
            if next_meta["retirement_descriptor_free_count"] == 0:
                if next_meta.get("retirement_descriptor_free_head_page") is not None:
                    raise RuntimeError("empty free list retains a head after tail unlink")

            if failpoint is not None:
                failpoint("retirement_tail_unlink_staged")
            os.fsync(arena_fd)
            if failpoint is not None:
                failpoint("retirement_tail_unlink_dependencies_synced")

            self._write_super(fd, committed_epoch, new_epoch, next_meta)
            os.fsync(fd)
            if failpoint is not None:
                failpoint("committed")
                failpoint("retirement_tail_unlink_committed")

            os.ftruncate(arena_fd, retained_pages * PAGE_SIZE)
            if failpoint is not None:
                failpoint("retirement_arena_truncated")
            os.fsync(arena_fd)
            if failpoint is not None:
                failpoint("retirement_tail_unlink_synced")

            return BidirectionalTailShrinkTrace(
                committed_epoch_before=committed_epoch,
                committed_epoch_after=new_epoch,
                retirement_queue_count=queue_count,
                retirement_descriptor_free_count_before=free_count_before,
                retirement_descriptor_free_count_after=free_count_before - 1,
                retirement_arena_pages_before=arena_pages_before,
                retirement_arena_pages_after=retained_pages,
                retirement_arena_pages_released=RETIREMENT_DESCRIPTOR_COPIES,
                retirement_arena_bytes_released=RETIREMENT_DESCRIPTOR_COPIES * PAGE_SIZE,
                retirement_descriptor_preads=descriptor_preads,
                retirement_descriptor_pwrites=descriptor_pwrites,
                retirement_descriptors_scanned=0,
                candidate_relocations=0,
                tail_page=tail_base,
                tail_incarnation=tail_incarnation,
                tail_was_free=True,
                tail_was_free_head=tail_was_free_head,
                predecessor_page=prev_page,
                predecessor_incarnation=prev_incarnation,
                successor_page=next_page,
                successor_incarnation=next_incarnation,
                retirement_arena_fsyncs=2,
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
        # Keep the v0.38 committed-frontier rule: physical post-commit residue is not
        # committed descriptor state even if bytes remain before recovery truncation.
        return super().retirement_descriptor_reference(
            base_page,
            incarnation,
            expected_status=expected_status,
        )
