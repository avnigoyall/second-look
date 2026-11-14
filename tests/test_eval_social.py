"""Deterministic evaluation arithmetic tests, with no live data or model calls."""
import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
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

    def test_paraphrase_retries_only_one_unchanged_row_and_retains_twenty_nine(self):
        posts = [{"uri": str(i), "text": "Original clothing description " + str(i)} for i in range(30)]
        first = [{"id": i, "query": "New clothing description " + str(i)} for i in range(30)]
        first[9]["query"] = posts[9]["text"]
        with mock.patch.dict("os.environ", {"CLAUDE_MODEL": "test-model"}), \
                mock.patch.object(evaluation, "_paraphrase_round", side_effect=[first, [{"id": 9, "query": "I like chinos"}]]) as provider:
            result = evaluation.paraphrase_posts(posts)
        self.assertEqual(len(result), 30)
        self.assertEqual(result[9]["query"], "I like chinos")
        self.assertEqual(result[8]["query"], first[8]["query"])
        self.assertEqual(provider.call_count, 2)
        self.assertEqual([p["id"] for p in provider.call_args_list[1].args[0]], [9])
        self.assertTrue(provider.call_args_list[1].kwargs["retry"])

    def test_paraphrase_retry_budget_is_three_calls_and_fails_honestly(self):
        posts = [{"uri": "x", "text": "The same original phrase"}]
        with mock.patch.dict("os.environ", {"CLAUDE_MODEL": "test-model"}), \
                mock.patch.object(evaluation, "_paraphrase_round", return_value=[{"id": 0, "query": posts[0]["text"]}]) as provider:
            with self.assertRaisesRegex(ValueError, "after three bounded calls: 1"):
                evaluation.paraphrase_posts(posts)
        self.assertEqual(provider.call_count, 3)

    def test_strict_provider_schema_uses_supported_array_constraints(self):
        response = SimpleNamespace(content=[SimpleNamespace(type="tool_use", name="save_queries",
                              input={"queries": [{"id": 0, "query": "A different phrase"}]})])
        create = mock.Mock(return_value=response)
        client = SimpleNamespace(messages=SimpleNamespace(create=create))
        with mock.patch("anthropic.Anthropic", return_value=client):
            evaluation._paraphrase_round([{"id": 0, "text": "Clothing detail"},
                                         {"id": 1, "text": "Another detail"}], "test-model")
        tool = create.call_args.kwargs["tools"][0]
        constraints = tool["input_schema"]["properties"]["queries"]
        self.assertTrue(tool["strict"])
        self.assertNotIn("maxItems", constraints)
        self.assertLessEqual(constraints.get("minItems", 0), 1)
        self.assertEqual(constraints["items"]["properties"]["id"]["enum"], [0, 1])

    def test_missing_press_seeds_remain_pending_without_invented_rows(self):
        comparison = evaluation.press_graph_comparison([{"term": "chinos", "label": "staple"}], [], [])
        self.assertEqual(comparison["provided_seed_rows"], 0)
        self.assertEqual(comparison["rows"], [])
        self.assertEqual(comparison["status"], "awaiting_seed_rows")
        report = evaluation.report_markdown({"press_graph": comparison})
        self.assertIn("Awaiting the requested 10", report)
        self.assertNotIn("| Term | press_status", report)

    def test_press_comparison_preserves_status_and_source_date_metadata(self):
        seed = {"term": "chinos", "label": "staple", "press_status": "Still discussed | uncertain",
                "press_sources": [{"url": "https://publication.example/style", "date": "2026-10-01"}],
                "press_date": "2026-10-01"}
        metric = {"term": "chinos", "post_count": 123, "breadth": 4, "concentration": 0.3,
                  "momentum": "steady", "confidence": "low", "coverage_note": "Incomplete sampling"}
        result = evaluation.press_graph_comparison([seed], [metric], [])
        row = result["rows"][0]
        self.assertEqual(row["press_status"], seed["press_status"])
        self.assertEqual(row["press_metadata"]["press_sources"], seed["press_sources"])
        self.assertEqual(row["sample_posts"], 123)
        report = evaluation.report_markdown({"press_graph": result})
        self.assertIn("Still discussed \\| uncertain", report)
        self.assertIn("2026-10-01", report)
        self.assertIn("Incomplete sampling", report)

    def test_prepare_only_thirty_without_gemini_reuses_cached_claude_queries(self):
        posts = [{"uri": str(i), "term": str(i % 3), "text": "Some detailed clothing observation " + str(i)}
                 for i in range(45)]
        collections = {name: mock.Mock() for name in ("social_posts", "social_communities", "social_trends")}
        collections["social_posts"].find.return_value = posts
        collections["social_communities"].find.return_value = []
        collections["social_trends"].find.return_value = []
        def paraphrases(selected):
            return [{"uri": p["uri"], "query": "A different clothing detail " + p["uri"], "term": p["term"],
                     "source_text_sha256": hashlib.sha256(p["text"].encode()).hexdigest()} for p in selected]
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.dict("sys.modules", {"backend.db": SimpleNamespace(db=collections)}), \
                mock.patch.object(evaluation, "paraphrase_posts", side_effect=paraphrases) as claude, \
                mock.patch.object(evaluation.social, "embed_query", side_effect=AssertionError("Gemini must not run")) as embed, \
                mock.patch.object(evaluation.social, "vector_search_posts", side_effect=AssertionError("Retrieval must not run")) as retrieve:
            root = Path(directory)
            args = ["--prepare-only", "--queries", str(root / "queries.json"),
                    "--report", str(root / "report.md"), "--output", str(root / "results.json")]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(evaluation.main(args), 0)
                self.assertEqual(evaluation.main(args), 0)
            result = json.loads((root / "results.json").read_text())
            self.assertEqual(result["status"], "prepared")
            self.assertEqual(result["prepared_queries"], 30)
            self.assertEqual(result["evaluated_queries"], 0)
            self.assertNotIn("retrieval", result)
            self.assertIn("Recall@5 | unmeasured", (root / "report.md").read_text())
            self.assertEqual(json.loads((root / "queries.json").read_text())["generator"], "Claude")
            claude.assert_called_once()
            embed.assert_not_called()
            retrieve.assert_not_called()

    def test_prepared_cache_rejects_changed_source_without_regeneration(self):
        post = {"uri": "a", "text": "An original clothing observation"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "queries.json"
            path.write_text(json.dumps({"generator": "Claude", "queries": [
                {"uri": "a", "query": "Paraphrased clothing", "source_text_sha256": "outdated"}]}))
            generate = mock.Mock()
            with self.assertRaises(ValueError):
                evaluation.prepared_queries([post], path, generate=generate)
            generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
