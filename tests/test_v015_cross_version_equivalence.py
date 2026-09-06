from __future__ import annotations

import unittest

from simulator.compositional_profile import (
    run_v014_monolithic_control,
    run_v015_compositional_case,
)


class V015CrossVersionEquivalenceTests(unittest.TestCase):
    maxDiff = None

    def test_full_logical_profile_exactly_matches_v014_monolith(self) -> None:
        control = run_v014_monolithic_control(
            entity_count=128,
            predicate_count=32,
            history_depth=8,
            changed_count=1,
        )
        fixed = run_v015_compositional_case(
            entity_count=128,
            predicate_count=32,
            history_depth=8,
            changed_count=1,
        )
        self.assertEqual(
            fixed["full_profile"],
            control["persisted_profile"],
            "v0.15 assembled full logical profile must exactly preserve v0.14 semantics",
        )


if __name__ == "__main__":
    unittest.main()
