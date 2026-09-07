from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

from storage.bounded_placement import (
    BoundedBucketCuckooIndex,
    FixedLinearProbeIndex,
    percentile,
)


@dataclass(frozen=True)
class EscapeInsertTrace:
    key: str
    success: bool
    placed_domain: int | None
    domains_attempted: int
    total_mutation_slot_work: int
    max_single_domain_work: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class EscapeLookupTrace:
    key: str
    found: bool
    found_domain: int | None
    domains_checked: int
    total_slot_work: int
    page_probes: int

    def to_dict(self) -> dict:
        return asdict(self)


class BoundedDomainEscalationIndex:
    """Finite sequence of independent bounded cuckoo placement domains.

    Each domain is a v0.20 ``BoundedBucketCuckooIndex`` with its own keyed hash
    family. Insertion tries domains in deterministic order. A failed domain attempt
    is safe because the v0.20 primitive rolls relocation edits back before returning
    failure. Lookup checks the same finite sequence.

    The first domain deliberately uses the exact v0.20 hash family so ordinary-growth
    behavior remains directly comparable to the previous milestone. Later domains use
    distinct deterministic hash keys.

    This is an algorithmic escape-path model, not a persistent storage engine.
    """

    def __init__(
        self,
        capacity_slots_per_domain: int,
        *,
        domain_count: int = 4,
        bucket_size: int = 4,
        max_kicks: int = 32,
        stash_capacity: int = 8,
        force_same_pair: bool = False,
    ) -> None:
        if domain_count <= 0:
            raise ValueError("domain_count must be positive")
        self.capacity_slots_per_domain = capacity_slots_per_domain
        self.domain_count = domain_count
        self.bucket_size = bucket_size
        self.max_kicks = max_kicks
        self.stash_capacity = stash_capacity
        self.force_same_pair = force_same_pair
        self._domains: list[BoundedBucketCuckooIndex] = []

        override = (lambda _key: (0, 1)) if force_same_pair else None
        for domain in range(domain_count):
            kwargs = {
                "bucket_size": bucket_size,
                "max_kicks": max_kicks,
                "stash_capacity": stash_capacity,
                "bucket_pair_override": override,
            }
            if domain == 0:
                index = BoundedBucketCuckooIndex(capacity_slots_per_domain, **kwargs)
            else:
                index = BoundedBucketCuckooIndex(
                    capacity_slots_per_domain,
                    hash_key_1=f"dic-v021-d{domain}-h1".encode("ascii"),
                    hash_key_2=f"dic-v021-d{domain}-h2".encode("ascii"),
                    choice_key=f"dic-v021-d{domain}-choice".encode("ascii"),
                    victim_key=f"dic-v021-d{domain}-victim".encode("ascii"),
                    **kwargs,
                )
            self._domains.append(index)
        self.size = 0

    @property
    def per_domain_mutation_slot_work_cap(self) -> int:
        return self._domains[0].theoretical_max_mutation_slot_work

    @property
    def theoretical_total_mutation_slot_work_cap(self) -> int:
        return self.domain_count * self.per_domain_mutation_slot_work_cap

    @property
    def theoretical_lookup_page_cap(self) -> int:
        # Each domain can touch two candidate bucket pages plus one stash page.
        return 3 * self.domain_count

    @property
    def reserved_capacity_slots(self) -> int:
        return self.domain_count * self.capacity_slots_per_domain

    @property
    def stash_entries(self) -> int:
        return sum(domain.stash_size for domain in self._domains)

    def insert(self, key: str) -> EscapeInsertTrace:
        total_work = 0
        max_domain_work = 0
        for domain_id, domain in enumerate(self._domains):
            trace = domain.insert(key)
            total_work += trace.mutation_slot_work
            max_domain_work = max(max_domain_work, trace.mutation_slot_work)
            if trace.success:
                self.size += 1
                return EscapeInsertTrace(
                    key=key,
                    success=True,
                    placed_domain=domain_id,
                    domains_attempted=domain_id + 1,
                    total_mutation_slot_work=total_work,
                    max_single_domain_work=max_domain_work,
                )
        return EscapeInsertTrace(
            key=key,
            success=False,
            placed_domain=None,
            domains_attempted=self.domain_count,
            total_mutation_slot_work=total_work,
            max_single_domain_work=max_domain_work,
        )

    def lookup(self, key: str) -> EscapeLookupTrace:
        total_slot_work = 0
        page_probes = 0
        for domain_id, domain in enumerate(self._domains):
            trace = domain.lookup(key)
            total_slot_work += trace.slot_work
            page_probes += trace.page_probes
            if trace.found:
                return EscapeLookupTrace(
                    key=key,
                    found=True,
                    found_domain=domain_id,
                    domains_checked=domain_id + 1,
                    total_slot_work=total_slot_work,
                    page_probes=page_probes,
                )
        return EscapeLookupTrace(
            key=key,
            found=False,
            found_domain=None,
            domains_checked=self.domain_count,
            total_slot_work=total_slot_work,
            page_probes=page_probes,
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


def run_bounded_escape_envelope(
    checkpoints: Iterable[int] = (1_000, 4_000, 16_000, 64_000, 256_000),
    *,
    ordinary_domain_count: int = 4,
    stress_domain_counts: Iterable[int] = (1, 2, 4, 8),
    bucket_size: int = 4,
    max_kicks: int = 32,
    stash_capacity: int = 8,
    slots_per_page: int = 64,
    sample_count: int = 512,
) -> dict:
    checkpoints = list(checkpoints)
    stress_domain_counts = list(stress_domain_counts)
    if not checkpoints or checkpoints != sorted(checkpoints) or checkpoints[0] <= 0:
        raise ValueError("checkpoints must be positive and increasing")
    if not stress_domain_counts or stress_domain_counts != sorted(stress_domain_counts):
        raise ValueError("stress_domain_counts must be positive and increasing")
    if any(count <= 0 for count in stress_domain_counts):
        raise ValueError("stress domain counts must be positive")

    ordinary_rows: list[dict] = []
    for rows in checkpoints:
        capacity = _capacity_for_rows(rows)
        linear = FixedLinearProbeIndex(capacity, slots_per_page=slots_per_page)
        escape = BoundedDomainEscalationIndex(
            capacity,
            domain_count=ordinary_domain_count,
            bucket_size=bucket_size,
            max_kicks=max_kicks,
            stash_capacity=stash_capacity,
        )

        linear_max = 0
        escape_failures = 0
        escape_max_work = 0
        escape_max_domains_attempted = 0
        for index in range(rows):
            key = f"entity_{index:09d}|deadline"
            linear_trace = linear.insert(key)
            if not linear_trace.success:
                raise AssertionError("linear control unexpectedly filled")
            linear_max = max(linear_max, linear_trace.slot_probes)

            trace = escape.insert(key)
            if not trace.success:
                escape_failures += 1
            escape_max_work = max(escape_max_work, trace.total_mutation_slot_work)
            escape_max_domains_attempted = max(
                escape_max_domains_attempted, trace.domains_attempted
            )

        sample = _sample_keys(rows, sample_count)
        lookups = [escape.lookup(key) for key in sample]
        if escape_failures == 0 and not all(trace.found for trace in lookups):
            raise AssertionError("bounded escape lost an ordinary inserted key")
        page_probes = [trace.page_probes for trace in lookups]
        domains_checked = [trace.domains_checked for trace in lookups]

        ordinary_rows.append(
            {
                "membership_rows": rows,
                "capacity_slots_per_domain": capacity,
                "reserved_capacity_slots": escape.reserved_capacity_slots,
                "reserved_capacity_amplification_vs_single_domain": ordinary_domain_count,
                "effective_load_across_reserved_slots": rows / escape.reserved_capacity_slots,
                "linear_max_insert_slot_probes": linear_max,
                "escape_insert_failures": escape_failures,
                "escape_stash_entries": escape.stash_entries,
                "escape_max_domains_attempted": escape_max_domains_attempted,
                "escape_max_mutation_slot_work": escape_max_work,
                "escape_theoretical_mutation_slot_work_cap": (
                    escape.theoretical_total_mutation_slot_work_cap
                ),
                "escape_lookup_domain_p95": percentile(domains_checked, 0.95),
                "escape_lookup_domain_max": max(domains_checked),
                "escape_lookup_page_p95": percentile(page_probes, 0.95),
                "escape_lookup_page_max": max(page_probes),
                "escape_theoretical_lookup_page_cap": escape.theoretical_lookup_page_cap,
            }
        )

    local_domain_capacity = 2 * bucket_size + stash_capacity
    stress_rows: list[dict] = []
    for domain_count in stress_domain_counts:
        # Use a comfortably large table; the discriminating fixture concentrates all
        # keys into the same two buckets in every domain, so global table capacity is
        # intentionally irrelevant.
        escape = BoundedDomainEscalationIndex(
            1024,
            domain_count=domain_count,
            bucket_size=bucket_size,
            max_kicks=max_kicks,
            stash_capacity=stash_capacity,
            force_same_pair=True,
        )
        concentrated_capacity = local_domain_capacity * domain_count
        width = 2 * concentrated_capacity
        successes = 0
        failures = 0
        first_failure_at: int | None = None
        max_work = 0
        max_domains_attempted = 0
        admitted: list[str] = []

        for index in range(width):
            key = f"collision_{domain_count}_{index:06d}"
            trace = escape.insert(key)
            max_work = max(max_work, trace.total_mutation_slot_work)
            max_domains_attempted = max(max_domains_attempted, trace.domains_attempted)
            if trace.success:
                successes += 1
                admitted.append(key)
            else:
                failures += 1
                if first_failure_at is None:
                    first_failure_at = index + 1

        lookups = [escape.lookup(key) for key in admitted]
        if not all(trace.found for trace in lookups):
            raise AssertionError("escape failure corrupted an admitted collision key")
        lookup_page_max = max(trace.page_probes for trace in lookups)
        lookup_domain_max = max(trace.domains_checked for trace in lookups)

        missing = escape.lookup(f"collision_{domain_count}_missing")
        if missing.found:
            raise AssertionError("missing collision key unexpectedly found")

        stress_rows.append(
            {
                "domain_count": domain_count,
                "local_concentrated_capacity_per_domain": local_domain_capacity,
                "concentrated_capacity": concentrated_capacity,
                "colliding_keys_attempted": width,
                "successes": successes,
                "failures": failures,
                "first_failure_at": first_failure_at,
                "max_domains_attempted": max_domains_attempted,
                "max_mutation_slot_work": max_work,
                "theoretical_mutation_slot_work_cap": (
                    escape.theoretical_total_mutation_slot_work_cap
                ),
                "lookup_domain_max_for_admitted": lookup_domain_max,
                "lookup_page_max_for_admitted": lookup_page_max,
                "missing_lookup_domains_checked": missing.domains_checked,
                "missing_lookup_page_probes": missing.page_probes,
                "theoretical_lookup_page_cap": escape.theoretical_lookup_page_cap,
                "reserved_capacity_amplification_vs_single_domain": domain_count,
                "all_admitted_found_after_failures": all(
                    trace.found for trace in lookups
                ),
            }
        )

    return {
        "checkpoints": checkpoints,
        "ordinary_domain_count": ordinary_domain_count,
        "stress_domain_counts": stress_domain_counts,
        "bucket_size": bucket_size,
        "max_kicks": max_kicks,
        "stash_capacity": stash_capacity,
        "slots_per_page": slots_per_page,
        "sample_count": sample_count,
        "per_domain_concentrated_capacity": local_domain_capacity,
        "ordinary_rows": ordinary_rows,
        "collision_stress_rows": stress_rows,
        "measurement_scope": (
            "algorithmic finite-domain escalation model. Mutation work sums v0.20 "
            "bucket inspections, relocation writes, stash writes, and rollback writes "
            "across attempted domains. Lookup pages sum domain-local logical pages. It "
            "does not claim persistence, crash safety, OS/device I/O, or unlimited "
            "admission under arbitrary collisions."
        ),
    }
