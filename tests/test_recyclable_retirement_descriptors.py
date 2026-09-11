from __future__ import annotations

import unittest

from storage.recyclable_retirement_descriptor_primary import (
    RecyclableRetirementDescriptorPrimaryStore,
)
from storage.retirement_descriptor_pool import (
    RETIREMENT_STATUS_FREE,
    RETIREMENT_STATUS_QUEUED,
    decode_descriptor,
    encode_descriptor,
)


class RecyclableRetirementDescriptorTests(unittest.TestCase):
    def test_primary_class_imports(self) -> None:
        self.assertIsNotNone(RecyclableRetirementDescriptorPrimaryStore)

    def test_queued_descriptor_round_trip(self) -> None:
        encoded = encode_descriptor(
            7,
            incarnation=3,
            status=RETIREMENT_STATUS_QUEUED,
            generation=11,
            cursor_header_page=19,
            remaining_segments=5,
            next_descriptor_page=101,
            next_descriptor_incarnation=4,
        )
        parsed = decode_descriptor(encoded)
        self.assertIsNotNone(parsed)
        epoch, payload = parsed or (None, {})
        self.assertEqual(7, epoch)
        self.assertEqual(3, payload["incarnation"])
        self.assertEqual(RETIREMENT_STATUS_QUEUED, payload["status"])
        self.assertEqual(11, payload["generation"])
        self.assertEqual(19, payload["cursor_header_page"])
        self.assertEqual(5, payload["remaining_segments"])
        self.assertEqual(101, payload["next_descriptor_page"])
        self.assertEqual(4, payload["next_descriptor_incarnation"])

    def test_free_descriptor_round_trip(self) -> None:
        encoded = encode_descriptor(
            8,
            incarnation=3,
            status=RETIREMENT_STATUS_FREE,
            generation=None,
            cursor_header_page=None,
            remaining_segments=0,
            next_descriptor_page=None,
            next_descriptor_incarnation=None,
        )
        parsed = decode_descriptor(encoded)
        self.assertIsNotNone(parsed)
        _epoch, payload = parsed or (None, {})
        self.assertEqual(RETIREMENT_STATUS_FREE, payload["status"])
        self.assertIsNone(payload["generation"])
        self.assertIsNone(payload["cursor_header_page"])
        self.assertEqual(0, payload["remaining_segments"])

    def test_free_descriptor_rejects_retirement_ownership(self) -> None:
        with self.assertRaises(ValueError):
            encode_descriptor(
                1,
                incarnation=1,
                status=RETIREMENT_STATUS_FREE,
                generation=9,
                cursor_header_page=None,
                remaining_segments=0,
                next_descriptor_page=None,
                next_descriptor_incarnation=None,
            )


if __name__ == "__main__":
    unittest.main()
