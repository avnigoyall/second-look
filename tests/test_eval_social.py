"""Deterministic evaluation arithmetic tests, with no live data or model calls."""
import unittest
from unittest import mock

from scripts import eval_social as evaluation


class EvalTests(unittest.TestCase):
    def test_recall_mrr_errors_and_latency_scope(self):
        queries = [{"uri": "a", "query": "one"}, {"uri": "b", "query": "two"}, {"uri": "c", "query": "three"}]
        retrieve = mock.Mock(side_effect=[[{"uri": "a"}], [{"uri": "x"}] * 5 + [{"uri": "b"}], RuntimeError("down")])
        result = evaluation.retrieval_metrics(queries, lambda _: [1.0] * 768, retrieve)
        self.assertEqual(result["recall_at_5"], 1 / 3)
        self.assertEqual(result["recall_at_10"], 2 / 3)
        self.assertAlmostEqual(result["mrr_at_30"], (1 + 1 / 6) / 3)
        self.assertEqual(result["lookup_errors"], 1)
        self.assertEqual(result["successful_lookups"], 2)
        self.assertIsNotNone(result["lookup_p95_ms"])
        self.assertEqual(retrieve.call_args.kwargs["limit"], 30)

    def test_cluster_purity_excludes_unassigned_and_reports_coverage(self):
        posts = [{"term": "a", "author_hash": "x"}, {"term": "a", "author_hash": "x"},
                 {"term": "b", "author_hash": "y"}, {"term": "c", "author_hash": "z"}]
        result = evaluation.cluster_purity(posts, [{"author_hash": "x", "community_id": "1"},
                                                   {"author_hash": "y", "community_id": "1"}])
        self.assertAlmostEqual(result["weighted_purity_by_term"], 2 / 3)
        self.assertEqual(result["assigned_post_share"], 0.75)

    def test_sampling_is_seeded_balanced_and_unique(self):
        posts = [{"uri": str(i), "term": str(i % 3), "text": "some distinctive clothing detail " * 2}
                 for i in range(45)]
        sampled = evaluation.select_posts(posts)
        self.assertEqual(len(sampled), 30)
        self.assertEqual(sampled, evaluation.select_posts(list(reversed(posts))))
        self.assertEqual(len({p["uri"] for p in sampled}), 30)
        self.assertEqual({t: sum(p["term"] == t for p in sampled) for t in ("0", "1", "2")},
                         {"0": 10, "1": 10, "2": 10})

    def test_unmeasured_report_does_not_invent_metrics(self):
        report = evaluation.report_markdown({"status": "unmeasured"})
        self.assertIn("Recall@5 | unmeasured", report)
        self.assertIn("not the trend origin", report)
        self.assertIn("never platform totals", report)
        self.assertNotIn(chr(0x2014), report)

    def test_percentiles(self):
        self.assertEqual(evaluation.percentile([10, 20, 30], 0.5), 20)
        self.assertEqual(evaluation.percentile([10, 20, 30], 0.95), 29)
        self.assertIsNone(evaluation.percentile([], 0.95))


if __name__ == "__main__":
    unittest.main()
