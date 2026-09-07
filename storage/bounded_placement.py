from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Callable, Iterable


@dataclass(frozen=True)
class LinearInsertTrace:
    key: str
    success: bool
    slot_probes: int
    page_probes: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class CuckooInsertTrace:
    key: str
    success: bool
    stashed: bool
    bucket_slot_inspections: int
    relocations: int
    stash_write: int
    rollback_writes: int
    mutation_slot_work: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class CuckooLookupTrace:
    key: str
    found: bool
    bucket_slot_inspections: int
    stash_inspections: int
    page_probes: int

    @property
    def slot_work(self) -> int:
        return self.bucket_slot_inspections + self.stash_inspections

    def to_dict(self) -> dict:
        out = asdict(self)
        out["slot_work"] = self.slot_work
        return out


class FixedLinearProbeIndex:
    """Fixed-capacity v0.19-style linear probing control."""

    def __init__(
        self,
        capacity_slots: int,
        *,
        slots_per_page: int = 64,
        hash_key: bytes = b"dic-v018-page-hash",
        start_slot_override: Callable[[str], int] | None = None,
    ) -> None:
        if capacity_slots <= 0 or capacity_slots & (capacity_slots - 1):
            raise ValueError("capacity_slots must be a positive power of two")
        if slots_per_page <= 0:
            raise ValueError("slots_per_page must be positive")
        self.capacity_slots = capacity_slots
        self.slots_per_page = slots_per_page
        self.hash_key = hash_key
        self.start_slot_override = start_slot_override
        self._slots: list[str | None] = [None] * capacity_slots
        self.size = 0

    def _hash(self, key: str) -> int:
        digest = hashlib.blake2b(
            key.encode("utf-8"), digest_size=8, key=self.hash_key
        ).digest()
        return int.from_bytes(digest, "big")

    def _start(self, key: str) -> int:
        if self.start_slot_override is not None:
            return int(self.start_slot_override(key)) & (self.capacity_slots - 1)
        return self._hash(key) & (self.capacity_slots - 1)

    def insert(self, key: str) -> LinearInsertTrace:
        start = self._start(key)
        pages: set[int] = set()
        for offset in range(self.capacity_slots):
            position = (start + offset) & (self.capacity_slots - 1)
            pages.add(position // self.slots_per_page)
            if self._slots[position] is None:
                self._slots[position] = key
                self.size += 1
                return LinearInsertTrace(key, True, offset + 1, len(pages))
        return LinearInsertTrace(key, False, self.capacity_slots, len(pages))

    def lookup(self, key: str) -> tuple[bool, int, int]:
        start = self._start(key)
        pages: set[int] = set()
        for offset in range(self.capacity_slots):
            position = (start + offset) & (self.capacity_slots - 1)
            pages.add(position // self.slots_per_page)
            current = self._slots[position]
            if current is None:
                return False, offset + 1, len(pages)
            if current == key:
                return True, offset + 1, len(pages)
        return False, self.capacity_slots, len(pages)


class BoundedBucketCuckooIndex:
    """Two-choice bucketized cuckoo placement with explicit finite bounds.

    The experiment uses unique membership keys. An insertion inspects two candidate
    buckets and then performs at most ``max_kicks`` relocations. If no placement is
    found, one displaced key may enter a finite stash. If the stash is full, all
    relocation edits are rolled back and the insertion fails explicitly rather than
    extending an unbounded probe chain.

    This is an algorithmic placement model, not a persistent storage engine.
    """

    def __init__(
        self,
        capacity_slots: int,
        *,
        bucket_size: int = 4,
        max_kicks: int = 32,
        stash_capacity: int = 8,
        hash_key_1: bytes = b"dic-v020-cuckoo-h1",
        hash_key_2: bytes = b"dic-v020-cuckoo-h2",
        choice_key: bytes = b"dic-v020-cuckoo-choice",
        victim_key: bytes = b"dic-v020-cuckoo-victim",
        bucket_pair_override: Callable[[str], tuple[int, int]] | None = None,
    ) -> None:
        if capacity_slots <= 0 or capacity_slots & (capacity_slots - 1):
            raise ValueError("capacity_slots must be a positive power of two")
        if bucket_size <= 0 or capacity_slots % bucket_size:
            raise ValueError("bucket_size must divide capacity_slots")
        bucket_count = capacity_slots // bucket_size
        if bucket_count <= 1 or bucket_count & (bucket_count - 1):
            raise ValueError("bucket count must be a power of two greater than one")
        if max_kicks <= 0:
            raise ValueError("max_kicks must be positive")
        if stash_capacity < 0:
            raise ValueError("stash_capacity must be non-negative")

        self.capacity_slots = capacity_slots
        self.bucket_size = bucket_size
        self.bucket_count = bucket_count
        self.max_kicks = max_kicks
        self.stash_capacity = stash_capacity
        self.hash_key_1 = hash_key_1
        self.hash_key_2 = hash_key_2
        self.choice_key = choice_key
        self.victim_key = victim_key
        self.bucket_pair_override = bucket_pair_override
        self._buckets: list[list[str | None]] = [
            [None] * bucket_size for _ in range(bucket_count)
        ]
        self._stash: list[str] = []
        self.size = 0

    def _hash(self, key: str, hash_key: bytes) -> int:
        digest = hashlib.blake2b(
            key.encode("utf-8"), digest_size=8, key=hash_key
        ).digest()
        return int.from_bytes(digest, "big")

    def bucket_pair(self, key: str) -> tuple[int, int]:
        mask = self.bucket_count - 1
        if self.bucket_pair_override is not None:
            first, second = self.bucket_pair_override(key)
            first &= mask
            second &= mask
        else:
            first = self._hash(key, self.hash_key_1) & mask
            second = self._hash(key, self.hash_key_2) & mask
        if second == first:
            second = (second + 1) & mask
        return first, second

    def _first_empty(self, bucket: int) -> tuple[int | None, int]:
        inspections = 0
        for index, current in enumerate(self._buckets[bucket]):
            inspections += 1
            if current is None:
                return index, inspections
        return None, inspections

    def _victim_slot(self, key: str, kick: int) -> int:
        return self._hash(f"{key}|{kick}", self.victim_key) % self.bucket_size

    @property
    def stash_size(self) -> int:
        return len(self._stash)

    @property
    def theoretical_max_mutation_slot_work(self) -> int:
        # Worst case is explicit failure: two candidate bucket scans, one full
        # bucket scan and relocation write for every kick, then rollback of every
        # relocation write. A successful stash write is cheaper than rollback.
        return (
            2 * self.bucket_size
            + self.max_kicks * self.bucket_size
            + self.max_kicks
            + self.max_kicks
        )

    def insert(self, key: str) -> CuckooInsertTrace:
        first, second = self.bucket_pair(key)
        inspections = 0
        relocations = 0
        stash_write = 0
        rollback_writes = 0

        for bucket in (first, second):
            empty, scanned = self._first_empty(bucket)
            inspections += scanned
            if empty is not None:
                self._buckets[bucket][empty] = key
                self.size += 1
                return CuckooInsertTrace(
                    key,
                    True,
                    False,
                    inspections,
                    relocations,
                    stash_write,
                    rollback_writes,
                    inspections,
                )

        current = key
        target = first if self._hash(key, self.choice_key) & 1 == 0 else second
        rollback_log: list[tuple[int, int, str]] = []

        for kick in range(self.max_kicks):
            empty, scanned = self._first_empty(target)
            inspections += scanned
            if empty is not None:
                self._buckets[target][empty] = current
                self.size += 1
                work = inspections + relocations
                return CuckooInsertTrace(
                    key,
                    True,
                    False,
                    inspections,
                    relocations,
                    stash_write,
                    rollback_writes,
                    work,
                )

            victim_slot = self._victim_slot(current, kick)
            victim = self._buckets[target][victim_slot]
            if victim is None:
                raise AssertionError("full bucket unexpectedly contained an empty slot")
            rollback_log.append((target, victim_slot, victim))
            self._buckets[target][victim_slot] = current
            relocations += 1
            current = victim

            victim_first, victim_second = self.bucket_pair(current)
            if victim_first == target:
                target = victim_second
            elif victim_second == target:
                target = victim_first
            else:
                raise AssertionError("evicted key was not stored in a candidate bucket")

        if len(self._stash) < self.stash_capacity:
            self._stash.append(current)
            stash_write = 1
            self.size += 1
            work = inspections + relocations + stash_write
            return CuckooInsertTrace(
                key,
                True,
                True,
                inspections,
                relocations,
                stash_write,
                rollback_writes,
                work,
            )

        for bucket, slot, previous in reversed(rollback_log):
            self._buckets[bucket][slot] = previous
            rollback_writes += 1

        work = inspections + relocations + rollback_writes
        return CuckooInsertTrace(
            key,
            False,
            False,
            inspections,
            relocations,
            stash_write,
            rollback_writes,
            work,
        )

    def lookup(self, key: str) -> CuckooLookupTrace:
        first, second = self.bucket_pair(key)
        bucket_inspections = 0
        pages: set[int | tuple[str, int]] = set()

        for bucket in (first, second):
            pages.add(bucket)
            for current in self._buckets[bucket]:
                bucket_inspections += 1
                if current == key:
                    return CuckooLookupTrace(
                        key, True, bucket_inspections, 0, len(pages)
                    )
                if current is None:
                    break

        stash_inspections = 0
        if self._stash:
            pages.add(("stash", 0))
        for current in self._stash:
            stash_inspections += 1
            if current == key:
                return CuckooLookupTrace(
                    key, True, bucket_inspections, stash_inspections, len(pages)
                )

        return CuckooLookupTrace(
            key, False, bucket_inspections, stash_inspections, len(pages)
        )


def _capacity_for_rows(rows: int) -> int:
    if rows <= 0:
        raise ValueError("rows must be positive")
    capacity = 1
    while capacity < 2 * rows:
        capacity *= 2
    return capacity


def _sample_keys(total: int, count: int = 512) -> list[str]:
    count = min(total, count)
    if count == 1:
        points = [total - 1]
    else:
        points = [round(i * (total - 1) / (count - 1)) for i in range(count)]
    return [f"entity_{point:09d}|deadline" for point in points]


def percentile(values: list[int], fraction: float) -> int:
    if not values:
        raise ValueError("values must be non-empty")
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round((len(ordered) - 1) * fraction)))
    return ordered[index]


