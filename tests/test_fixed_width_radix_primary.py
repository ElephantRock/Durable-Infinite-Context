from __future__ import annotations

import unittest

from storage.fixed_width_radix_primary import (
    FIXED_NODE_USED_BYTES,
    decode_fixed_radix_node,
    encode_fixed_radix_node,
)
from storage.fixed_page_primary import PAGE_SIZE
from storage.segmented_fixed_page_primary import RADIX_FANOUT, RADIX_LEVELS


class FixedWidthRadixNodeTests(unittest.TestCase):
    def test_full_fanout_round_trips_at_uint64_limit(self) -> None:
        base = (1 << 64) - RADIX_FANOUT
        entries = {str(i): base + i for i in range(RADIX_FANOUT)}
        encoded = encode_fixed_radix_node(
            11, {"depth": RADIX_LEVELS - 1, "entries": entries}
        )
        self.assertEqual(PAGE_SIZE, len(encoded))
        self.assertLess(FIXED_NODE_USED_BYTES, PAGE_SIZE)
        parsed = decode_fixed_radix_node(encoded)
        self.assertIsNotNone(parsed)
        epoch, payload = parsed or (None, None)
        self.assertEqual(11, epoch)
        self.assertEqual(RADIX_LEVELS - 1, payload["depth"])
        self.assertEqual(entries, payload["entries"])

    def test_crc_rejects_corruption(self) -> None:
        encoded = bytearray(
            encode_fixed_radix_node(3, {"depth": 0, "entries": {"7": 99}})
        )
        encoded[100] ^= 0x01
        self.assertIsNone(decode_fixed_radix_node(bytes(encoded)))

    def test_pointer_overflow_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            encode_fixed_radix_node(
                1, {"depth": 0, "entries": {"0": 1 << 64}}
            )


if __name__ == "__main__":
    unittest.main()
