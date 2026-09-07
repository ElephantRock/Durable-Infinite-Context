from __future__ import annotations

import unittest

from storage.incremental_hash import IncrementalHashIndex, run_incremental_hash_envelope


class IncrementalHashIndexTests(unittest.TestCase):
    def test_incremental_migration_caps_source_scan_and_preserves_keys(self) -> None:
        index = IncrementalHashIndex(
            initial_capacity=16,
            slots_per_page=4,
            max_load=0.50,
            migration_slot_budget=2,
        )
        traces = []
        for position in range(40):
            key = f"entity_{position:09d}|deadline"
            trace = index.insert(key)
            traces.append(trace)
            self.assertLessEqual(trace.migration_source_slots_scanned, 2)
            self.assertLessEqual(trace.migration_rows_copied, 2)
            self.assertLessEqual(trace.capacity_amplification_vs_current, 1.5)

        self.assertTrue(any(trace.migration_started for trace in traces))
        self.assertTrue(any(trace.migration_completed for trace in traces))
        for position in range(40):
            key = f"entity_{position:09d}|deadline"
            self.assertTrue(index.lookup(key).found)

    def test_lookup_reads_old_generation_during_migration(self) -> None:
        index = IncrementalHashIndex(
            initial_capacity=16,
            slots_per_page=4,
            max_load=0.50,
            migration_slot_budget=1,
        )
        for position in range(8):
            index.insert(f"entity_{position:09d}|deadline")
        start = index.insert("entity_000000008|deadline")
        self.assertTrue(start.migration_started)
        self.assertTrue(index.migration_active)

        lookups = [
            index.lookup(f"entity_{position:09d}|deadline")
            for position in range(8)
        ]
        self.assertTrue(all(trace.found for trace in lookups))
        self.assertTrue(any(trace.found_in == "old" for trace in lookups))
        self.assertTrue(any(trace.generations_touched == 2 for trace in lookups))
        self.assertTrue(all(trace.generations_touched <= 2 for trace in lookups))

    def test_fixed_envelope_completes_migrations_under_sustained_growth(self) -> None:
        out = run_incremental_hash_envelope(
            checkpoints=(100, 400, 1_600),
            initial_capacity=32,
            slots_per_page=8,
            max_load=0.50,
            migration_slot_budget=8,
            sample_count=64,
        )
        self.assertEqual(out["global_max_source_slots_scanned_per_insert"], 8)
        self.assertLessEqual(out["global_max_rows_copied_per_insert"], 8)
        self.assertLessEqual(out["global_max_capacity_amplification_vs_current"], 1.5)
        self.assertTrue(out["migration_events"])
        self.assertTrue(
            all(event["completion_live_size"] is not None for event in out["migration_events"])
        )
        self.assertTrue(out["migration_snapshots"])
        self.assertTrue(
            all(snapshot["lookup_generations_max"] <= 2 for snapshot in out["migration_snapshots"])
        )


if __name__ == "__main__":
    unittest.main()