def run_bounded_placement_envelope(
    checkpoints: Iterable[int] = (1_000, 4_000, 16_000, 64_000, 256_000),
    *,
    bucket_size: int = 4,
    max_kicks: int = 32,
    stash_capacity: int = 8,
    slots_per_page: int = 64,
    sample_count: int = 512,
    collision_widths: Iterable[int] = (8, 16, 17, 32, 64, 128),
) -> dict:
    checkpoints = list(checkpoints)
    collision_widths = list(collision_widths)
    if not checkpoints or checkpoints != sorted(checkpoints):
        raise ValueError("checkpoints must be increasing")
    if not collision_widths or collision_widths != sorted(collision_widths):
        raise ValueError("collision_widths must be increasing")

    ordinary_rows: list[dict] = []

    for rows in checkpoints:
        capacity = _capacity_for_rows(rows)
        linear = FixedLinearProbeIndex(capacity, slots_per_page=slots_per_page)
        cuckoo = BoundedBucketCuckooIndex(
            capacity,
            bucket_size=bucket_size,
            max_kicks=max_kicks,
            stash_capacity=stash_capacity,
        )

        linear_max = 0
        linear_page_max = 0
        cuckoo_max_work = 0
        cuckoo_max_relocations = 0
        cuckoo_failures = 0
        cuckoo_stash_peak = 0

        for index in range(rows):
            key = f"entity_{index:09d}|deadline"
            linear_trace = linear.insert(key)
            if not linear_trace.success:
                raise AssertionError("fixed linear control unexpectedly filled")
            linear_max = max(linear_max, linear_trace.slot_probes)
            linear_page_max = max(linear_page_max, linear_trace.page_probes)

            cuckoo_trace = cuckoo.insert(key)
            if not cuckoo_trace.success:
                cuckoo_failures += 1
            cuckoo_max_work = max(cuckoo_max_work, cuckoo_trace.mutation_slot_work)
            cuckoo_max_relocations = max(cuckoo_max_relocations, cuckoo_trace.relocations)
            cuckoo_stash_peak = max(cuckoo_stash_peak, cuckoo.stash_size)

        sample = _sample_keys(rows, sample_count)
        lookup_traces = [cuckoo.lookup(key) for key in sample]
        if cuckoo_failures == 0 and not all(trace.found for trace in lookup_traces):
            raise AssertionError("cuckoo lookup lost an ordinary inserted key")
        lookup_pages = [trace.page_probes for trace in lookup_traces]
        lookup_slot_work = [trace.slot_work for trace in lookup_traces]

        ordinary_rows.append(
            {
                "membership_rows": rows,
                "capacity_slots": capacity,
                "load_factor": rows / capacity,
                "linear_max_insert_slot_probes": linear_max,
                "linear_max_insert_page_probes": linear_page_max,
                "cuckoo_insert_failures": cuckoo_failures,
                "cuckoo_stash_peak": cuckoo_stash_peak,
                "cuckoo_max_relocations": cuckoo_max_relocations,
                "cuckoo_max_mutation_slot_work": cuckoo_max_work,
                "cuckoo_theoretical_max_mutation_slot_work": (
                    cuckoo.theoretical_max_mutation_slot_work
                ),
                "cuckoo_lookup_page_p95": percentile(lookup_pages, 0.95),
                "cuckoo_lookup_page_max": max(lookup_pages),
                "cuckoo_lookup_slot_p95": percentile(lookup_slot_work, 0.95),
                "cuckoo_lookup_slot_max": max(lookup_slot_work),
            }
        )

    stress_rows: list[dict] = []
    stress_capacity = 2048
    for width in collision_widths:
        linear = FixedLinearProbeIndex(
            stress_capacity,
            slots_per_page=slots_per_page,
            start_slot_override=lambda _key: 0,
        )
        cuckoo = BoundedBucketCuckooIndex(
            stress_capacity,
            bucket_size=bucket_size,
            max_kicks=max_kicks,
            stash_capacity=stash_capacity,
            bucket_pair_override=lambda _key: (0, 1),
        )

        linear_max = 0
        cuckoo_max_work = 0
        cuckoo_max_relocations = 0
        cuckoo_failures = 0

        for index in range(width):
            key = f"stress_{index:04d}"
            linear_trace = linear.insert(key)
            if not linear_trace.success:
                raise AssertionError("stress linear control unexpectedly filled")
            linear_max = max(linear_max, linear_trace.slot_probes)

            cuckoo_trace = cuckoo.insert(key)
            cuckoo_max_work = max(cuckoo_max_work, cuckoo_trace.mutation_slot_work)
            cuckoo_max_relocations = max(cuckoo_max_relocations, cuckoo_trace.relocations)
            if not cuckoo_trace.success:
                cuckoo_failures += 1

        successful = width - cuckoo_failures
        found_count = sum(
            1
            for index in range(width)
            if cuckoo.lookup(f"stress_{index:04d}").found
        )
        if found_count != successful:
            raise AssertionError((width, found_count, successful))

        stress_rows.append(
            {
                "colliding_keys": width,
                "linear_max_insert_slot_probes": linear_max,
                "cuckoo_successes": successful,
                "cuckoo_failures": cuckoo_failures,
                "cuckoo_stash_size": cuckoo.stash_size,
                "cuckoo_max_relocations": cuckoo_max_relocations,
                "cuckoo_max_mutation_slot_work": cuckoo_max_work,
                "cuckoo_theoretical_max_mutation_slot_work": (
                    cuckoo.theoretical_max_mutation_slot_work
                ),
                "cuckoo_found_keys_after_failures": found_count,
            }
        )

    return {
        "checkpoints": checkpoints,
        "bucket_size": bucket_size,
        "max_kicks": max_kicks,
        "stash_capacity": stash_capacity,
        "slots_per_page": slots_per_page,
        "sample_count": sample_count,
        "ordinary_rows": ordinary_rows,
        "collision_widths": collision_widths,
        "collision_stress_rows": stress_rows,
        "measurement_scope": (
            "algorithmic fixed-capacity placement model. Linear control counts slot/page "
            "probes. Cuckoo candidate counts bucket-slot inspections, relocation writes, "
            "stash writes, and rollback writes. It does not claim OS/device I/O, "
            "persistence, crash safety, allocator cost, or a zero-failure guarantee."
        ),
    }
