from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Iterable


@dataclass(frozen=True)
class HashInsertTrace:
    inserted_key: str
    table_size_before: int
    table_size_after: int
    capacity_before: int
    capacity_after: int
    resized: bool
    rehashed_rows: int
    slot_probes: int
    page_probes: int

    def to_dict(self) -> dict[str, int | str | bool]:
        return asdict(self)


@dataclass(frozen=True)
class HashLookupTrace:
    key: str
    found: bool
    slot_probes: int
    page_probes: int

    def to_dict(self) -> dict[str, int | str | bool]:
        return asdict(self)


class StopTheWorldHashIndex:
    """v0.18 candidate/control: conventional bounded-load open addressing.

    This is deliberately an algorithmic page model, not a production storage engine.
    A slot belongs to exactly one fixed-size logical page. Point lookup counts distinct
    logical pages touched by linear probing. Capacity doubles before insertion would
    exceed ``max_load``; that resize reinserts every live row and exposes the mutation
    spike that a conventional stop-the-world hash table hides behind expected O(1)
    lookup.

    The model is intentionally simple because v0.18 asks whether this mechanism is
    sufficient *before* paying for a crash-safe on-disk implementation.
    """

    def __init__(
        self,
        *,
        initial_capacity: int = 128,
        slots_per_page: int = 64,
        max_load: float = 0.50,
        hash_key: bytes = b"dic-v018-page-hash",
    ) -> None:
        if initial_capacity <= 0 or initial_capacity & (initial_capacity - 1):
            raise ValueError("initial_capacity must be a positive power of two")
        if slots_per_page <= 0:
            raise ValueError("slots_per_page must be positive")
        if not (0.0 < max_load < 1.0):
            raise ValueError("max_load must be between zero and one")
        self.capacity = initial_capacity
        self.slots_per_page = slots_per_page
        self.max_load = max_load
        self.hash_key = hash_key
        self._slots: list[str | None] = [None] * self.capacity
        self.size = 0

    def _hash(self, key: str) -> int:
        digest = hashlib.blake2b(
            key.encode("utf-8"), digest_size=8, key=self.hash_key
        ).digest()
        return int.from_bytes(digest, "big")

    def _probe_positions(self, key: str):
        start = self._hash(key) & (self.capacity - 1)
        for offset in range(self.capacity):
            yield (start + offset) & (self.capacity - 1)

    def _place_without_resize(self, key: str) -> tuple[int, int]:
        pages: set[int] = set()
        slot_probes = 0
        for position in self._probe_positions(key):
            slot_probes += 1
            pages.add(position // self.slots_per_page)
            current = self._slots[position]
            if current is None:
                self._slots[position] = key
                self.size += 1
                return slot_probes, len(pages)
            if current == key:
                return slot_probes, len(pages)
        raise RuntimeError("hash table is full")

    def _resize(self, new_capacity: int) -> int:
        old_keys = [key for key in self._slots if key is not None]
        self.capacity = new_capacity
        self._slots = [None] * self.capacity
        self.size = 0
        for key in old_keys:
            self._place_without_resize(key)
        return len(old_keys)

    def insert(self, key: str) -> HashInsertTrace:
        before_size = self.size
        before_capacity = self.capacity
        resized = False
        rehashed_rows = 0
        if (self.size + 1) / self.capacity > self.max_load:
            rehashed_rows = self._resize(self.capacity * 2)
            resized = True
        slot_probes, page_probes = self._place_without_resize(key)
        return HashInsertTrace(
            inserted_key=key,
            table_size_before=before_size,
            table_size_after=self.size,
            capacity_before=before_capacity,
            capacity_after=self.capacity,
            resized=resized,
            rehashed_rows=rehashed_rows,
            slot_probes=slot_probes,
            page_probes=page_probes,
        )

    def lookup(self, key: str) -> HashLookupTrace:
        pages: set[int] = set()
        slot_probes = 0
        for position in self._probe_positions(key):
            slot_probes += 1
            pages.add(position // self.slots_per_page)
            current = self._slots[position]
            if current is None:
                return HashLookupTrace(key, False, slot_probes, len(pages))
            if current == key:
                return HashLookupTrace(key, True, slot_probes, len(pages))
        return HashLookupTrace(key, False, slot_probes, len(pages))

    @property
    def load_factor(self) -> float:
        return self.size / self.capacity

    @property
    def logical_pages(self) -> int:
        return (self.capacity + self.slots_per_page - 1) // self.slots_per_page

    def keys(self) -> list[str]:
        return [key for key in self._slots if key is not None]


def percentile(values: list[int], fraction: float) -> int:
    if not values:
        raise ValueError("values must be non-empty")
    if not (0.0 <= fraction <= 1.0):
        raise ValueError("fraction must be between zero and one")
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round((len(ordered) - 1) * fraction)))
    return ordered[index]


