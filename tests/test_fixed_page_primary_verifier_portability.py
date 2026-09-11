from __future__ import annotations

import unittest

from verify_fixed_page_primary_results import (
    _require_portable_allocation_observations,
    _without_allocated_bytes,
)


class FixedPagePrimaryVerifierPortabilityTests(unittest.TestCase):
    def test_only_allocated_bytes_may_vary(self) -> None:
        recorded = {
            "row": {
                "file_size_bytes": 16384,
                "allocated_bytes": 8192,
                "semantic_value": 7,
            }
        }
        reproduced = {
            "row": {
                "file_size_bytes": 16384,
                "allocated_bytes": 12288,
                "semantic_value": 7,
            }
        }
        self.assertEqual(
            _without_allocated_bytes(recorded),
            _without_allocated_bytes(reproduced),
        )
        self.assertEqual(
            1,
            _require_portable_allocation_observations(recorded, reproduced),
        )

    def test_nonallocation_drift_is_not_normalized(self) -> None:
        recorded = {
            "row": {
                "file_size_bytes": 16384,
                "allocated_bytes": 8192,
                "semantic_value": 7,
            }
        }
        reproduced = {
            "row": {
                "file_size_bytes": 16384,
                "allocated_bytes": 8192,
                "semantic_value": 8,
            }
        }
        self.assertNotEqual(
            _without_allocated_bytes(recorded),
            _without_allocated_bytes(reproduced),
        )

    def test_invalid_allocation_observation_is_rejected(self) -> None:
        recorded = {
            "row": {
                "file_size_bytes": 4096,
                "allocated_bytes": 4096,
            }
        }
        reproduced = {
            "row": {
                "file_size_bytes": 4096,
                "allocated_bytes": 8192,
            }
        }
        with self.assertRaises(AssertionError):
            _require_portable_allocation_observations(recorded, reproduced)


if __name__ == "__main__":
    unittest.main()
