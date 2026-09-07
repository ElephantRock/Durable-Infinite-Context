from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Iterable

from storage.hash_resize import deterministic_sample_keys, percentile


@dataclass(frozen=True)
class IncrementalLookupTrace:
    key: str
    found: bool
    found_in: str
    generations_touched: int
    slot_probes: int
    page_probes: int

    def to_dict(self) -> dict[str, int | str | bool]:
        return asdict(self)


@dataclass(frozen=True)
class IncrementalInsertTrace:
    inserted_key: str
    live_size_before: int
    live_size_after: int
    current_capacity_before: int
    current_capacity_after: int
    migration_id: int | None
    migration_started: bool
    migration_completed: bool
    migration_source_capacity: int
    migration_source_rows: int
    migration_source_slots_scanned: int
    migration_source_pages_scanned: int
    migration_rows_copied: int
    migration_destination_slot_probes: int
    migration_destination_page_probes: int
    insert_slot_probes: int
    insert_page_probes: int
    mutation_slot_work: int
    allocated_capacity_slots: int
    capacity_amplification_vs_current: float

    def to_dict(self) -> dict[str, int | float | str | bool | None]:
        return asdict(self)


class _GenerationTable:
    def __init__(
        self,
        *,
        capacity: int,
        slots_per_page: int,
        hash_key: bytes,
    ) -> None:
        self.capacity = capacity
        self.slots_per_page = slots_per_page
        self.hash_key = hash_key
        self._slots: list[str | None] = [None] * capacity
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

    def place(self, key: str) -> tuple[int, int, bool]:
        pages: set[int] = set()
        slot_probes = 0
        for position in self._probe_positions(key):
            slot_probes += 1
            pages.add(position // self.slots_per_page)
            current = self._slots[position]
            if current is None:
                self._slots[position] = key
                self.size += 1
                return slot_probes, len(pages), True
            if current == key:
                return slot_probes, len(pages), False
        raise RuntimeError("hash generation is full")

    def lookup(self, key: str) -> tuple[bool, int, int]:
        pages: set[int] = set()
        slot_probes = 0
        for position in self._probe_positions(key):
            slot_probes += 1
            pages.add(position // self.slots_per_page)
            current = self._slots[position]
            if current is None:
                return False, slot_probes, len(pages)
            if current == key:
                return True, slot_probes, len(pages)
        return False, slot_probes, len(pages)

    @property
    def load_factor(self) -> float:
        return self.size / self.capacity


class IncrementalHashIndex:
    """v0.19 candidate: two-generation open addressing with bounded migration scan.

    Capacity doubles when a non-migrating generation would exceed ``max_load``.
    Instead of rehashing the old generation in the triggering mutation, the old table
    remains readable while a new table receives all new writes. Each subsequent insert
    scans at most ``migration_slot_budget`` source slots and copies any live keys found
    there. Lookups search the new generation first and the old generation second until
    migration completes.

    This is an algorithmic page model, not a crash-safe persistent implementation. The
    source *scan* budget is deterministic. Destination linear-probe work remains measured
    rather than assumed constant because clustering can make per-key placement variable.
    """

    def __init__(
        self,
        *,
        initial_capacity: int = 128,
        slots_per_page: int = 64,
        max_load: float = 0.50,
        migration_slot_budget: int = 8,
        hash_key: bytes = b"dic-v019-page-hash",
    ) -> None:
        if initial_capacity <= 0 or initial_capacity & (initial_capacity - 1):
            raise ValueError("initial_capacity must be a positive power of two")
        if slots_per_page <= 0:
            raise ValueError("slots_per_page must be positive")
        if not (0.0 < max_load < 1.0):
            raise ValueError("max_load must be between zero and one")
        if migration_slot_budget <= 0:
            raise ValueError("migration_slot_budget must be positive")

        self.slots_per_page = slots_per_page
        self.max_load = max_load
        self.migration_slot_budget = migration_slot_budget
        self.hash_key = hash_key
        self._current = _GenerationTable(
            capacity=initial_capacity,
            slots_per_page=slots_per_page,
            hash_key=hash_key,
        )
        self._old: _GenerationTable | None = None
        self._migration_cursor = 0
        self._migration_id = 0
        self._migration_source_rows = 0
        self._migration_copied_total = 0
        self.live_size = 0

    def _start_migration(self) -> None:
        if self._old is not None:
            raise RuntimeError("overlapping migration is not allowed")
        old = self._current
        self._old = old
        self._current = _GenerationTable(
            capacity=old.capacity * 2,
            slots_per_page=self.slots_per_page,
            hash_key=self.hash_key,
        )
        self._migration_cursor = 0
        self._migration_id += 1
        self._migration_source_rows = old.size
        self._migration_copied_total = 0

    def _migrate_step(self) -> tuple[int, int, int, int, int, bool, int, int]:
        old = self._old
        if old is None:
            return 0, 0, 0, 0, 0, False, 0, 0

        source_capacity = old.capacity
        source_rows = self._migration_source_rows
        pages: set[int] = set()
        scanned = 0
        copied = 0
        destination_slot_probes = 0
        destination_page_probes = 0

        while (
            scanned < self.migration_slot_budget
            and self._migration_cursor < old.capacity
        ):
            position = self._migration_cursor
            self._migration_cursor += 1
            scanned += 1
            pages.add(position // self.slots_per_page)
            key = old._slots[position]
            if key is None:
                continue
            slot_probes, page_probes, inserted = self._current.place(key)
            destination_slot_probes += slot_probes
            destination_page_probes += page_probes
            if inserted:
                copied += 1
                self._migration_copied_total += 1

        if self._current.load_factor > self.max_load + 1e-12:
            raise RuntimeError(
                "migration budget failed: target generation exceeded max_load before completion"
            )

        completed = self._migration_cursor >= old.capacity
        if completed:
            if self._migration_copied_total != self._migration_source_rows:
                raise AssertionError(
                    (self._migration_copied_total, self._migration_source_rows)
                )
            if self._current.size != self.live_size:
                raise AssertionError((self._current.size, self.live_size))
            self._old = None
            self._migration_cursor = 0
            self._migration_source_rows = 0
            self._migration_copied_total = 0

        return (
            scanned,
            len(pages),
            copied,
            destination_slot_probes,
            destination_page_probes,
            completed,
            source_capacity,
            source_rows,
        )

    def insert(self, key: str) -> IncrementalInsertTrace:
        existing = self.lookup(key)
        if existing.found:
            raise ValueError(f"duplicate key: {key}")

        before_live = self.live_size
        before_capacity = self._current.capacity
        migration_started = False
        migration_id: int | None = self._migration_id if self._old is not None else None

        if self._old is None and (self.live_size + 1) / self._current.capacity > self.max_load:
            self._start_migration()
            migration_started = True
            migration_id = self._migration_id

        slot_probes, page_probes, inserted = self._current.place(key)
        if not inserted:
            raise AssertionError("new key unexpectedly already existed in current generation")
        self.live_size += 1

        (
            scanned,
            source_pages,
            copied,
            destination_slot_probes,
            destination_page_probes,
            migration_completed,
            source_capacity,
            source_rows,
        ) = self._migrate_step()

        if self._old is not None and self._current.load_factor > self.max_load + 1e-12:
            raise RuntimeError("target generation exceeded max_load during migration")
        if self._old is None and self._current.size != self.live_size:
            raise AssertionError((self._current.size, self.live_size))

        allocated = self.allocated_capacity_slots
        amplification = allocated / self._current.capacity
        return IncrementalInsertTrace(
            inserted_key=key,
            live_size_before=before_live,
            live_size_after=self.live_size,
            current_capacity_before=before_capacity,
            current_capacity_after=self._current.capacity,
            migration_id=migration_id,
            migration_started=migration_started,
            migration_completed=migration_completed,
            migration_source_capacity=source_capacity,
            migration_source_rows=source_rows,
            migration_source_slots_scanned=scanned,
            migration_source_pages_scanned=source_pages,
            migration_rows_copied=copied,
            migration_destination_slot_probes=destination_slot_probes,
            migration_destination_page_probes=destination_page_probes,
            insert_slot_probes=slot_probes,
            insert_page_probes=page_probes,
            mutation_slot_work=slot_probes + scanned + destination_slot_probes,
            allocated_capacity_slots=allocated,
            capacity_amplification_vs_current=amplification,
        )

    def lookup(self, key: str) -> IncrementalLookupTrace:
        found, slots, pages = self._current.lookup(key)
        if found:
            return IncrementalLookupTrace(
                key=key,
                found=True,
                found_in="current",
                generations_touched=1,
                slot_probes=slots,
                page_probes=pages,
            )

        old = self._old
        if old is None:
            return IncrementalLookupTrace(
                key=key,
                found=False,
                found_in="none",
                generations_touched=1,
                slot_probes=slots,
                page_probes=pages,
            )

        old_found, old_slots, old_pages = old.lookup(key)
        return IncrementalLookupTrace(
            key=key,
            found=old_found,
            found_in="old" if old_found else "none",
            generations_touched=2,
            slot_probes=slots + old_slots,
            page_probes=pages + old_pages,
        )

    @property
    def migration_active(self) -> bool:
        return self._old is not None

    @property
    def migration_id(self) -> int | None:
        return self._migration_id if self._old is not None else None

    @property
    def migration_cursor(self) -> int:
        return self._migration_cursor

    @property
    def migration_source_capacity(self) -> int:
        return 0 if self._old is None else self._old.capacity

    @property
    def migration_source_rows(self) -> int:
        return 0 if self._old is None else self._migration_source_rows

    @property
    def current_capacity(self) -> int:
        return self._current.capacity

    @property
    def current_load_factor(self) -> float:
        return self._current.load_factor

    @property
    def allocated_capacity_slots(self) -> int:
        return self._current.capacity + (0 if self._old is None else self._old.capacity)

    @property
    def capacity_amplification_vs_current(self) -> float:
        return self.allocated_capacity_slots / self._current.capacity


def _lookup_summary(index: IncrementalHashIndex, total: int, sample_count: int) -> dict:
    sample = deterministic_sample_keys(total, sample_count)
    lookups = [index.lookup(key) for key in sample]
    if not all(item.found for item in lookups):
        raise AssertionError("incremental hash lookup lost an inserted membership key")
    page_probes = [item.page_probes for item in lookups]
    slot_probes = [item.slot_probes for item in lookups]
    generations = [item.generations_touched for item in lookups]
    old_hits = sum(item.found_in == "old" for item in lookups)
    return {
        "lookup_sample_count": len(sample),
        "lookup_page_p50": percentile(page_probes, 0.50),
        "lookup_page_p95": percentile(page_probes, 0.95),
        "lookup_page_max": max(page_probes),
        "lookup_slot_p95": percentile(slot_probes, 0.95),
        "lookup_slot_max": max(slot_probes),
        "lookup_generations_p95": percentile(generations, 0.95),
        "lookup_generations_max": max(generations),
        "lookup_old_generation_hits": old_hits,
    }


def run_incremental_hash_envelope(
    checkpoints: Iterable[int] = (1_000, 4_000, 16_000, 64_000, 256_000),
    *,
    initial_capacity: int = 128,
    slots_per_page: int = 64,
    max_load: float = 0.50,
    migration_slot_budget: int = 8,
    sample_count: int = 512,
) -> dict:
    checkpoints = list(checkpoints)
    if not checkpoints or checkpoints != sorted(checkpoints) or checkpoints[0] <= 0:
        raise ValueError("checkpoints must be positive and increasing")

    index = IncrementalHashIndex(
        initial_capacity=initial_capacity,
        slots_per_page=slots_per_page,
        max_load=max_load,
        migration_slot_budget=migration_slot_budget,
    )
    rows: list[dict] = []
    migration_events: dict[int, dict] = {}
    migration_snapshots: list[dict] = []
    snapshot_taken: set[int] = set()
    previous = 0
    total_copied_rows = 0
    total_source_slots_scanned = 0
    global_max_source_slots = 0
    global_max_rows_copied = 0
    global_max_mutation_slot_work = 0
    global_max_capacity_amplification = 1.0

    for checkpoint in checkpoints:
        interval_max_source_slots = 0
        interval_max_rows_copied = 0
        interval_max_mutation_slot_work = 0
        interval_max_destination_slot_probes = 0
        interval_max_insert_page_probes = 0
        interval_migration_starts = 0
        interval_migration_completions = 0

        for position in range(previous, checkpoint):
            key = f"entity_{position:09d}|deadline"
            trace = index.insert(key)
            if trace.migration_source_slots_scanned > migration_slot_budget:
                raise AssertionError("per-mutation source scan exceeded configured budget")
            if trace.migration_rows_copied > migration_slot_budget:
                raise AssertionError("copied rows exceeded source-slot scan budget")
            if trace.capacity_amplification_vs_current > 1.5 + 1e-12:
                raise AssertionError("two-generation capacity amplification exceeded 1.5x")

            total_copied_rows += trace.migration_rows_copied
            total_source_slots_scanned += trace.migration_source_slots_scanned
            interval_max_source_slots = max(
                interval_max_source_slots, trace.migration_source_slots_scanned
            )
            interval_max_rows_copied = max(
                interval_max_rows_copied, trace.migration_rows_copied
            )
            interval_max_mutation_slot_work = max(
                interval_max_mutation_slot_work, trace.mutation_slot_work
            )
            interval_max_destination_slot_probes = max(
                interval_max_destination_slot_probes,
                trace.migration_destination_slot_probes,
            )
            interval_max_insert_page_probes = max(
                interval_max_insert_page_probes, trace.insert_page_probes
            )
            global_max_source_slots = max(
                global_max_source_slots, trace.migration_source_slots_scanned
            )
            global_max_rows_copied = max(
                global_max_rows_copied, trace.migration_rows_copied
            )
            global_max_mutation_slot_work = max(
                global_max_mutation_slot_work, trace.mutation_slot_work
            )
            global_max_capacity_amplification = max(
                global_max_capacity_amplification,
                trace.capacity_amplification_vs_current,
            )

            if trace.migration_started:
                if trace.migration_id is None:
                    raise AssertionError("migration start missing id")
                interval_migration_starts += 1
                migration_events[trace.migration_id] = {
                    "migration_id": trace.migration_id,
                    "start_live_size": trace.live_size_after,
                    "source_capacity_slots": trace.migration_source_capacity,
                    "source_live_rows": trace.migration_source_rows,
                    "target_capacity_slots": trace.current_capacity_after,
                    "completion_live_size": None,
                }

            active_id = index.migration_id
            if (
                active_id is not None
                and active_id not in snapshot_taken
                and index.migration_cursor >= index.migration_source_capacity // 2
            ):
                summary = _lookup_summary(index, index.live_size, sample_count)
                migration_snapshots.append(
                    {
                        "migration_id": active_id,
                        "live_size": index.live_size,
                        "source_capacity_slots": index.migration_source_capacity,
                        "source_live_rows": index.migration_source_rows,
                        "source_cursor": index.migration_cursor,
                        "current_capacity_slots": index.current_capacity,
                        "current_load_factor": index.current_load_factor,
                        "allocated_capacity_slots": index.allocated_capacity_slots,
                        "capacity_amplification_vs_current": index.capacity_amplification_vs_current,
                        **summary,
                    }
                )
                snapshot_taken.add(active_id)

            if trace.migration_completed:
                if trace.migration_id is None:
                    raise AssertionError("migration completion missing id")
                interval_migration_completions += 1
                event = migration_events[trace.migration_id]
                event["completion_live_size"] = trace.live_size_after
                event["insertions_spanned"] = (
                    trace.live_size_after - int(event["start_live_size"]) + 1
                )

        summary = _lookup_summary(index, checkpoint, sample_count)
        if index.live_size != checkpoint:
            raise AssertionError((index.live_size, checkpoint))
        rows.append(
            {
                "membership_rows": checkpoint,
                "current_capacity_slots": index.current_capacity,
                "migration_active": index.migration_active,
                "allocated_capacity_slots": index.allocated_capacity_slots,
                "capacity_amplification_vs_current": index.capacity_amplification_vs_current,
                "interval_migration_starts": interval_migration_starts,
                "interval_migration_completions": interval_migration_completions,
                "interval_max_source_slots_scanned_per_insert": interval_max_source_slots,
                "interval_max_rows_copied_per_insert": interval_max_rows_copied,
                "interval_max_migration_destination_slot_probes": interval_max_destination_slot_probes,
                "interval_max_mutation_slot_work": interval_max_mutation_slot_work,
                "interval_max_insert_page_probes": interval_max_insert_page_probes,
                "cumulative_rows_copied": total_copied_rows,
                "cumulative_source_slots_scanned": total_source_slots_scanned,
                **summary,
            }
        )
        previous = checkpoint

    incomplete = [
        event for event in migration_events.values() if event["completion_live_size"] is None
    ]
    if incomplete:
        raise AssertionError(f"migration did not complete under sustained inserts: {incomplete}")
    if any(snapshot["lookup_generations_max"] > 2 for snapshot in migration_snapshots):
        raise AssertionError("lookup touched more than two generations")

    return {
        "checkpoints": checkpoints,
        "initial_capacity": initial_capacity,
        "slots_per_page": slots_per_page,
        "max_load": max_load,
        "migration_slot_budget": migration_slot_budget,
        "sample_count": sample_count,
        "rows": rows,
        "migration_events": [migration_events[key] for key in sorted(migration_events)],
        "migration_snapshots": migration_snapshots,
        "global_max_source_slots_scanned_per_insert": global_max_source_slots,
        "global_max_rows_copied_per_insert": global_max_rows_copied,
        "global_max_mutation_slot_work": global_max_mutation_slot_work,
        "global_max_capacity_amplification_vs_current": global_max_capacity_amplification,
        "cumulative_rows_copied": total_copied_rows,
        "cumulative_source_slots_scanned": total_source_slots_scanned,
        "measurement_scope": (
            "algorithmic two-generation open-addressing model: migration budget counts "
            "source slots scanned per insertion; destination linear-probe work and lookup "
            "page probes are measured separately. Capacity amplification counts allocated "
            "slot arrays. It does not claim OS/device I/O, persistence, crash recovery, "
            "filesystem metadata cost, allocator behavior, or production latency."
        ),
    }
