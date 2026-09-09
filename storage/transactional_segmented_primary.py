from __future__ import annotations

from typing import Any

from storage.segmented_fixed_page_primary import (
    DATA_PAGE_COPIES,
    PAGE_SIZE,
    RADIX_LEVELS,
    RADIX_NODE_COPIES,
    ROOT_NODE_BASE_PAGE,
    SEGMENT_BUCKET_PAGES,
    SegmentedFixedPagePrimaryStore as _SegmentedFixedPagePrimaryStore,
)


class SegmentedFixedPagePrimaryStore(_SegmentedFixedPagePrimaryStore):
    """Transaction-view repair for the v0.30 integrated segmented primary.

    The lower mapping layer publishes new radix nodes at the pending transaction epoch.
    A transaction that materializes more than one sparse segment can therefore update a
    parent to point at a child that is intentionally invisible to committed-epoch reads.
    External/restart reads must retain that committed-only rule, while the writer itself
    requires read-your-writes visibility for nodes already staged by the same transaction.

    This layer supplies exactly that distinction. It does not weaken committed-epoch
    filtering after restart and it does not add a recovery scan.
    """

    def _transaction_node_view(
        self,
        fd: int,
        node_base_page: int,
        committed_epoch: int,
    ) -> tuple[dict[str, Any], int]:
        if node_base_page in self._pending_nodes:
            payload = self._pending_nodes[node_base_page]
            return (
                {
                    "depth": int(payload["depth"]),
                    "entries": dict(payload["entries"]),
                },
                int(self._pending_node_slots[node_base_page]),
            )
        return self._read_node(fd, node_base_page, committed_epoch)

    def _resolve_segment(
        self,
        fd: int,
        segment_id: int,
        committed_epoch: int,
    ) -> int | None:
        if segment_id in self._pending_segments:
            return int(self._pending_segments[segment_id])

        cache_key = (int(committed_epoch), int(segment_id))
        use_committed_cache = not self._pending_nodes
        if use_committed_cache and cache_key in self._segment_cache:
            return self._segment_cache[cache_key]

        digits = self._radix_digits(segment_id)
        node_base = ROOT_NODE_BASE_PAGE
        for depth in range(RADIX_LEVELS):
            payload, _slot = self._transaction_node_view(
                fd, node_base, committed_epoch
            )
            if int(payload["depth"]) != depth:
                raise RuntimeError("radix node depth drifted")
            value = payload["entries"].get(str(digits[depth]))
            if value is None:
                if use_committed_cache:
                    self._segment_cache[cache_key] = None
                return None
            if depth == RADIX_LEVELS - 1:
                segment_base = int(value)
                if use_committed_cache:
                    self._segment_cache[cache_key] = segment_base
                return segment_base
            node_base = int(value)
        raise AssertionError("radix traversal fell through")