def deterministic_sample_keys(total: int, sample_count: int = 512) -> list[str]:
    if total <= 0:
        raise ValueError("total must be positive")
    count = min(total, sample_count)
    if count == 1:
        positions = [total - 1]
    else:
        positions = [
            round(i * (total - 1) / (count - 1))
            for i in range(count)
        ]
    return [f"entity_{position:09d}|deadline" for position in positions]


def run_hash_resize_envelope(
    checkpoints: Iterable[int] = (1_000, 4_000, 16_000, 64_000, 256_000),
    *,
    initial_capacity: int = 128,
    slots_per_page: int = 64,
    max_load: float = 0.50,
    sample_count: int = 512,
) -> dict:
    checkpoints = list(checkpoints)
    if not checkpoints or checkpoints != sorted(checkpoints) or checkpoints[0] <= 0:
        raise ValueError("checkpoints must be positive and increasing")

    index = StopTheWorldHashIndex(
        initial_capacity=initial_capacity,
        slots_per_page=slots_per_page,
        max_load=max_load,
    )
    rows: list[dict] = []
    resize_events: list[dict] = []
    previous = 0
    total_rehashed_rows = 0
    largest_single_resize = 0

    for checkpoint in checkpoints:
        interval_resize_rows = 0
        interval_resize_events = 0
        interval_max_resize = 0
        interval_insert_page_max = 0
        for position in range(previous, checkpoint):
            key = f"entity_{position:09d}|deadline"
            trace = index.insert(key)
            interval_insert_page_max = max(interval_insert_page_max, trace.page_probes)
            if trace.resized:
                event = trace.to_dict()
                resize_events.append(event)
                interval_resize_events += 1
                interval_resize_rows += trace.rehashed_rows
                total_rehashed_rows += trace.rehashed_rows
                interval_max_resize = max(interval_max_resize, trace.rehashed_rows)
                largest_single_resize = max(largest_single_resize, trace.rehashed_rows)

        sample = deterministic_sample_keys(checkpoint, sample_count)
        lookups = [index.lookup(key) for key in sample]
        if not all(item.found for item in lookups):
            raise AssertionError("hash lookup lost an inserted membership key")
        page_probes = [item.page_probes for item in lookups]
        slot_probes = [item.slot_probes for item in lookups]
        if index.size != checkpoint:
            raise AssertionError((index.size, checkpoint))
        if index.load_factor > max_load + 1e-12:
            raise AssertionError("load-factor invariant violated")

        rows.append(
            {
                "membership_rows": checkpoint,
                "capacity_slots": index.capacity,
                "logical_pages": index.logical_pages,
                "load_factor": index.load_factor,
                "lookup_sample_count": len(sample),
                "lookup_page_p50": percentile(page_probes, 0.50),
                "lookup_page_p95": percentile(page_probes, 0.95),
                "lookup_page_max": max(page_probes),
                "lookup_slot_p95": percentile(slot_probes, 0.95),
                "lookup_slot_max": max(slot_probes),
                "interval_resize_events": interval_resize_events,
                "interval_rehashed_rows": interval_resize_rows,
                "interval_max_single_resize_rows": interval_max_resize,
                "interval_insert_page_max": interval_insert_page_max,
                "cumulative_rehashed_rows": total_rehashed_rows,
                "largest_single_resize_rows": largest_single_resize,
            }
        )
        previous = checkpoint

    return {
        "checkpoints": checkpoints,
        "initial_capacity": initial_capacity,
        "slots_per_page": slots_per_page,
        "max_load": max_load,
        "sample_count": sample_count,
        "rows": rows,
        "resize_events": resize_events,
        "measurement_scope": (
            "algorithmic fixed-page open-addressing model: page probes count distinct "
            "logical slot pages touched by linear probing; resize work counts live rows "
            "that must be rehashed. It does not claim OS/device I/O, cache behavior, "
            "filesystem metadata cost, or crash-safety implementation cost."
        ),
    }
