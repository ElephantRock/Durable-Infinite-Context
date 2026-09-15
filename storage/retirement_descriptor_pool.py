from __future__ import annotations

import os
import struct
import zlib
from typing import Any

from storage.fixed_page_primary import PAGE_SIZE
from storage.fixed_width_radix_primary import MAX_PHYSICAL_PAGE_ID
from storage.reclaiming_radix_primary import NULL_PAGE

RETIREMENT35_MAGIC = b"DICRET35"
RETIREMENT_STATUS_QUEUED = 1
RETIREMENT_STATUS_FREE = 2
RETIREMENT_DESCRIPTOR_COPIES = 2
_PREFIX = struct.Struct(">8sQQQQQQQQ")
_CRC = struct.Struct(">I")
USED_BYTES = _PREFIX.size + _CRC.size


def encode_reference(page: int | None, incarnation: int | None) -> tuple[int, int]:
    if page is None:
        if incarnation is not None:
            raise ValueError("null descriptor page requires null incarnation")
        return NULL_PAGE, 0
    if incarnation is None or int(incarnation) <= 0:
        raise ValueError("descriptor reference requires positive incarnation")
    if int(page) < 0 or int(page) > MAX_PHYSICAL_PAGE_ID:
        raise ValueError("descriptor page exceeds uint64")
    if int(incarnation) > NULL_PAGE:
        raise ValueError("descriptor incarnation exceeds uint64")
    return int(page), int(incarnation)


def decode_reference(page: int, incarnation: int) -> tuple[int | None, int | None]:
    if int(page) == NULL_PAGE:
        if int(incarnation) != 0:
            raise ValueError("null descriptor page has non-null incarnation")
        return None, None
    if int(page) < 0 or int(page) > MAX_PHYSICAL_PAGE_ID:
        raise ValueError("descriptor page exceeds uint64")
    if int(incarnation) <= 0 or int(incarnation) > NULL_PAGE:
        raise ValueError("descriptor incarnation is invalid")
    return int(page), int(incarnation)


def _encode_free_predecessor(
    page: int | None,
    incarnation: int | None,
) -> tuple[int, int]:
    """Encode v0.39 predecessor authority in fields unused by FREE records.

    Historical FREE records encode both fields as NULL_PAGE. Keeping that exact null
    encoding preserves their bytes and canonical outputs. A non-null predecessor uses
    the former generation/cursor slots as (page, incarnation).
    """
    if page is None:
        if incarnation is not None:
            raise ValueError("null free predecessor page requires null incarnation")
        return NULL_PAGE, NULL_PAGE
    if incarnation is None or int(incarnation) <= 0:
        raise ValueError("free predecessor requires positive incarnation")
    if int(page) < 0 or int(page) > MAX_PHYSICAL_PAGE_ID:
        raise ValueError("free predecessor page exceeds uint64")
    if int(incarnation) > NULL_PAGE:
        raise ValueError("free predecessor incarnation exceeds uint64")
    return int(page), int(incarnation)


def _decode_free_predecessor(page: int, incarnation: int) -> tuple[int | None, int | None]:
    if int(page) == NULL_PAGE:
        if int(incarnation) != NULL_PAGE:
            raise ValueError("null free predecessor has non-null incarnation")
        return None, None
    if int(page) < 0 or int(page) > MAX_PHYSICAL_PAGE_ID:
        raise ValueError("free predecessor page exceeds uint64")
    if int(incarnation) <= 0 or int(incarnation) > NULL_PAGE:
        raise ValueError("free predecessor incarnation is invalid")
    return int(page), int(incarnation)


def encode_descriptor(
    epoch: int,
    *,
    incarnation: int,
    status: int,
    generation: int | None,
    cursor_header_page: int | None,
    remaining_segments: int,
    next_descriptor_page: int | None,
    next_descriptor_incarnation: int | None,
    prev_descriptor_page: int | None = None,
    prev_descriptor_incarnation: int | None = None,
) -> bytes:
    if not 0 <= int(epoch) <= NULL_PAGE:
        raise ValueError("retirement epoch exceeds uint64")
    if int(incarnation) <= 0 or int(incarnation) > NULL_PAGE:
        raise ValueError("retirement incarnation must be positive uint64")
    if int(status) not in (RETIREMENT_STATUS_QUEUED, RETIREMENT_STATUS_FREE):
        raise ValueError("unknown retirement descriptor status")

    if int(status) == RETIREMENT_STATUS_QUEUED:
        if prev_descriptor_page is not None or prev_descriptor_incarnation is not None:
            raise ValueError("queued descriptor cannot retain free predecessor authority")
        if generation is None or not 0 <= int(generation) <= NULL_PAGE:
            raise ValueError("queued descriptor requires uint64 generation")
        if cursor_header_page is None:
            raise ValueError("queued descriptor requires ownership cursor")
        if int(remaining_segments) <= 0 or int(remaining_segments) > NULL_PAGE:
            raise ValueError("queued descriptor requires positive remaining count")
        generation_value = int(generation)
        cursor_value = int(cursor_header_page)
        if cursor_value < 0 or cursor_value > MAX_PHYSICAL_PAGE_ID:
            raise ValueError("retirement cursor exceeds uint64")
    else:
        if generation is not None or cursor_header_page is not None or int(remaining_segments) != 0:
            raise ValueError("free descriptor cannot retain retirement ownership")
        generation_value, cursor_value = _encode_free_predecessor(
            prev_descriptor_page,
            prev_descriptor_incarnation,
        )

    next_page, next_incarnation = encode_reference(
        next_descriptor_page, next_descriptor_incarnation
    )
    prefix = _PREFIX.pack(
        RETIREMENT35_MAGIC,
        int(epoch),
        int(incarnation),
        int(status),
        generation_value,
        cursor_value,
        int(remaining_segments),
        next_page,
        next_incarnation,
    )
    crc = zlib.crc32(prefix) & 0xFFFFFFFF
    record = prefix + _CRC.pack(crc)
    if len(record) != USED_BYTES or len(record) > PAGE_SIZE:
        raise AssertionError("v0.35 retirement descriptor layout drifted")
    return record + bytes(PAGE_SIZE - len(record))


