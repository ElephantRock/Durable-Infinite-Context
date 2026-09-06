from __future__ import annotations

import unittest

from core.models import Assertion, EvidenceRecord
from simulator.cascade import alias, assertion_id, build_cascade_store, evidence_id, subject_id
from state.cascade import CascadeMaintainer, CascadeMaterialization
from state.scanfree_cascade import ScanFreeCascadeMaintainer


class ScanFreeCascadeTests(unittest.TestCase):
    def test_canonical_object_update_and_local_rebuild_do_not_scan_global_invalid_set(self):
        materialization = CascadeMaterialization(build_cascade_store(512))
        maintainer = CascadeMaintainer(materialization)
        i = 41
        old = materialization.store.assertions[assertion_id(i)]

        def forbidden_global_scan():
            raise AssertionError("whole-graph invalid_nodes scan used in maintenance path")

        materialization.graph.invalid_nodes = forbidden_global_scan  # type: ignore[method-assign]
        result = maintainer.upsert_assertion(Assertion(
            old.id,
            old.subject_id,
            old.predicate,
            73,
            old.recorded_seq,
            valid_from=old.valid_from,
            valid_to=old.valid_to,
            evidence_ids=old.evidence_ids,
        ))
        self.assertEqual(len(result.invalidated_node_ids), 3)
        trace = materialization.rebuild(result.invalidated_node_ids)
        self.assertEqual(trace.nodes_rebuilt, 3)
        self.assertEqual(materialization.store.state[old.key].operative_values, [73])

    def test_canonical_evidence_update_returns_exact_affected_ids_without_scan(self):
        materialization = CascadeMaterialization(build_cascade_store(512))
        maintainer = CascadeMaintainer(materialization)
        i = 43

        def forbidden_global_scan():
            raise AssertionError("whole-graph invalid_nodes scan used in maintenance path")

        materialization.graph.invalid_nodes = forbidden_global_scan  # type: ignore[method-assign]
        result = maintainer.upsert_evidence(EvidenceRecord(
            evidence_id(i),
            f"{alias(i, 'Nova')} finance migration deadline is day 42.",
            "source",
            999,
            source_event_time=42,
        ))
        self.assertEqual(len(result.invalidated_node_ids), 3)
        materialization.rebuild(result.invalidated_node_ids)
        key = (subject_id(i), "deadline", "default")
        self.assertIn("Nova", materialization.read_context(key) or "")

    def test_legacy_scanfree_name_is_same_behavior(self):
        materialization = CascadeMaterialization(build_cascade_store(64))
        maintainer = ScanFreeCascadeMaintainer(materialization)
        self.assertIsInstance(maintainer, CascadeMaintainer)


if __name__ == "__main__":
    unittest.main()
