from __future__ import annotations

import unittest

from simulator.persistence_fault_model import (
    DEFAULT_STALE_TAIL_BYTES,
    POWER_LOSS_CASES,
    run_marker_counterexample,
    run_persistence_fault_matrix,
    run_reclamation_scaling,
)
from storage.persistence_fault_model import (
    derived_recovery_pass,
    make_state,
)


class PersistenceFaultModelTests(unittest.TestCase):
    def test_unsynced_truncate_is_lost_on_power_loss(self) -> None:
        state = make_state(future_rows=0, stale_tail_bytes=DEFAULT_STALE_TAIL_BYTES)
        derived_recovery_pass(state, failpoint="tail_truncated")
        self.assertEqual(state.file.durable_tail_bytes, DEFAULT_STALE_TAIL_BYTES)
        self.assertEqual(state.file.volatile_tail_bytes, DEFAULT_STALE_TAIL_BYTES)

    def test_synced_truncate_survives_power_loss(self) -> None:
        state = make_state(future_rows=0, stale_tail_bytes=DEFAULT_STALE_TAIL_BYTES)
        derived_recovery_pass(state, failpoint="tail_synced")
        self.assertEqual(state.file.durable_tail_bytes, 0)
        self.assertEqual(state.file.volatile_tail_bytes, 0)

    def test_uncommitted_delete_rolls_back_but_committed_delete_survives(self) -> None:
        uncommitted = make_state(future_rows=1, stale_tail_bytes=0)
        derived_recovery_pass(uncommitted, failpoint="future_delete_uncommitted")
        self.assertEqual(uncommitted.overflow.durable_future_rows, 1)

        committed = make_state(future_rows=1, stale_tail_bytes=0)
        derived_recovery_pass(committed, failpoint="future_delete_committed")
        self.assertEqual(committed.overflow.durable_future_rows, 0)

    def test_derived_recovery_converges_for_every_fixed_case(self) -> None:
        out = run_persistence_fault_matrix()
        self.assertEqual(out["cases"], sum(len(v) for v in POWER_LOSS_CASES.values()))
        self.assertEqual(out["cases"], 9)
        for row in out["rows"]:
            self.assertTrue(row["logical_snapshot_exact_after_power_loss"])
            self.assertTrue(row["converged"])
            self.assertTrue(row["second_retry_noop"])
            self.assertEqual(row["final_state"]["durable_future_rows"], 0)
            self.assertEqual(row["final_state"]["durable_tail_bytes"], 0)

    def test_premature_durable_cleanup_marker_strands_tail(self) -> None:
        row = run_marker_counterexample()
        self.assertFalse(row["marker_protocol_converged"])
        self.assertEqual(row["stranded_tail_bytes"], DEFAULT_STALE_TAIL_BYTES)
        self.assertTrue(row["after_power_loss"]["durable_tail_clean_marker"])
        self.assertTrue(row["derived_protocol_can_repair"])

    def test_reclamation_volume_still_scales_with_capacity(self) -> None:
        rows = run_reclamation_scaling()["rows"]
        expected_capacities = [32, 128, 512, 2048]
        self.assertEqual([row["initial_capacity"] for row in rows], expected_capacities)
        observed = [row["retry_reclaimed_tail_bytes"] for row in rows]
        expected = [4096 * (capacity + 2) for capacity in expected_capacities]
        self.assertEqual(observed, expected)
        self.assertTrue(all(row["final_durable_tail_bytes"] == 0 for row in rows))


if __name__ == "__main__":
    unittest.main()
