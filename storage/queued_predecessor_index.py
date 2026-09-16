from __future__ import annotations

import os
import struct
import zlib
from typing import Any

from storage.fixed_page_primary import PAGE_SIZE
from storage.reclaiming_radix_primary import NULL_PAGE
from storage.retirement_descriptor_pool import decode_reference, encode_reference

QUEUED_PREDECESSOR44_MAGIC = b"DICQPR44"
_PREFIX = struct.Struct(">8sQQQQ")
_CRC = struct.Struct(">I")
USED_BYTES = _PREFIX.size + _CRC.size


def encode_queued_predecessor(
    epoch: int,
    *,
    incarnation: int,
    prev_page: int | None,
    prev_incarnation: int | None,
) -> bytes:
    if not 0 <= int(epoch) <= NULL_PAGE:
        raise ValueError("queued predecessor epoch exceeds uint64")
    if int(incarnation) <= 0 or int(incarnation) > NULL_PAGE:
        raise ValueError("queued predecessor incarnation must be positive uint64")
    encoded_page, encoded_incarnation = encode_reference(prev_page, prev_incarnation)
    prefix = _PREFIX.pack(
        QUEUED_PREDECESSOR44_MAGIC,
        int(epoch),
        int(incarnation),
        encoded_page,
        encoded_incarnation,
    )
    crc = zlib.crc32(prefix) & 0xFFFFFFFF
    record = prefix + _CRC.pack(crc)
    if len(record) != USED_BYTES or len(record) > PAGE_SIZE:
        raise AssertionError("v0.44 queued predecessor layout drifted")
    return record + bytes(PAGE_SIZE - len(record))


def decode_queued_predecessor(data: bytes) -> tuple[int, dict[str, Any]] | None:
    if len(data) != PAGE_SIZE:
        return None
    try:
        magic, epoch, incarnation, prev_page, prev_incarnation = _PREFIX.unpack(
            data[: _PREFIX.size]
        )
        (stored_crc,) = _CRC.unpack(data[_PREFIX.size:USED_BYTES])
    except struct.error:
        return None
    prefix = data[: _PREFIX.size]
    if magic != QUEUED_PREDECESSOR44_MAGIC:
        return None
    if (zlib.crc32(prefix) & 0xFFFFFFFF) != stored_crc:
        return None
    if int(incarnation) <= 0:
        return None
    try:
        decoded_page, decoded_incarnation = decode_reference(
            int(prev_page), int(prev_incarnation)
        )
    except ValueError:
        return None
    return int(epoch), {
        "incarnation": int(incarnation),
        "prev_page": decoded_page,
        "prev_incarnation": decoded_incarnation,
    }


class QueuedPredecessorIO:
    """Dual-copy page-addressed tagged predecessor authority for v0.44."""

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
    ) -> tuple[dict[str, Any], int, int]:
        candidates: list[tuple[int, dict[str, Any], int]] = []
        for copy_slot in (0, 1):
            parsed = decode_queued_predecessor(
                os.pread(fd, PAGE_SIZE, cls.offset(base_page, copy_slot))
            )
            if parsed is None:
                continue
            epoch, payload = parsed
            if epoch <= int(committed_epoch):
                candidates.append((epoch, payload, copy_slot))
        if not candidates:
            raise RuntimeError(
                f"no valid v0.44 queued predecessor at physical page {base_page}"
            )
        _epoch, payload, slot = max(candidates, key=lambda row: row[0])
        if expected_incarnation is not None and int(payload["incarnation"]) != int(
            expected_incarnation
        ):
            raise RuntimeError(
                "queued predecessor incarnation mismatch: "
                f"expected {expected_incarnation}, observed {payload['incarnation']}"
            )
        return dict(payload), int(slot), 2

    @classmethod
    def write_new(
        cls,
        fd: int,
        base_page: int,
        epoch: int,
        *,
        incarnation: int,
        prev_page: int | None,
        prev_incarnation: int | None,
    ) -> int:
        os.pwrite(
            fd,
            encode_queued_predecessor(
                epoch,
                incarnation=incarnation,
                prev_page=prev_page,
                prev_incarnation=prev_incarnation,
            ),
            cls.offset(base_page, 0),
        )
        return 1

    @classmethod
    def write_existing(
        cls,
        fd: int,
        base_page: int,
        epoch: int,
        *,
        committed_slot: int,
        incarnation: int,
        prev_page: int | None,
        prev_incarnation: int | None,
    ) -> int:
        target_slot = 1 - int(committed_slot)
        os.pwrite(
            fd,
            encode_queued_predecessor(
                epoch,
                incarnation=incarnation,
                prev_page=prev_page,
                prev_incarnation=prev_incarnation,
            ),
            cls.offset(base_page, target_slot),
        )
        return 1
