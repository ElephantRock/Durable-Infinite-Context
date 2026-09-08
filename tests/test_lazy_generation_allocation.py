from __future__ import annotations

import unittest

from storage.lazy_generation_allocation import (
    PAGE_SIZE,
    eager_generation_residue_bytes,
    lazy_bucket_residue_bytes,
    lazy_stash_residue_bytes,
    new_bucket_pages,
    run_lazy_generation_sweep,
)


class LazyGenerationAllocationTests(unittest.TestCase):
    def test_exact_v024_arithmetic_residue_formulas(self) -> None:
        for capacity in (32, 128, 512, 2048):
            buckets = new_bucket_pages(capacity)
            self.assertEqual(buckets, capacity // 2)
            self.assertEqual(
                lazy_bucket_residue_bytes(capacity, 0),
                PAGE_SIZE,
            )
            self.assertEqual(
                lazy_bucket_residue_bytes(capacity, buckets - 1),
                PAGE_SIZE * (capacity - 1),
            )
            self.assertEqual(
                lazy_stash_residue_bytes(capacity),
                PAGE_SIZE * (capacity + 1),
            )
            self.assertEqual(
                eager_generation_residue_bytes(capacity),
                PAGE_SIZE * (capacity + 2),
            )

    def test_one_lazy_page_write_does_not_bound_file_length_range(self) -> None:
        rows = run_lazy_generation_sweep()["rows"]
        self.assertTrue(all(row["pages_materialized_by_first_write"] == 1 for row in rows))
        self.assertEqual(
            [row["lazy_last_bucket_bytes"] for row in rows],
            [PAGE_SIZE * (capacity - 1) for capacity in (32, 128, 512, 2048)],
        )
        self.assertGreater(rows[-1]["lazy_last_bucket_bytes"], 60 * rows[0]["lazy_last_bucket_bytes"])

    def test_hash_sample_exercises_high_direct_addresses(self) -> None:
        rows = run_lazy_generation_sweep(sample_count=4096)["rows"]
        for row in rows:
            self.assertLessEqual(row["sampled_max_bytes"], row["lazy_last_bucket_bytes"])
            self.assertGreaterEqual(row["sampled_p95_bytes"], row["lazy_last_bucket_bytes"] * 0.85)
        self.assertEqual(
            [row["sampled_p95_bytes"] for row in rows],
            sorted(row["sampled_p95_bytes"] for row in rows),
        )

    def test_naive_lazy_worst_case_remains_linear_in_capacity(self) -> None:
        for capacity in (32, 128, 512, 2048):
            lazy = lazy_bucket_residue_bytes(capacity, new_bucket_pages(capacity) - 1)
            eager = eager_generation_residue_bytes(capacity)
            self.assertEqual(lazy, PAGE_SIZE * (capacity - 1))
            self.assertLess(lazy, eager)
            self.assertGreater(lazy / eager, 0.90)


if __name__ == "__main__":
    unittest.main()
