from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass
from typing import Iterable

PAGE_SIZE = 4096
BUCKET_SIZE = 4
HASH_KEY_1 = b"dic-v024-fixed-h1"


@dataclass(frozen=True)
class LazyResidueRow:
    old_capacity: int
    new_capacity: int
    new_bucket_pages: int
    eager_extension_bytes: int
    lazy_first_bucket_bytes: int
    lazy_last_bucket_bytes: int
    lazy_stash_bytes: int
    sampled_p50_bytes: int
    sampled_p95_bytes: int
    sampled_max_bytes: int
    sampled_max_bucket: int
    sampled_keys: int
    pages_materialized_by_first_write: int

    def to_dict(self) -> dict:
        return asdict(self)


def _validate_capacity(old_capacity: int, bucket_size: int = BUCKET_SIZE) -> None:
    if old_capacity <= 0 or old_capacity & (old_capacity - 1):
        raise ValueError("old_capacity must be a positive power of two")
    if bucket_size <= 0 or old_capacity % bucket_size:
        raise ValueError("bucket_size must divide old_capacity")


def new_bucket_pages(old_capacity: int, bucket_size: int = BUCKET_SIZE) -> int:
    """Bucket pages in the doubled generation.

    If old capacity is C, the new capacity is 2C. With B slots per bucket,
    the new generation contains 2C/B bucket pages plus one stash page.
    For the v0.24/v0.25 fixed B=4 layout this is C/2 bucket pages.
    """

    _validate_capacity(old_capacity, bucket_size)
    return (2 * old_capacity) // bucket_size


def eager_generation_residue_bytes(old_capacity: int, bucket_size: int = BUCKET_SIZE) -> int:
    """File-length range created by the existing eager generation extension.

    Every logical generation page has two physical copies. A doubled generation
    therefore reserves two copies of all bucket pages plus two stash copies.
    """

    buckets = new_bucket_pages(old_capacity, bucket_size)
    return PAGE_SIZE * 2 * (buckets + 1)


def lazy_bucket_residue_bytes(
    old_capacity: int,
    bucket_index: int,
    bucket_size: int = BUCKET_SIZE,
) -> int:
    """File-length range created by the first write to one new bucket page.

    The v0.24 direct-address layout places logical page p at physical copies
    `2 + 2*p + copy`. At migration start the committed file frontier ends at
    the new generation base. Writing copy 0 of bucket b therefore extends the
    visible file length by `(2*b + 1) * PAGE_SIZE` even though only one 4096-byte
    logical page is materialized.
    """

    buckets = new_bucket_pages(old_capacity, bucket_size)
    if bucket_index < 0 or bucket_index >= buckets:
        raise IndexError(bucket_index)
    return PAGE_SIZE * (2 * bucket_index + 1)


def lazy_stash_residue_bytes(old_capacity: int, bucket_size: int = BUCKET_SIZE) -> int:
    """File-length range created by first writing the new generation stash page."""

    buckets = new_bucket_pages(old_capacity, bucket_size)
    return PAGE_SIZE * (2 * buckets + 1)


def first_bucket_for_key(key: str, old_capacity: int, bucket_size: int = BUCKET_SIZE) -> int:
    buckets = new_bucket_pages(old_capacity, bucket_size)
    digest = hashlib.blake2b(key.encode("utf-8"), digest_size=8, key=HASH_KEY_1).digest()
    return int.from_bytes(digest, "big") & (buckets - 1)


def percentile_nearest_rank(values: Iterable[int], q: float) -> int:
    rows = sorted(int(value) for value in values)
    if not rows:
        raise ValueError("values must be non-empty")
    if not (0.0 < q <= 1.0):
        raise ValueError("q must be in (0, 1]")
    rank = max(1, math.ceil(q * len(rows)))
    return rows[rank - 1]


def run_lazy_generation_sweep(
    capacities: tuple[int, ...] = (32, 128, 512, 2048),
    sample_count: int = 4096,
    bucket_size: int = BUCKET_SIZE,
) -> dict:
    if sample_count <= 0:
        raise ValueError("sample_count must be positive")

    rows: list[dict] = []
    for old_capacity in capacities:
        _validate_capacity(old_capacity, bucket_size)
        bucket_rows: list[tuple[int, int]] = []
        for i in range(sample_count):
            key = f"v028-lazy-key-{i:05d}"
            bucket = first_bucket_for_key(key, old_capacity, bucket_size)
            residue = lazy_bucket_residue_bytes(old_capacity, bucket, bucket_size)
            bucket_rows.append((bucket, residue))

        residues = [residue for _bucket, residue in bucket_rows]
        max_bucket, max_residue = max(bucket_rows, key=lambda row: row[1])
        buckets = new_bucket_pages(old_capacity, bucket_size)
        row = LazyResidueRow(
            old_capacity=old_capacity,
            new_capacity=2 * old_capacity,
            new_bucket_pages=buckets,
            eager_extension_bytes=eager_generation_residue_bytes(old_capacity, bucket_size),
            lazy_first_bucket_bytes=lazy_bucket_residue_bytes(old_capacity, 0, bucket_size),
            lazy_last_bucket_bytes=lazy_bucket_residue_bytes(old_capacity, buckets - 1, bucket_size),
            lazy_stash_bytes=lazy_stash_residue_bytes(old_capacity, bucket_size),
            sampled_p50_bytes=percentile_nearest_rank(residues, 0.50),
            sampled_p95_bytes=percentile_nearest_rank(residues, 0.95),
            sampled_max_bytes=max_residue,
            sampled_max_bucket=max_bucket,
            sampled_keys=sample_count,
            pages_materialized_by_first_write=1,
        )
        rows.append(row.to_dict())

    return {
        "page_size": PAGE_SIZE,
        "bucket_size": bucket_size,
        "sample_count": sample_count,
        "capacities": list(capacities),
        "rows": rows,
        "address_formula": "physical_page_index = 2 + 2*logical_page_id + copy_slot",
        "lazy_first_write_formula": "bucket b => (2*b+1)*4096 bytes beyond committed frontier",
        "algebraic_bucket_worst_case": "4096*(C-1)=Theta(C) for bucket_size=4",
        "algebraic_stash_case": "4096*(C+1)=Theta(C) for bucket_size=4",
        "eager_formula": "4096*(C+2)=Theta(C) for bucket_size=4",
        "measurement_scope": (
            "deterministic arithmetic file-length model using the v0.24 dual-copy direct-address layout "
            "and its keyed BLAKE2 first-bucket hash. Bytes are process-visible file-length range beyond "
            "the committed frontier, not allocated filesystem blocks, storage-device writes, or latency."
        ),
    }
