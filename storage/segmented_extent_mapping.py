from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass
from itertools import product
from typing import Iterable

PAGE_SIZE = 4096
BUCKET_SIZE = 4
SEGMENT_BUCKET_PAGES = 16
DATA_PAGE_COPIES = 2
RADIX_BITS_PER_LEVEL = 8
RADIX_FANOUT = 1 << RADIX_BITS_PER_LEVEL
RADIX_LEVELS = 8
RADIX_NODE_COPIES = 2
LOGICAL_SEGMENT_BITS = RADIX_BITS_PER_LEVEL * RADIX_LEVELS
DESCRIPTOR_ENTRY_BYTES = 16
FLAT_ENTRIES_PER_PAGE = PAGE_SIZE // DESCRIPTOR_ENTRY_BYTES
SUPERBLOCK_COPIES = 2
HASH_KEY_1 = b"dic-v024-fixed-h1"


@dataclass(frozen=True)
class SegmentedExtentRow:
    old_capacity: int
    new_capacity: int
    new_bucket_pages: int
    logical_segments: int
    last_segment_id: int
    flat_last_descriptor_residue_bytes: int
    flat_sampled_p50_bytes: int
    flat_sampled_p95_bytes: int
    flat_sampled_max_bytes: int
    radix_sparse_allocation_tail_bytes: int
    radix_sparse_new_descriptor_nodes: int
    radix_sparse_descriptor_reserved_pages: int
    radix_metadata_physical_preads: int
    radix_data_physical_preads: int
    radix_total_physical_preads: int
    radix_metadata_pwrites_new_path: int
    radix_superblock_pwrites: int
    radix_fsyncs: int
    dense_radix_logical_nodes: int
    dense_radix_physical_descriptor_pages: int
    dense_flat_descriptor_pages_single_copy: int
    sampled_keys: int

    def to_dict(self) -> dict:
        return asdict(self)


def _validate_capacity(old_capacity: int, bucket_size: int = BUCKET_SIZE) -> None:
    if old_capacity <= 0 or old_capacity & (old_capacity - 1):
        raise ValueError("old_capacity must be a positive power of two")
    if bucket_size <= 0 or old_capacity % bucket_size:
        raise ValueError("bucket_size must divide old_capacity")


def new_bucket_pages(old_capacity: int, bucket_size: int = BUCKET_SIZE) -> int:
    _validate_capacity(old_capacity, bucket_size)
    return (2 * old_capacity) // bucket_size


def logical_segments(old_capacity: int, bucket_size: int = BUCKET_SIZE) -> int:
    buckets = new_bucket_pages(old_capacity, bucket_size)
    return math.ceil(buckets / SEGMENT_BUCKET_PAGES)


def segment_id_for_bucket(bucket_index: int) -> int:
    if bucket_index < 0:
        raise ValueError("bucket_index must be non-negative")
    return bucket_index // SEGMENT_BUCKET_PAGES


def local_bucket_for_bucket(bucket_index: int) -> int:
    if bucket_index < 0:
        raise ValueError("bucket_index must be non-negative")
    return bucket_index % SEGMENT_BUCKET_PAGES


def flat_descriptor_residue_bytes(segment_id: int) -> int:
    """Sparse file-length span of a packed direct-address extent table.

    The control stores one 16-byte extent descriptor per logical segment in a
    packed descriptor file. Writing a descriptor in page j while no descriptor
    pages are committed extends the process-visible descriptor frontier by
    (j + 1) pages. This is a file-length metric, not allocated-block usage.
    """

    if segment_id < 0:
        raise ValueError("segment_id must be non-negative")
    descriptor_page = segment_id // FLAT_ENTRIES_PER_PAGE
    return (descriptor_page + 1) * PAGE_SIZE


def radix_path_digits(segment_id: int) -> tuple[int, ...]:
    if segment_id < 0 or segment_id >= (1 << LOGICAL_SEGMENT_BITS):
        raise ValueError("segment_id exceeds fixed radix namespace")
    digits = []
    for shift in range(LOGICAL_SEGMENT_BITS - RADIX_BITS_PER_LEVEL, -1, -RADIX_BITS_PER_LEVEL):
        digits.append((segment_id >> shift) & (RADIX_FANOUT - 1))
    return tuple(digits)


