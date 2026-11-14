"""Offline embedding jobs: independent inputs, resumability, bounded retries."""
import unittest
import time
from threading import Barrier
from unittest.mock import Mock

from scripts import social_embed as embed


class Rows(list):
    def sort(self, *args):
        return self

    def limit(self, limit):
        return Rows(self[:limit] if limit else self)


class Collection:
    def __init__(self, rows):
        self.rows, self.writes = rows, []

    def find(self, query, projection):
        self.query = query
        return Rows(self.rows)

    def update_one(self, query, update):
        self.writes.append((query, update))


class EmbedTests(unittest.TestCase):
    def test_wrapped_401_aborts_corpus_without_retry(self):
        collection = Collection([{"uri": str(i), "text": str(i)} for i in range(100)])
        sleeps = []
        def one(text):
            error = RuntimeError("secret provider text must not be reported")
            error.status_code = 401
            try:
                raise error
            except RuntimeError as cause:
                raise RuntimeError("sanitized wrapper") from cause
        with self.assertRaises(embed.EmbeddingJobStopped) as captured:
            embed.embed_posts(collection, one, limit=100, workers=1, sleep=sleeps.append)
        error = captured.exception
        self.assertEqual(error.status_code, 401)
        self.assertEqual(error.report["attempted"], 1)
        self.assertEqual(error.report["model_calls"], 1)
        self.assertEqual(error.report["failed"], 1)
        self.assertEqual(sleeps, [])
        self.assertNotIn("secret", str(error))
        self.assertEqual(collection.writes, [])

    def test_inflight_success_is_saved_when_other_worker_gets_403(self):
        collection = Collection([{"uri": str(i), "text": str(i)} for i in range(20)])
        barrier = Barrier(2)
        def one(text):
            barrier.wait(timeout=2)
            if text == "0":
                error = RuntimeError("forbidden")
                error.code = 403
                raise error
            time.sleep(.01)
            return [1.0] * 768
        with self.assertRaises(embed.EmbeddingJobStopped) as captured:
            embed.embed_posts(collection, one, limit=20, workers=2)
        self.assertEqual(captured.exception.report["embedded"], 1)
        self.assertEqual(captured.exception.report["model_calls"], 2)
        self.assertEqual(collection.writes[0][0], {"uri": "1"})

    def test_429_retries_then_succeeds(self):
        collection = Collection([{"uri": "a", "text": "cargo pants"}])
        error = RuntimeError("rate limited")
        error.code = 429
        one = Mock(side_effect=[error, [1.0] * 768])
        sleeps = []
        result = embed.embed_posts(collection, one, limit=1, sleep=sleeps.append)
        self.assertEqual(result["embedded"], 1)
        self.assertEqual(result["model_calls"], 2)
        self.assertEqual(sleeps, [2])

    def test_each_post_is_independent_and_persisted(self):
        collection = Collection([{"uri": "a", "text": "cargo pants"}, {"uri": "b", "text": "plain tee"}])
        one = Mock(return_value=[2.0] * 768)
        result = embed.embed_posts(collection, one, limit=2, workers=1)
        self.assertEqual([call.args[0] for call in one.call_args_list], ["cargo pants", "plain tee"])
        self.assertEqual(result["embedded"], 2)
        for query, update in collection.writes:
            fields = update["$set"]
            self.assertEqual(fields["embedding_model"], "gemini-embedding-2")
            self.assertAlmostEqual(sum(v * v for v in fields["embedding"]), 1)
            self.assertTrue(fields["embedded_at"].endswith("Z"))

    def test_failure_does_not_mark_embedded_and_other_posts_continue(self):
        collection = Collection([{"uri": "a", "text": "bad"}, {"uri": "b", "text": "good"}])
        sleeps = []
        def one(text):
            if text == "bad":
                error = RuntimeError("provider failure")
                error.code = 503
                raise error
            return [1.0] * 768
        result = embed.embed_posts(collection, one, limit=2, workers=1, sleep=sleeps.append)
        self.assertEqual(result["embedded"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(sleeps, [2, 4, 8])
        self.assertEqual(collection.writes[0][0], {"uri": "b"})

    def test_zero_limit_never_embeds(self):
        one = Mock()
        result = embed.embed_posts(Collection([{"uri": "a", "text": "x"}]), one, limit=0)
        one.assert_not_called()
        self.assertEqual(result["attempted"], 0)

    def test_invalid_vectors_are_rejected(self):
        for vector in ([1.0] * 767, [0.0] * 768, [float("nan")] * 768):
            self.assertFalse(embed.valid_embedding(vector))

    def test_existing_ready_index_is_reused(self):
        collection = Mock()
        collection.list_search_indexes.return_value = [{"name": embed.INDEX_NAME, "status": "READY",
                                                       "queryable": True, "latestDefinition": embed.INDEX_DEFINITION}]
        self.assertTrue(embed.ensure_vector_index(collection))
        collection.create_search_index.assert_not_called()
        collection.update_search_index.assert_not_called()

    def test_missing_index_created_with_filters(self):
        collection = Mock()
        collection.list_search_indexes.side_effect = [[], [{"status": "READY", "queryable": True,
                                                           "latestDefinition": embed.INDEX_DEFINITION}]]
        self.assertTrue(embed.ensure_vector_index(collection))
        model = collection.create_search_index.call_args.args[0]
        self.assertEqual(model.document["type"], "vectorSearch")
        self.assertEqual({field["path"] for field in embed.INDEX_DEFINITION["fields"]},
                         {"embedding", "term", "created_at"})


if __name__ == "__main__":
    unittest.main()
