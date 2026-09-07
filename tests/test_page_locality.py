from __future__ import annotations

import unittest

from storage.page_locality import run_fixed_shard_geometry


class PageLocalityTests(unittest.TestCase):
    def test_fixed_sharding_delays_btree_growth_but_does_not_remove_it(self):
        result = run_fixed_shard_geometry(
            checkpoints=(500, 5_000, 20_000),
            shard_count=8,
            page_size=1024,
        )
        rows = result["rows"]
        global_heights = [row["global_height"] for row in rows]
        sharded_heights = [row["max_shard_height"] for row in rows]

        self.assertGreater(max(global_heights), min(global_heights))
        self.assertGreater(max(sharded_heights), min(sharded_heights))
        self.assertTrue(
            any(
                row["max_shard_height"] < row["global_height"]
                for row in rows
            )
        )
        for row in rows:
            self.assertEqual(row["global_height"], row["global_cold_index_pages"])
            self.assertEqual(
                row["target_shard_height"],
                row["target_shard_cold_index_pages"],
            )

    def test_fixed_shard_geometry_rejects_invalid_inputs(self):
        with self.assertRaises(ValueError):
            run_fixed_shard_geometry(checkpoints=(), shard_count=8)
        with self.assertRaises(ValueError):
            run_fixed_shard_geometry(checkpoints=(10, 5), shard_count=8)
        with self.assertRaises(ValueError):
            run_fixed_shard_geometry(checkpoints=(10,), shard_count=0)


if __name__ == "__main__":
    unittest.main()