def sparse_radix_new_nodes(segment_id: int) -> int:
    """New child nodes needed when mapping one segment in an otherwise empty tree.

    The root is preallocated. A 64-bit logical segment id with 8-bit radix
    digits traverses eight logical nodes total, so seven child nodes are appended.
    The count is independent of the numeric segment id.
    """

    radix_path_digits(segment_id)
    return RADIX_LEVELS - 1


def sparse_radix_allocation_tail_pages(segment_id: int) -> int:
    """Bounded file-length extension for one fresh segment mapping.

    A physical segment reserves two copies for each of its 16 logical bucket
    pages (32 pages). Newly needed radix nodes are append-allocated with two
    fixed copy slots each. The existing parent/root update and superblock commit
    target already allocated fixed locations and therefore do not extend the tail.
    """

    new_nodes = sparse_radix_new_nodes(segment_id)
    return DATA_PAGE_COPIES * SEGMENT_BUCKET_PAGES + RADIX_NODE_COPIES * new_nodes


def sparse_radix_allocation_tail_bytes(segment_id: int) -> int:
    return sparse_radix_allocation_tail_pages(segment_id) * PAGE_SIZE


def dense_radix_logical_nodes(mapped_segments: int) -> int:
    """Logical radix nodes needed for a dense prefix of segment ids [0, K).

    The root exists even for K=0. For K>0, a node at depth d covers
    FANOUT**(RADIX_LEVELS-d) segment ids. The leaf-node count is ceil(K/FANOUT).
    """

    if mapped_segments < 0:
        raise ValueError("mapped_segments must be non-negative")
    if mapped_segments == 0:
        return 1
    if mapped_segments > (1 << LOGICAL_SEGMENT_BITS):
        raise ValueError("mapped_segments exceeds fixed radix namespace")
    nodes = 1
    for exponent in range(RADIX_LEVELS - 1, 0, -1):
        nodes += math.ceil(mapped_segments / (RADIX_FANOUT**exponent))
    return nodes


def dense_flat_descriptor_pages(mapped_segments: int) -> int:
    if mapped_segments < 0:
        raise ValueError("mapped_segments must be non-negative")
    return math.ceil(mapped_segments / FLAT_ENTRIES_PER_PAGE) if mapped_segments else 0


def first_bucket_for_key(key: str, old_capacity: int, bucket_size: int = BUCKET_SIZE) -> int:
    buckets = new_bucket_pages(old_capacity, bucket_size)
    if buckets & (buckets - 1):
        raise ValueError("new bucket count must be a power of two for masked hash control")
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


def radix_lookup_cost() -> dict:
    metadata_preads = SUPERBLOCK_COPIES + RADIX_NODE_COPIES * RADIX_LEVELS
    data_preads = DATA_PAGE_COPIES
    return {
        "superblock_physical_preads": SUPERBLOCK_COPIES,
        "radix_node_physical_preads": RADIX_NODE_COPIES * RADIX_LEVELS,
        "metadata_physical_preads": metadata_preads,
        "data_physical_preads": data_preads,
        "total_physical_preads": metadata_preads + data_preads,
        "logical_radix_nodes": RADIX_LEVELS,
    }


def radix_new_path_write_cost(segment_id: int) -> dict:
    new_nodes = sparse_radix_new_nodes(segment_id)
    return {
        "new_descriptor_node_initial_pwrites": new_nodes,
        "existing_parent_alternate_copy_pwrites": 1,
        "metadata_pwrites_before_commit": new_nodes + 1,
        "superblock_pwrites": 1,
        "fsyncs": 2,
    }


def _recovery_valid(durable: frozenset[str]) -> bool:
    if "superblock" not in durable:
        return True
    return {"data", "nodes", "parent"}.issubset(durable)


