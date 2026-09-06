from __future__ import annotations

import unittest

from simulator.compositional_profile import (
    run_composed_read_protection_case,
    run_manifest_topology_case,
    run_v014_monolithic_control,
    run_v015_compositional_case,
)


class CompositionalProfileTests(unittest.TestCase):
    def test_monolithic_control_grows_with_P_for_one_changed_facet(self):
        small = run_v014_monolithic_control(
            entity_count=64, predicate_count=4, history_depth=4, changed_count=1
        )
        large = run_v014_monolithic_control(
            entity_count=64, predicate_count=16, history_depth=4, changed_count=1
        )
        self.assertTrue(small["materialization_equal"])
        self.assertTrue(large["materialization_equal"])
        self.assertGreater(
            large["recovery"]["total_work"], small["recovery"]["total_work"]
        )
        self.assertTrue(small["profile_has_embedded_evidence"])
        self.assertTrue(large["profile_has_embedded_evidence"])

    def test_compositional_one_facet_maintenance_is_independent_of_P(self):
        small = run_v015_compositional_case(
            entity_count=64, predicate_count=4, history_depth=4, changed_count=1
        )
        large = run_v015_compositional_case(
            entity_count=64, predicate_count=16, history_depth=4, changed_count=1
        )
        self.assertEqual(small["recovery"]["total_work"], large["recovery"]["total_work"])
        self.assertFalse(small["profile_has_embedded_evidence"])
        self.assertFalse(large["profile_has_embedded_evidence"])
        self.assertTrue(small["materialization_equal"])
        self.assertTrue(large["materialization_equal"])
        self.assertTrue(small["full_assembly_equal"])
        self.assertTrue(large["full_assembly_equal"])
        self.assertTrue(small["partial_assembly_equal"])
        self.assertTrue(large["partial_assembly_equal"])

    def test_partial_assembly_scales_with_requested_K_while_full_scales_with_P(self):
        k1 = run_v015_compositional_case(
            entity_count=64, predicate_count=16, history_depth=4, changed_count=1
        )
        k4 = run_v015_compositional_case(
            entity_count=64, predicate_count=16, history_depth=4, changed_count=4
        )
        self.assertLess(
            k1["partial_assembly_trace"]["logical_work"],
            k4["partial_assembly_trace"]["logical_work"],
        )
        self.assertEqual(
            k1["full_assembly_trace"]["logical_work"],
            k4["full_assembly_trace"]["logical_work"],
        )
        self.assertEqual(k1["full_assembly_trace"]["facet_reads"], 16)
        self.assertEqual(k1["partial_assembly_trace"]["facet_reads"], 1)
        self.assertEqual(k4["partial_assembly_trace"]["facet_reads"], 4)
        self.assertTrue(k1["full_assembly_equal"])
        self.assertTrue(k4["partial_assembly_equal"])

    def test_compositional_K_maintenance_grows_with_changed_subset_not_total_P(self):
        k1 = run_v015_compositional_case(
            entity_count=64, predicate_count=16, history_depth=4, changed_count=1
        )
        k4 = run_v015_compositional_case(
            entity_count=64, predicate_count=16, history_depth=4, changed_count=4
        )
        p32_k1 = run_v015_compositional_case(
            entity_count=64, predicate_count=32, history_depth=4, changed_count=1
        )
        self.assertGreater(k4["recovery"]["total_work"], k1["recovery"]["total_work"])
        self.assertEqual(p32_k1["recovery"]["total_work"], k1["recovery"]["total_work"])

    def test_fixed_P_K_H_is_independent_of_unrelated_global_cardinality(self):
        small = run_v015_compositional_case(
            entity_count=64, predicate_count=8, history_depth=4, changed_count=1
        )
        large = run_v015_compositional_case(
            entity_count=512, predicate_count=8, history_depth=4, changed_count=1
        )
        self.assertEqual(small["recovery"]["total_work"], large["recovery"]["total_work"])
        self.assertEqual(
            small["partial_assembly_trace"]["logical_work"],
            large["partial_assembly_trace"]["logical_work"],
        )
        self.assertTrue(small["materialization_equal"])
        self.assertTrue(large["materialization_equal"])

    def test_manifest_add_and_remove_predicate_preserves_parity(self):
        case = run_manifest_topology_case(entity_count=48, index=24)
        self.assertEqual(case["before_predicates"], ["deadline", "facet_001"])
        self.assertEqual(
            case["after_add_predicates"], ["deadline", "facet_001", "facet_added"]
        )
        self.assertEqual(case["after_delete_predicates"], ["deadline", "facet_001"])
        self.assertTrue(case["add_materialization_equal"])
        self.assertTrue(case["delete_materialization_equal"])
        self.assertTrue(case["head_index_equal"])
        self.assertTrue(case["all_derived_fresh"])

    def test_composed_read_protection_is_facet_local_and_snapshot_safe(self):
        case = run_composed_read_protection_case(entity_count=48, index=24)
        self.assertTrue(case["unrelated_partial_present"])
        self.assertTrue(case["affected_partial_blocked"])
        self.assertTrue(case["full_profile_blocked"])
        self.assertTrue(case["final_full_equal"])
        self.assertTrue(case["materialization_equal"])


if __name__ == "__main__":
    unittest.main()
