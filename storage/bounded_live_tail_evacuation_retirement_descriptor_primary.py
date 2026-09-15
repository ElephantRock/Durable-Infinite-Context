from __future__ import annotations

from typing import Callable

from storage.live_tail_evacuation_retirement_descriptor_primary import (
    LiveTailEvacuationRetirementDescriptorPrimaryStore as _PrototypeLiveTailStore,
    LiveTailEvacuationTrace,
)
from storage.retirement_descriptor_pool import RETIREMENT_DESCRIPTOR_COPIES


class BoundedLiveTailEvacuationRetirementDescriptorPrimaryStore(
    _PrototypeLiveTailStore
):
    """Claim-bearing v0.40 store with an explicit sole-free-pair boundary.

    The underlying relocation primitive is sufficient for the first falsification
    geometry only when the lower destination is the sole committed FREE descriptor.
    If more than one free descriptor exists, consuming the free-list head would also
    require updating its successor's predecessor link. That broader topology rewrite
    is deliberately outside v0.40, so this wrapper refuses the operation before any
    descriptor read or write instead of leaving a stale bidirectional FREE edge.
    """

    def evacuate_live_retirement_arena_tail_step(
        self,
        failpoint: Callable[[str], None] | None = None,
    ) -> LiveTailEvacuationTrace:
        fd = self._open()
        try:
            committed_epoch, meta, _slot = self._read_super(fd)
            meta = dict(meta)
            arena_pages = self._committed_arena_pages(meta)
            queue_count = int(meta.get("retirement_queue_count", 0))
            free_count = int(meta.get("retirement_descriptor_free_count", 0))
            if free_count != 1:
                tail_page = (
                    arena_pages - RETIREMENT_DESCRIPTOR_COPIES
                    if arena_pages >= RETIREMENT_DESCRIPTOR_COPIES
                    else None
                )
                queue_tail_page = meta.get("retirement_queue_tail_page")
                queue_tail_incarnation = meta.get("retirement_queue_tail_incarnation")
                tail_is_queue_tail = (
                    tail_page is not None
                    and queue_count > 0
                    and queue_tail_page == tail_page
                    and queue_tail_incarnation is not None
                )
                return self._no_release_trace(
                    committed_epoch=committed_epoch,
                    queue_count=queue_count,
                    free_count=free_count,
                    arena_pages=arena_pages,
                    preads=0,
                    tail_page=tail_page,
                    tail_incarnation=(
                        int(queue_tail_incarnation) if tail_is_queue_tail else None
                    ),
                    tail_was_queued=bool(tail_is_queue_tail),
                    tail_was_queue_tail=bool(tail_is_queue_tail),
                )
        finally:
            fd.close() if hasattr(fd, "close") else None

        return super().evacuate_live_retirement_arena_tail_step(failpoint=failpoint)


# Keep the claim-bearing type name compact for experiment imports.
LiveTailEvacuationRetirementDescriptorPrimaryStore = (
    BoundedLiveTailEvacuationRetirementDescriptorPrimaryStore
)
