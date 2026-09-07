from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from typing import Iterable

from storage.bounded_escape import BoundedDomainEscalationIndex
from storage.bounded_placement import BoundedBucketCuckooIndex
from storage.page_locality import btree_stats


@dataclass(frozen=True)
class HybridInsertTrace:
    key: str
    success: bool
    path: str
    primary_mutation_slot_work: int
    overflow_rows_after: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class HybridLookupTrace:
    key: str
    found: bool
    path: str
    primary_page_probes: int
    primary_slot_work: int
    overflow_checked: bool
    overflow_btree_height: int
    modeled_cold_lookup_pages: int

    def to_dict(self) -> dict:
        return asdict(self)


class RareOverflowHybridIndex:
    """Bounded v0.20 primary placement plus an explicit B-tree overflow path.

    The primary path is one finite ``BoundedBucketCuckooIndex``. If primary placement
    exhausts its fixed relocation/stash contract, the key is inserted into a directly
    addressable SQLite ``WITHOUT ROWID`` comparison index. Primary hits return before
    touching overflow; primary misses query overflow by exact key.

    This is an algorithmic/index-geometry experiment. The SQLite connection is
    in-memory and is *not* evidence for crash durability. ``modeled_cold_lookup_pages``
    adds the primary logical page probes to the overflow B-tree root-to-leaf height;
    it does not count pager/filesystem/device behavior or page-split write cost.
    """

    def __init__(
        self,
        capacity_slots: int,
        *,
        bucket_size: int = 4,
        max_kicks: int = 32,
        stash_capacity: int = 8,
        force_same_pair: bool = False,
        page_size: int = 4096,
    ) -> None:
        override = (lambda _key: (0, 1)) if force_same_pair else None
        self.primary = BoundedBucketCuckooIndex(
            capacity_slots,
            bucket_size=bucket_size,
            max_kicks=max_kicks,
            stash_capacity=stash_capacity,
            bucket_pair_override=override,
        )
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(f"PRAGMA page_size={int(page_size)}")
        self.conn.execute("PRAGMA temp_store=MEMORY")
        self.conn.execute("CREATE TABLE overflow(key TEXT PRIMARY KEY) WITHOUT ROWID")
        self.conn.commit()
        self.page_size = int(self.conn.execute("PRAGMA page_size").fetchone()[0])
        self.size = 0
        self._overflow_rows = 0

    def close(self) -> None:
        self.conn.close()

    @property
    def primary_mutation_slot_work_cap(self) -> int:
        return self.primary.theoretical_max_mutation_slot_work

    @property
    def overflow_rows(self) -> int:
        return self._overflow_rows

    @property
    def overflow_height(self) -> int:
        return btree_stats(self.conn, "overflow").height

    def overflow_stats(self) -> dict:
        return btree_stats(self.conn, "overflow").to_dict()

    def overflow_uses_primary_key(self) -> bool:
        rows = self.conn.execute(
            "EXPLAIN QUERY PLAN SELECT key FROM overflow WHERE key=?",
            ("probe",),
        ).fetchall()
        detail = " ".join(str(row[3]).lower() for row in rows)
        return "primary key" in detail and "scan" not in detail

    def insert(self, key: str) -> HybridInsertTrace:
        primary_trace = self.primary.insert(key)
        if primary_trace.success:
            self.size += 1
            return HybridInsertTrace(
                key=key,
                success=True,
                path="primary",
                primary_mutation_slot_work=primary_trace.mutation_slot_work,
                overflow_rows_after=self._overflow_rows,
            )

        self.conn.execute("INSERT INTO overflow(key) VALUES (?)", (key,))
        self.conn.commit()
        self._overflow_rows += 1
        self.size += 1
        return HybridInsertTrace(
            key=key,
            success=True,
            path="overflow",
            primary_mutation_slot_work=primary_trace.mutation_slot_work,
            overflow_rows_after=self._overflow_rows,
        )

    def lookup(self, key: str) -> HybridLookupTrace:
        primary_trace = self.primary.lookup(key)
        if primary_trace.found:
            return HybridLookupTrace(
                key=key,
                found=True,
                path="primary",
                primary_page_probes=primary_trace.page_probes,
                primary_slot_work=primary_trace.slot_work,
                overflow_checked=False,
                overflow_btree_height=0,
                modeled_cold_lookup_pages=primary_trace.page_probes,
            )

        height = self.overflow_height
        row = self.conn.execute(
            "SELECT key FROM overflow WHERE key=?",
            (key,),
        ).fetchone()
        found = row is not None
        return HybridLookupTrace(
            key=key,
            found=found,
            path="overflow" if found else "miss",
            primary_page_probes=primary_trace.page_probes,
            primary_slot_work=primary_trace.slot_work,
            overflow_checked=True,
            overflow_btree_height=height,
            modeled_cold_lookup_pages=primary_trace.page_probes + height,
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
        positions = [total - 1]
    else:
        positions = [round(i * (total - 1) / (count - 1)) for i in range(count)]
    return [f"entity_{position:09d}|deadline" for position in positions]


def run_rare_overflow_envelope(
    checkpoints: Iterable[int] = (1_000, 4_000, 16_000, 64_000, 256_000),
    *,
    overflow_checkpoints: Iterable[int] = (1, 16, 64, 256, 1_024, 4_096, 16_384),
    bucket_size: int = 4,
    max_kicks: int = 32,
    stash_capacity: int = 8,
    sample_count: int = 512,
    page_size: int = 4096,
) -> dict:
    checkpoints = list(checkpoints)
    overflow_checkpoints = list(overflow_checkpoints)
    if not checkpoints or checkpoints != sorted(checkpoints) or checkpoints[0] <= 0:
        raise ValueError("checkpoints must be positive and increasing")
    if (
        not overflow_checkpoints
        or overflow_checkpoints != sorted(overflow_checkpoints)
        or overflow_checkpoints[0] <= 0
    ):
        raise ValueError("overflow_checkpoints must be positive and increasing")

    ordinary_rows: list[dict] = []
    for rows in checkpoints:
        capacity = _capacity_for_rows(rows)
        index = RareOverflowHybridIndex(
            capacity,
            bucket_size=bucket_size,
            max_kicks=max_kicks,
            stash_capacity=stash_capacity,
            page_size=page_size,
        )
        try:
            primary_max_work = 0
            overflow_insertions = 0
            for number in range(rows):
                key = f"entity_{number:09d}|deadline"
                trace = index.insert(key)
                if not trace.success:
                    raise AssertionError("hybrid ordinary insertion failed")
                primary_max_work = max(primary_max_work, trace.primary_mutation_slot_work)
                overflow_insertions += int(trace.path == "overflow")

            sample = _sample_keys(rows, sample_count)
            lookups = [index.lookup(key) for key in sample]
            if not all(trace.found and trace.path == "primary" for trace in lookups):
                raise AssertionError("ordinary hybrid lookup left the primary path")
            if any(trace.overflow_checked for trace in lookups):
                raise AssertionError("ordinary primary hit queried overflow")

            ordinary_rows.append(
                {
                    "membership_rows": rows,
                    "capacity_slots": capacity,
                    "primary_load_factor": rows / capacity,
                    "overflow_insertions": overflow_insertions,
                    "overflow_rows": index.overflow_rows,
                    "primary_max_mutation_slot_work": primary_max_work,
                    "primary_theoretical_mutation_slot_work_cap": index.primary_mutation_slot_work_cap,
                    "primary_lookup_page_max": max(trace.modeled_cold_lookup_pages for trace in lookups),
                    "primary_lookup_overflow_checks": sum(
                        int(trace.overflow_checked) for trace in lookups
                    ),
                    "overflow_uses_primary_key": index.overflow_uses_primary_key(),
                }
            )
        finally:
            index.close()

    stress = RareOverflowHybridIndex(
        1024,
        bucket_size=bucket_size,
        max_kicks=max_kicks,
        stash_capacity=stash_capacity,
        force_same_pair=True,
        page_size=page_size,
    )
    stress_rows: list[dict] = []
    primary_capacity = 2 * bucket_size + stash_capacity
    try:
        admitted_primary: list[str] = []
        for number in range(primary_capacity):
            key = f"collision_primary_{number:06d}"
            trace = stress.insert(key)
            if trace.path != "primary":
                raise AssertionError("concentrated primary filled before expected capacity")
            admitted_primary.append(key)

        previous_overflow = 0
        for target_overflow in overflow_checkpoints:
            for number in range(previous_overflow, target_overflow):
                key = f"collision_overflow_{number:09d}"
                trace = stress.insert(key)
                if trace.path != "overflow":
                    raise AssertionError("post-capacity collision did not use explicit overflow")
                if trace.primary_mutation_slot_work != stress.primary_mutation_slot_work_cap:
                    raise AssertionError("overflow admission did not exhaust the bounded primary")

            stats = stress.overflow_stats()
            if stats["overflow_pages"] != 0:
                raise AssertionError("small overflow keys unexpectedly used SQLite overflow pages")
            if not stress.overflow_uses_primary_key():
                raise AssertionError("overflow exact lookup did not use the primary-key index")

            primary_hit = stress.lookup(admitted_primary[0])
            overflow_key = f"collision_overflow_{target_overflow - 1:09d}"
            overflow_hit = stress.lookup(overflow_key)
            missing = stress.lookup(f"collision_missing_{target_overflow:09d}")

            if not primary_hit.found or primary_hit.path != "primary" or primary_hit.overflow_checked:
                raise AssertionError("primary hit was contaminated by overflow")
            if not overflow_hit.found or overflow_hit.path != "overflow" or not overflow_hit.overflow_checked:
                raise AssertionError("overflow hit was not directly resolved")
            if missing.found or missing.path != "miss" or not missing.overflow_checked:
                raise AssertionError("missing key did not exercise explicit overflow miss path")

            actual_overflow_keys = [
                str(row[0])
                for row in stress.conn.execute("SELECT key FROM overflow ORDER BY key").fetchall()
            ]
            expected_overflow_keys = [
                f"collision_overflow_{i:09d}" for i in range(target_overflow)
            ]
            primary_intact = all(stress.lookup(key).found for key in admitted_primary)

            stress_rows.append(
                {
                    "overflow_rows": target_overflow,
                    "primary_capacity": primary_capacity,
                    "primary_mutation_slot_work_cap": stress.primary_mutation_slot_work_cap,
                    "primary_hit_overflow_checked": primary_hit.overflow_checked,
                    "primary_hit_modeled_pages": primary_hit.modeled_cold_lookup_pages,
                    "overflow_hit_primary_pages": overflow_hit.primary_page_probes,
                    "overflow_hit_btree_height": overflow_hit.overflow_btree_height,
                    "overflow_hit_modeled_pages": overflow_hit.modeled_cold_lookup_pages,
                    "missing_primary_pages": missing.primary_page_probes,
                    "missing_btree_height": missing.overflow_btree_height,
                    "missing_modeled_pages": missing.modeled_cold_lookup_pages,
                    "overflow_btree_height": stats["height"],
                    "overflow_btree_total_pages": stats["total_pages"],
                    "overflow_btree_internal_pages": stats["internal_pages"],
                    "overflow_btree_leaf_pages": stats["leaf_pages"],
                    "all_inserted_found": primary_intact
                    and actual_overflow_keys == expected_overflow_keys,
                }
            )
            previous_overflow = target_overflow
    finally:
        stress.close()

    control = BoundedDomainEscalationIndex(
        1024,
        domain_count=8,
        bucket_size=bucket_size,
        max_kicks=max_kicks,
        stash_capacity=stash_capacity,
        force_same_pair=True,
    )
    control_successes = 0
    control_failures = 0
    control_max_work = 0
    for number in range(256):
        trace = control.insert(f"control_collision_{number:06d}")
        control_max_work = max(control_max_work, trace.total_mutation_slot_work)
        if trace.success:
            control_successes += 1
        else:
            control_failures += 1
    control_missing = control.lookup("control_collision_missing")

    return {
        "checkpoints": checkpoints,
        "overflow_checkpoints": overflow_checkpoints,
        "bucket_size": bucket_size,
        "max_kicks": max_kicks,
        "stash_capacity": stash_capacity,
        "sample_count": sample_count,
        "page_size": page_size,
        "ordinary_rows": ordinary_rows,
        "overflow_stress_rows": stress_rows,
        "v021_d8_control": {
            "attempted": 256,
            "successes": control_successes,
            "failures": control_failures,
            "max_mutation_slot_work": control_max_work,
            "missing_lookup_page_probes": control_missing.page_probes,
            "reserved_capacity_slots": control.reserved_capacity_slots,
        },
        "measurement_scope": (
            "algorithmic bounded-primary plus in-memory SQLite B-tree overflow geometry. "
            "Primary page probes are v0.20 logical pages; overflow B-tree height is dbstat "
            "root-to-leaf index pages for small non-overflow keys. The modeled total adds "
            "those quantities. It excludes crash durability, page-split write cost, pager "
            "bookkeeping, filesystem/device I/O, cache effects, and production latency."
        ),
    }
