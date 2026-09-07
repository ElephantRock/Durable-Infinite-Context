from __future__ import annotations

import unittest

from storage.hash_resize import StopTheWorldHashIndex, run_hash_resize_envelope


class HashResizeTests(unittest.TestCase):
    def test_insert_and_lookup_preserve_exact_keys(self) -> None:
        index = StopTheWorldHashIndex(initial_capacity=8, slots_per_page=4, max_load=0.5)
        keys = [f"entity_{i}|deadline" for i in range(40)]
        for key in keys:
            index.insert(key)
        self.assertEqual(index.size, len(keys))
        self.assertLessEqual(index.load_factor, 0.5)
        for key in keys:
            trace = index.lookup(key)
            self.assertTrue(trace.found)
            self.assertGreaterEqual(trace.page_probes, 1)
        self.assertFalse(index.lookup("missing|deadline").found)

    def test_resize_rehashes_all_prior_live_rows(self) -> None:
        index = StopTheWorldHashIndex(initial_capacity=8, slots_per_page=4, max_load=0.5)
        traces = [index.insert(f"entity_{i}|deadline") for i in range(9)]
        resize_traces = [trace for trace in traces if trace.resized]
        self.assertGreaterEqual(len(resize_traces), 2)
        for trace in resize_traces:
            self.assertEqual(trace.rehashed_rows, trace.table_size_before)
            self.assertEqual(trace.capacity_after, trace.capacity_before * 2)

    def test_fixed_load_lookup_is_local_but_resize_spike_grows(self) -> None:
        result = run_hash_resize_envelope(
            checkpoints=(1_000, 4_000, 16_000),
            sample_count=128,
        )
        rows = result["rows"]
        self.assertTrue(all(row["lookup_page_p95"] <= 2 for row in rows))
        spikes = [row["largest_single_resize_rows"] for row in rows]
        self.assertEqual(spikes, sorted(spikes))
        self.assertGreater(spikes[-1], spikes[0])
        self.assertGreater(result["resize_events"][-1]["rehashed_rows"], 0)


if __name__ == "__main__":
    unittest.main()
