from __future__ import annotations

import unittest

from simulator.normalized_membership import (
    run_cross_version_case,
    run_v015_manifest_topology_control,
    run_v016_normalized_case,
    run_v016_normalized_topology_case,
    run_v016_read_protection_case,
)


class NormalizedMembershipTests(unittest.TestCase):
    def test_selective_read_no_longer_loads_P_sized_manifest(self):
        small = run_v016_normalized_case(
            entity_count=64, predicate_count=4, history_depth=4, changed_count=1
        )
        large = run_v016_normalized_case(
            entity_count=64, predicate_count=32, history_depth=4, changed_count=1
        )
        self.assertEqual(small["descriptor_storage_bytes"], large["descriptor_storage_bytes"])
        self.assertEqual(
            small["partial_assembly_trace"]["descriptor_bytes"],
            large["partial_assembly_trace"]["descriptor_bytes"],
        )
        self.assertEqual(
            small["partial_assembly_trace"]["membership_bytes"],
            large["partial_assembly_trace"]["membership_bytes"],
        )
        self.assertEqual(small["partial_assembly_trace"]["membership_rows"], 1)
        self.assertEqual(large["partial_assembly_trace"]["membership_rows"], 1)
        self.assertEqual(small["full_assembly_trace"]["membership_rows"], 4)
        self.assertEqual(large["full_assembly_trace"]["membership_rows"], 32)
        self.assertTrue(small["membership_lookup_uses_index"])
        self.assertTrue(large["membership_lookup_uses_index"])
        self.assertTrue(small["membership_enumeration_uses_index"])
        self.assertTrue(large["membership_enumeration_uses_index"])

    def test_normalized_representation_preserves_v015_logical_profile(self):
        case = run_cross_version_case(
            entity_count=64, predicate_count=16, history_depth=4, changed_count=3
        )
        self.assertTrue(case["equal"])

    def test_normalized_state_matches_clean_rebuild_and_heads(self):
        case = run_v016_normalized_case(
            entity_count=64, predicate_count=16, history_depth=8, changed_count=2
        )
        self.assertTrue(case["membership_equal"])
        self.assertTrue(case["materialization_equal"])
        self.assertTrue(case["all_derived_fresh"])
        self.assertTrue(case["head_index_equal"])
        self.assertTrue(case["full_assembly_equal"])
        self.assertTrue(case["partial_assembly_equal"])
        self.assertEqual(case["membership_count"], 16)

    def test_predicate_topology_delta_no_longer_rewrites_P_sized_profile(self):
        control_small = run_v015_manifest_topology_control(
            entity_count=64, predicate_count=4, history_depth=4
        )
        control_large = run_v015_manifest_topology_control(
            entity_count=64, predicate_count=16, history_depth=4
        )
        fixed_small = run_v016_normalized_topology_case(
            entity_count=64, predicate_count=4, history_depth=4
        )
        fixed_large = run_v016_normalized_topology_case(
            entity_count=64, predicate_count=16, history_depth=4
        )
        self.assertGreater(
            control_large["add"]["total_work"], control_small["add"]["total_work"]
        )
        self.assertEqual(
            fixed_large["add"]["total_work"], fixed_small["add"]["total_work"]
        )
        self.assertEqual(
            fixed_large["after_add_profile_bytes"], fixed_small["after_add_profile_bytes"]
        )
        self.assertEqual(fixed_small["membership_after_add"], 5)
        self.assertEqual(fixed_large["membership_after_add"], 17)
        self.assertEqual(fixed_small["membership_after_delete"], 4)
        self.assertEqual(fixed_large["membership_after_delete"], 16)
        self.assertTrue(fixed_small["membership_equal"])
        self.assertTrue(fixed_large["membership_equal"])
        self.assertTrue(fixed_small["materialization_equal"])
        self.assertTrue(fixed_large["materialization_equal"])

    def test_fixed_selective_physical_payload_is_history_and_global_local(self):
        h1 = run_v016_normalized_case(
            entity_count=64, predicate_count=8, history_depth=1, changed_count=1
        )
        h16 = run_v016_normalized_case(
            entity_count=64, predicate_count=8, history_depth=16, changed_count=1
        )
        n64 = run_v016_normalized_case(
            entity_count=64, predicate_count=8, history_depth=4, changed_count=1
        )
        n512 = run_v016_normalized_case(
            entity_count=512, predicate_count=8, history_depth=4, changed_count=1
        )
        self.assertEqual(h1["recovery"]["total_work"], h16["recovery"]["total_work"])
        self.assertEqual(
            h1["partial_assembly_trace"]["payload_bytes"],
            h16["partial_assembly_trace"]["payload_bytes"],
        )
        self.assertEqual(n64["recovery"]["total_work"], n512["recovery"]["total_work"])
        self.assertEqual(
            n64["partial_assembly_trace"]["payload_bytes"],
            n512["partial_assembly_trace"]["payload_bytes"],
        )

    def test_read_protection_remains_facet_local(self):
        case = run_v016_read_protection_case(entity_count=48, index=24)
        self.assertTrue(case["unrelated_partial_present"])
        self.assertTrue(case["affected_partial_blocked"])
        self.assertTrue(case["full_profile_blocked"])
        self.assertTrue(case["final_full_equal"])
        self.assertTrue(case["membership_equal"])
        self.assertTrue(case["materialization_equal"])


if __name__ == "__main__":
    unittest.main()