def decode_descriptor(data: bytes) -> tuple[int, dict[str, Any]] | None:
    if len(data) != PAGE_SIZE:
        return None
    try:
        values = _PREFIX.unpack(data[: _PREFIX.size])
        (stored_crc,) = _CRC.unpack(data[_PREFIX.size:USED_BYTES])
    except struct.error:
        return None
    (
        magic,
        epoch,
        incarnation,
        status,
        generation,
        cursor,
        remaining,
        next_page,
        next_incarnation,
    ) = values
    prefix = data[: _PREFIX.size]
    if magic != RETIREMENT35_MAGIC or (zlib.crc32(prefix) & 0xFFFFFFFF) != stored_crc:
        return None
    if int(incarnation) <= 0:
        return None
    if int(status) not in (RETIREMENT_STATUS_QUEUED, RETIREMENT_STATUS_FREE):
        return None
    try:
        decoded_next_page, decoded_next_incarnation = decode_reference(
            int(next_page), int(next_incarnation)
        )
    except ValueError:
        return None

    predecessor_page: int | None = None
    predecessor_incarnation: int | None = None
    if int(status) == RETIREMENT_STATUS_QUEUED:
        if int(remaining) <= 0 or int(cursor) == NULL_PAGE:
            return None
        payload_generation: int | None = int(generation)
        payload_cursor: int | None = int(cursor)
    else:
        if int(remaining) != 0:
            return None
        try:
            predecessor_page, predecessor_incarnation = _decode_free_predecessor(
                int(generation), int(cursor)
            )
        except ValueError:
            return None
        payload_generation = None
        payload_cursor = None

    payload = {
        "incarnation": int(incarnation),
        "status": int(status),
        "generation": payload_generation,
        "cursor_header_page": payload_cursor,
        "remaining_segments": int(remaining),
        "next_descriptor_page": decoded_next_page,
        "next_descriptor_incarnation": decoded_next_incarnation,
    }
    # Preserve historical decoded payload shape when predecessor authority is null.
    # v0.35-v0.38 canonical JSON therefore remains byte-stable.
    if predecessor_page is not None:
        payload["prev_descriptor_page"] = predecessor_page
        payload["prev_descriptor_incarnation"] = predecessor_incarnation
    return int(epoch), payload


class TaggedDescriptorIO:
    """Dual-copy descriptor IO with commit-epoch and incarnation validation."""

    @staticmethod
    def offset(base_page: int, copy_slot: int) -> int:
        return (int(base_page) + int(copy_slot)) * PAGE_SIZE

    @classmethod
    def read(
        cls,
        fd: int,
        base_page: int,
        committed_epoch: int,
        *,
        expected_incarnation: int | None = None,
        expected_status: int | None = None,
    ) -> tuple[dict[str, Any], int, int]:
        candidates: list[tuple[int, dict[str, Any], int]] = []
        for copy_slot in (0, 1):
            parsed = decode_descriptor(os.pread(fd, PAGE_SIZE, cls.offset(base_page, copy_slot)))
            if parsed is None:
                continue
            epoch, payload = parsed
            if epoch <= int(committed_epoch):
                candidates.append((epoch, payload, copy_slot))
        if not candidates:
            raise RuntimeError(f"no valid v0.35 descriptor at physical page {base_page}")

        # Select newest committed record first, then validate identity. Filtering by
        # identity first would let an obsolete copy satisfy an old reference after ABA reuse.
        _epoch, payload, slot = max(candidates, key=lambda row: row[0])
        if expected_incarnation is not None and int(payload["incarnation"]) != int(expected_incarnation):
            raise RuntimeError(
                f"descriptor incarnation mismatch: expected {expected_incarnation}, "
                f"observed {payload['incarnation']}"
            )
        if expected_status is not None and int(payload["status"]) != int(expected_status):
            raise RuntimeError(
                f"descriptor status mismatch: expected {expected_status}, observed {payload['status']}"
            )
        return dict(payload), int(slot), 2

    @classmethod
    def write_new(cls, fd: int, base_page: int, epoch: int, **payload: Any) -> int:
        os.pwrite(fd, encode_descriptor(epoch, **payload), cls.offset(base_page, 0))
        return 1

    @classmethod
    def write_existing(
        cls,
        fd: int,
        base_page: int,
        committed_epoch: int,
        new_epoch: int,
        *,
        committed_slot: int | None = None,
        **payload: Any,
    ) -> tuple[int, int]:
        preads = 0
        if committed_slot is None:
            _current, committed_slot, preads = cls.read(fd, base_page, committed_epoch)
        target_slot = 1 - int(committed_slot)
        os.pwrite(fd, encode_descriptor(new_epoch, **payload), cls.offset(base_page, target_slot))
        return preads, 1