def persistence_ordering_experiment() -> dict:
    """Enumerate a small adversarial durability model for segment publication.

    Before an fsync barrier returns, any subset of writes issued since the last
    barrier may have reached durable media. A returned fsync makes all prior
    writes durable. The safe protocol flushes data/mapping dependencies before
    publishing the alternate superblock epoch. The one-barrier control publishes
    the superblock before the only fsync and is therefore vulnerable to an
    adversarial persistence order.
    """

    deps = ("data", "nodes", "parent")

    safe_cases: list[dict] = []
    for bits in product((False, True), repeat=len(deps)):
        durable = frozenset(name for name, bit in zip(deps, bits) if bit)
        safe_cases.append({
            "phase": "before_dependency_fsync",
            "durable": sorted(durable),
            "valid": _recovery_valid(durable),
        })
    durable_deps = frozenset(deps)
    safe_cases.append({
        "phase": "after_dependency_fsync_before_commit",
        "durable": sorted(durable_deps),
        "valid": _recovery_valid(durable_deps),
    })
    for super_durable in (False, True):
        durable = durable_deps | ({"superblock"} if super_durable else set())
        durable = frozenset(durable)
        safe_cases.append({
            "phase": "after_commit_write_before_commit_fsync",
            "durable": sorted(durable),
            "valid": _recovery_valid(durable),
        })
    all_durable = frozenset((*deps, "superblock"))
    safe_cases.append({
        "phase": "after_commit_fsync",
        "durable": sorted(all_durable),
        "valid": _recovery_valid(all_durable),
    })

    control_cases: list[dict] = []
    issued = (*deps, "superblock")
    for bits in product((False, True), repeat=len(issued)):
        durable = frozenset(name for name, bit in zip(issued, bits) if bit)
        control_cases.append({
            "phase": "before_single_fsync",
            "durable": sorted(durable),
            "valid": _recovery_valid(durable),
        })
    control_cases.append({
        "phase": "after_single_fsync",
        "durable": sorted(all_durable),
        "valid": _recovery_valid(all_durable),
    })

    safe_invalid = [case for case in safe_cases if not case["valid"]]
    control_invalid = [case for case in control_cases if not case["valid"]]
    first_counterexample = control_invalid[0] if control_invalid else None

    return {
        "fault_model": (
            "before an fsync barrier returns, any subset of writes since the prior barrier may be durable; "
            "after it returns, all prior writes are durable"
        ),
        "safe_protocol": [
            "write segment data, new radix nodes, and alternate parent copy at epoch T",
            "fsync dependency file",
            "write alternate fixed superblock committing epoch T",
            "fsync superblock file",
        ],
        "one_barrier_control": [
            "write segment data, mapping metadata, alternate parent copy, and alternate superblock epoch T",
            "single fsync",
        ],
        "safe_case_count": len(safe_cases),
        "safe_invalid_case_count": len(safe_invalid),
        "control_case_count": len(control_cases),
        "control_invalid_case_count": len(control_invalid),
        "control_first_counterexample": first_counterexample,
        "safe_cases": safe_cases,
        "control_cases": control_cases,
    }


