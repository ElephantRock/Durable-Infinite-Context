import unittest

from benchmark.evaluator import build_store
from rag.scalable_planner import ScalableQueryPlanner, SubjectProfileIndex
from simulator.scalable_planner import build_ambiguous_cardinality_suite


class QueryBoundednessTests(unittest.TestCase):
    def test_broad_postings_are_sized_before_materialization(self):
        suite = build_ambiguous_cardinality_suite(1000, queries=1)
        index = SubjectProfileIndex(build_store([suite.scenario]))
        limit = 128

        original_token = index.token_posting
        original_fragment = index.fragment_posting
        original_ngram = index.ngram_posting

        def guarded_token(token, predicate):
            self.assertLessEqual(index.token_posting_size(token, predicate), limit)
            return original_token(token, predicate)

        def guarded_fragment(fragment, predicate):
            self.assertLessEqual(index.fragment_posting_size(fragment, predicate), limit)
            return original_fragment(fragment, predicate)

        def guarded_ngram(gram, predicate):
            self.assertLessEqual(index.ngram_posting_size(gram, predicate), limit)
            return original_ngram(gram, predicate)

        # These guards make the regression observable: if candidate generation ever
        # materializes a posting before checking its O(1) cardinality, this test fails.
        index.token_posting = guarded_token  # type: ignore[method-assign]
        index.fragment_posting = guarded_fragment  # type: ignore[method-assign]
        index.ngram_posting = guarded_ngram  # type: ignore[method-assign]

        q = suite.cases[0].query
        plan, trace = ScalableQueryPlanner(index, broad_posting_limit=limit).plan_with_trace(
            q.question_text or ""
        )
        self.assertIsNone(plan.subject_id)
        self.assertGreater(trace.broad_postings_skipped, 0)
        self.assertLess(trace.logical_work, trace.total_subjects)


if __name__ == "__main__":
    unittest.main()