def run_segmented_extent_sweep(
    capacities: tuple[int, ...] = (1024, 16384, 262144, 4194304),
    sample_count: int = 4096,
    bucket_size: int = BUCKET_SIZE,
) -> dict:
    if sample_count <= 0:
        raise ValueError("sample_count must be positive")

    lookup = radix_lookup_cost()
    rows: list[dict] = []
    for old_capacity in capacities:
        _validate_capacity(old_capacity, bucket_size)
        buckets = new_bucket_pages(old_capacity, bucket_size)
        segments = logical_segments(old_capacity, bucket_size)
        last_segment = segments - 1
        if last_segment >= (1 << LOGICAL_SEGMENT_BITS):
            raise ValueError("capacity exceeds fixed radix namespace")

        sampled_flat_residue: list[int] = []
        for i in range(sample_count):
            key = f"v029-segment-key-{i:05d}"
            bucket = first_bucket_for_key(key, old_capacity, bucket_size)
            segment = segment_id_for_bucket(bucket)
            sampled_flat_residue.append(flat_descriptor_residue_bytes(segment))

        write_cost = radix_new_path_write_cost(last_segment)
        dense_nodes = dense_radix_logical_nodes(segments)
        row = SegmentedExtentRow(
            old_capacity=old_capacity,
            new_capacity=2 * old_capacity,
            new_bucket_pages=buckets,
            logical_segments=segments,
            last_segment_id=last_segment,
            flat_last_descriptor_residue_bytes=flat_descriptor_residue_bytes(last_segment),
            flat_sampled_p50_bytes=percentile_nearest_rank(sampled_flat_residue, 0.50),
            flat_sampled_p95_bytes=percentile_nearest_rank(sampled_flat_residue, 0.95),
            flat_sampled_max_bytes=max(sampled_flat_residue),
            radix_sparse_allocation_tail_bytes=sparse_radix_allocation_tail_bytes(last_segment),
            radix_sparse_new_descriptor_nodes=sparse_radix_new_nodes(last_segment),
            radix_sparse_descriptor_reserved_pages=RADIX_NODE_COPIES * sparse_radix_new_nodes(last_segment),
            radix_metadata_physical_preads=lookup["metadata_physical_preads"],
            radix_data_physical_preads=lookup["data_physical_preads"],
            radix_total_physical_preads=lookup["total_physical_preads"],
            radix_metadata_pwrites_new_path=write_cost["metadata_pwrites_before_commit"],
            radix_superblock_pwrites=write_cost["superblock_pwrites"],
            radix_fsyncs=write_cost["fsyncs"],
            dense_radix_logical_nodes=dense_nodes,
            dense_radix_physical_descriptor_pages=RADIX_NODE_COPIES * dense_nodes,
            dense_flat_descriptor_pages_single_copy=dense_flat_descriptor_pages(segments),
            sampled_keys=sample_count,
        )
        rows.append(row.to_dict())

    return {
        "page_size": PAGE_SIZE,
        "bucket_size": bucket_size,
        "segment_bucket_pages": SEGMENT_BUCKET_PAGES,
        "segment_data_physical_pages": DATA_PAGE_COPIES * SEGMENT_BUCKET_PAGES,
        "descriptor_entry_bytes": DESCRIPTOR_ENTRY_BYTES,
        "flat_entries_per_page": FLAT_ENTRIES_PER_PAGE,
        "radix_bits_per_level": RADIX_BITS_PER_LEVEL,
        "radix_fanout": RADIX_FANOUT,
        "radix_levels": RADIX_LEVELS,
        "logical_segment_bits": LOGICAL_SEGMENT_BITS,
        "radix_node_copies": RADIX_NODE_COPIES,
        "sample_count": sample_count,
        "capacities": list(capacities),
        "rows": rows,
        "flat_control_formula": (
            "packed descriptor page = floor(segment_id/256); first sparse descriptor write extends "
            "the descriptor file by 4096*(page+1) bytes = Theta(C) for last segment"
        ),
        "radix_candidate_formula": (
            "physical segment is append-allocated near the committed frontier; one fresh mapping reserves "
            "2*16 data-page slots plus 2*(8-1) descriptor-node slots = 46 pages = 188416 bytes"
        ),
        "lookup_formula": (
            "2 superblock preads + 2*8 radix-node preads + 2 data-page preads = 20 user-space preads"
        ),
        "dense_descriptor_formula": (
            "dual-copy radix descriptor pages = 2*(1 + sum_{e=1..7} ceil(K/256^e)); total metadata grows "
            "with materialized segment count K even though sparse high-id allocation work is bounded"
        ),
        "measurement_scope": (
            "deterministic arithmetic model of process-visible file-length span, fixed dual-copy segment/node "
            "slots, user-space pread/pwrite counts, and an abstract fsync persistence-ordering fault model. "
            "It does not measure filesystem allocated blocks, device I/O, latency, journaling, or hardware."
        ),
    }
