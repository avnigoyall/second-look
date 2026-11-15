"""Offline embedding jobs and the Social provider: batching, resumability, bounded retries."""
import json
import os
import tempfile
import time
from pathlib import Path
import unittest
from threading import Barrier, Lock
from unittest import mock
from unittest.mock import Mock

from backend.services import social_embeddings as provider
from scripts import social_embed as embed

VULTR = provider.spec("vultr")
GEMINI = provider.spec("gemini")


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


class BulkCollection(Collection):
    def __init__(self, rows):
        super().__init__(rows)
        self.bulk_requests = []

    def bulk_write(self, operations, *, ordered):
        self.bulk_requests.append((operations, ordered))


def error(code, text="secret provider text"):
    exc = RuntimeError(text)
    exc.code = code
    return exc


def vectors(texts, dims=VULTR.dims, value=1.0):
    return [[value] * dims for _ in texts]


class EmbedJobTests(unittest.TestCase):
    def test_requests_carry_lists_and_writes_tag_provider_model_and_dims(self):
        rows = [{"uri": str(i), "text": "post " + str(i)} for i in range(64)]
        collection, progress, seen = BulkCollection(rows), [], []
        def batch(texts):
            seen.append(list(texts))
            return vectors(texts, value=2.0)
        result = embed.embed_posts(collection, batch, limit=64, batch_size=32, request_size=8, workers=4,
                                   on_progress=progress.append, spec=VULTR)
        self.assertEqual(result["model_calls"], 8)
        self.assertTrue(all(len(texts) == 8 for texts in seen))
        self.assertEqual(sorted(t for texts in seen for t in texts), sorted(r["text"] for r in rows))
        self.assertEqual(len(collection.bulk_requests), 2)
        self.assertEqual([row["embedded"] for row in progress], [32, 64])
        for operations, ordered in collection.bulk_requests:
            self.assertFalse(ordered)
            for operation in operations:
                fields = operation._doc["$set"]
                self.assertEqual(fields["embedding_model"], VULTR.model)
                self.assertEqual(fields["dims"], 1024)
                # Packed float32 binary: about 4 KB instead of a 13 KB double array.
                self.assertEqual(len(bytes(fields["embedding_vultr"])), 1024 * 4 + 2)
                stored = provider.unpack(fields["embedding_vultr"])
                self.assertEqual(len(stored), 1024)
                self.assertNotIn("embedding", fields)
                self.assertAlmostEqual(sum(v * v for v in stored), 1, places=5)
                self.assertTrue(fields["embedded_at"].endswith("Z"))
        self.assertNotIn("post ", json.dumps(progress))

    def test_vectors_follow_input_order_within_a_request(self):
        rows = [{"uri": u, "text": u} for u in "abc"]
        collection = Collection(rows)
        def batch(texts):
            return [[float(ord(t))] + [0.0] * (VULTR.dims - 1) for t in texts]
        embed.embed_posts(collection, batch, limit=3, request_size=3, workers=1, spec=VULTR)
        self.assertEqual([q["uri"] for q, _ in collection.writes], ["a", "b", "c"])
        self.assertTrue(all(provider.unpack(u["$set"]["embedding_vultr"])[0] == 1.0 for _, u in collection.writes))

    def test_gemini_writes_only_its_own_field(self):
        collection = Collection([{"uri": "a", "text": "cargo pants"}])
        embed.embed_posts(collection, lambda texts: vectors(texts, GEMINI.dims), limit=1, workers=1,
                          request_size=1, spec=GEMINI)
        fields = collection.writes[0][1]["$set"]
        self.assertEqual(set(fields), {"embedding", "embedded_at"})
        self.assertEqual(len(fields["embedding"]), 768)

    def test_wrong_count_or_dimensions_fail_without_writes(self):
        for result in ([[1.0] * 1024], [[1.0] * 2560, [1.0] * 2560]):
            collection = Collection([{"uri": "a", "text": "x"}, {"uri": "b", "text": "y"}])
            report = embed.embed_posts(collection, Mock(return_value=result), limit=2, request_size=2,
                                       workers=1, attempts=1, spec=VULTR)
            self.assertEqual(report["failed"], 2)
            self.assertEqual(collection.writes, [])

    def test_fatal_response_drains_and_bulk_flushes_inflight_success(self):
        collection = BulkCollection([{"uri": str(i), "text": str(i)} for i in range(32)])
        barrier, progress = Barrier(2), []
        def batch(texts):
            barrier.wait(timeout=2)
            if texts == ["0"]:
                raise error(401)
            time.sleep(.01)
            return vectors(texts)
        with self.assertRaises(embed.EmbeddingJobStopped) as captured:
            embed.embed_posts(collection, batch, limit=32, request_size=1, workers=2,
                              on_progress=progress.append, spec=VULTR)
        self.assertEqual(len(collection.bulk_requests), 1)
        self.assertEqual(collection.bulk_requests[0][0][0]._filter, {"uri": "1"})
        self.assertEqual(captured.exception.report["embedded"], 1)
        self.assertEqual(captured.exception.report["model_calls"], 2)
        self.assertNotIn("secret", str(captured.exception))

    def test_bad_request_splits_chunk_and_isolates_rejected_post(self):
        collection = Collection([{"uri": u, "text": u} for u in ("ok1", "bad", "ok2")])
        def batch(texts):
            if "bad" in texts:
                raise error(400)
            return vectors(texts)
        result = embed.embed_posts(collection, batch, limit=3, request_size=3, workers=1, spec=VULTR)
        self.assertEqual(result["embedded"], 2)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["model_calls"], 4)
        self.assertEqual({q["uri"] for q, _ in collection.writes}, {"ok1", "ok2"})

    def test_retry_after_hint_is_honored_and_capped(self):
        collection = Collection([{"uri": "a", "text": "x"}])
        limited = error(429)
        limited.retry_after = 7
        huge = error(503)
        huge.retry_after = 500
        sleeps = []
        result = embed.embed_posts(collection, Mock(side_effect=[limited, huge, vectors(["x"])]), limit=1,
                                   workers=1, sleep=sleeps.append, spec=VULTR)
        self.assertEqual(result["embedded"], 1)
        self.assertEqual(sleeps, [7, 30.0])

    def test_opt_in_transient_403_retries_then_succeeds(self):
        collection = Collection([{"uri": "a", "text": "cargo pants"}])
        batch = Mock(side_effect=[error(403), error(403), vectors(["cargo pants"])])
        sleeps = []
        result = embed.embed_posts(collection, batch, limit=1, workers=1, transient_403_retries=2,
                                   sleep=sleeps.append, spec=VULTR)
        self.assertEqual((result["model_calls"], result["embedded"]), (3, 1))
        self.assertEqual(sleeps, [2, 4])

    def test_persistent_403_stops_after_explicit_budget(self):
        collection = Collection([{"uri": str(i), "text": "cargo"} for i in range(20)])
        batch, sleeps = Mock(side_effect=error(403)), []
        with self.assertRaises(embed.EmbeddingJobStopped):
            embed.embed_posts(collection, batch, workers=1, request_size=1, transient_403_retries=2,
                              sleep=sleeps.append, spec=VULTR)
        self.assertEqual(batch.call_count, 3)
        self.assertEqual(sleeps, [2, 4])

    def test_default_403_and_opt_in_401_are_immediate(self):
        for code, retries in [(403, 0), (401, 2)]:
            collection = Collection([{"uri": "a", "text": "cargo"}])
            batch, sleeps = Mock(side_effect=error(code)), []
            with self.assertRaises(embed.EmbeddingJobStopped):
                embed.embed_posts(collection, batch, workers=1, transient_403_retries=retries,
                                  sleep=sleeps.append, spec=VULTR)
            self.assertEqual(batch.call_count, 1)
            self.assertEqual(sleeps, [])

    def test_transient_failure_does_not_mark_embedded_and_other_posts_continue(self):
        collection = Collection([{"uri": "a", "text": "bad"}, {"uri": "b", "text": "good"}])
        sleeps = []
        def batch(texts):
            if texts == ["bad"]:
                raise error(503)
            return vectors(texts)
        result = embed.embed_posts(collection, batch, limit=2, request_size=1, workers=1,
                                   sleep=sleeps.append, spec=VULTR)
        self.assertEqual((result["embedded"], result["failed"]), (1, 1))
        self.assertEqual(sleeps, [2, 4, 8])
        self.assertEqual(collection.writes[0][0], {"uri": "b"})

    def test_parallel_requests_are_bounded(self):
        collection = Collection([{"uri": str(i), "text": str(i)} for i in range(40)])
        lock, active, peak = Lock(), [0], [0]
        def batch(texts):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(.01)
            with lock:
                active[0] -= 1
            return vectors(texts)
        result = embed.embed_posts(collection, batch, limit=40, request_size=4, workers=3, spec=VULTR)
        self.assertEqual(result["embedded"], 40)
        self.assertLessEqual(peak[0], 3)

    def test_zero_limit_never_embeds(self):
        batch = Mock()
        result = embed.embed_posts(Collection([{"uri": "a", "text": "x"}]), batch, limit=0, spec=VULTR)
        batch.assert_not_called()
        self.assertEqual(result["attempted"], 0)

    def test_safe_diagnostics_allowlist_excludes_secret_payloads(self):
        inner = error(403, "secret-key-secret-input-secret-url")
        inner.details = {"error": {"message": "secret-input", "details": [
            {"reason": "API_KEY_INVALID", "metadata": {"api_key": "secret-key"}}]}}
        wrapper = RuntimeError("secret wrapper")
        wrapper.__cause__ = inner
        report = embed.safe_error_report(wrapper)
        self.assertEqual(report["diagnostics"], {"category": "credential", "reason": "API_KEY_INVALID"})
        self.assertNotIn("secret", json.dumps(report))

    def test_invalid_vectors_are_rejected(self):
        for vector in ([1.0] * 1023, [0.0] * 1024, [float("nan")] * 1024):
            self.assertFalse(embed.valid_embedding(vector, 1024))

    def test_index_definition_follows_provider(self):
        definition = embed.index_definition(VULTR)
        self.assertEqual(definition["fields"][0], {"type": "vector", "path": "embedding_vultr",
                                                   "numDimensions": 1024, "similarity": "cosine"})
        self.assertEqual({f["path"] for f in definition["fields"][1:]}, {"term", "created_at"})
        self.assertEqual(embed.index_definition(GEMINI)["fields"][0]["path"], "embedding")

    def test_missing_index_created_and_other_indexes_untouched(self):
        collection = Mock()
        ready = {"status": "READY", "queryable": True, "latestDefinition": embed.index_definition(VULTR)}
        collection.list_search_indexes.side_effect = [[], [ready]]
        self.assertTrue(embed.ensure_vector_index(collection, spec=VULTR))
        model = collection.create_search_index.call_args.args[0]
        self.assertEqual(model.document["name"], "social_posts_vultr_vector")
        self.assertEqual(model.document["type"], "vectorSearch")
        self.assertEqual(collection.list_search_indexes.call_args.args, ("social_posts_vultr_vector",))
        collection.drop_search_index.assert_not_called()


class Response:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        return self._body


def ok(n, dims=1024, shuffle=False):
    rows = [{"index": i, "embedding": [float(i + 1)] * dims} for i in range(n)]
    return Response(200, {"data": list(reversed(rows)) if shuffle else rows})


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"SOCIAL_EMBED_PROVIDER": "vultr"})
        self.env.start()
        self.client = Mock()
        self.patch = mock.patch.object(provider, "_client", return_value=self.client)
        self.patch.start()
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = mock.patch.object(provider, "LEDGER_PATH", Path(self.tmp.name) / "usage.json")
        self.ledger.start()

    def tearDown(self):
        self.ledger.stop()
        self.tmp.cleanup()
        self.patch.stop()
        self.env.stop()

    def ledger_tokens(self):
        return json.loads(provider.LEDGER_PATH.read_text())["tokens"]

    def test_budget_blocks_before_sending_when_cap_would_be_exceeded(self):
        with mock.patch.dict(os.environ, {"VULTR_BUDGET_USD": "0.00001", "VULTR_PRICE_PER_MTOK": "1"}):
            with self.assertRaises(provider.SocialEmbeddingError) as captured:
                provider.embed_texts(["x" * 100])
        self.assertEqual(captured.exception.category, "budget")
        self.client.post.assert_not_called()

    def test_reported_usage_replaces_estimate_and_failed_attempts_stay_charged(self):
        response = ok(1)
        response._body["usage"] = {"total_tokens": 5}
        self.client.post.side_effect = [Response(503), response]
        provider.embed_texts(["hello world"], attempts=2, sleep=lambda _: None)
        estimate = provider.estimate_tokens(["hello world"])
        self.assertEqual(self.ledger_tokens(), estimate + 5)
        self.assertEqual(json.loads(provider.LEDGER_PATH.read_text())["requests"], 2)

    def test_budget_stop_is_fatal_for_the_embed_job(self):
        collection = Collection([{"uri": str(i), "text": "x"} for i in range(10)])
        stop = provider.SocialEmbeddingError("budget")
        batch, sleeps = Mock(side_effect=stop), []
        with self.assertRaises(embed.EmbeddingJobStopped) as captured:
            embed.embed_posts(collection, batch, limit=10, request_size=1, workers=1,
                              sleep=sleeps.append, spec=VULTR)
        self.assertEqual(batch.call_count, 1)
        self.assertEqual(sleeps, [])
        self.assertEqual(captured.exception.provider_diagnostics, {"category": "budget"})

    def test_corrupt_ledger_blocks_instead_of_resetting_spend(self):
        provider.LEDGER_PATH.write_text("{not json")
        with self.assertRaises(provider.SocialEmbeddingError) as captured:
            provider.embed_texts(["x"])
        self.assertEqual(captured.exception.category, "budget_ledger_unreadable")
        self.client.post.assert_not_called()

    def test_default_spec_is_vultr_1024_with_separate_field_and_index(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SOCIAL_EMBED_PROVIDER", None)
            spec = provider.spec()
        self.assertEqual((spec.provider, spec.dims, spec.field, spec.index),
                         ("vultr", 1024, "embedding_vultr", "social_posts_vultr_vector"))

    def test_batch_request_sends_list_and_dimensions_and_reorders_by_index(self):
        self.client.post.return_value = ok(3, shuffle=True)
        result = provider.embed_texts([" a ", "b", "c"], timeout=5)
        body = self.client.post.call_args.kwargs["json"]
        self.assertEqual(body["input"], ["a", "b", "c"])
        self.assertEqual(body["dimensions"], 1024)
        self.assertTrue(self.client.post.call_args.args[0].endswith("/embeddings"))
        self.assertEqual(self.client.post.call_args.kwargs["timeout"], 5)
        self.assertEqual([round(v[0] * 32, 6) for v in result], [1.0, 1.0, 1.0])
        self.assertAlmostEqual(sum(v * v for v in result[0]), 1)

    def test_auth_failure_stops_immediately_without_provider_text(self):
        self.client.post.return_value = Response(401, {"error": "secret detail"})
        sleeps = []
        with self.assertRaises(provider.SocialEmbeddingError) as captured:
            provider.embed_texts(["x"], attempts=5, sleep=sleeps.append)
        self.assertEqual(self.client.post.call_count, 1)
        self.assertEqual((captured.exception.status_code, captured.exception.category), (401, "credential"))
        self.assertEqual(sleeps, [])
        self.assertNotIn("secret", str(captured.exception))

    def test_429_honors_retry_after_then_succeeds(self):
        self.client.post.side_effect = [Response(429, headers={"retry-after": "3"}), ok(1)]
        sleeps = []
        self.assertEqual(len(provider.embed_texts(["x"], attempts=3, sleep=sleeps.append)), 1)
        self.assertEqual(sleeps, [3.0])

    def test_server_errors_back_off_boundedly_then_fail(self):
        self.client.post.return_value = Response(503)
        sleeps = []
        with self.assertRaises(provider.SocialEmbeddingError) as captured:
            provider.embed_texts(["x"], attempts=3, sleep=sleeps.append)
        self.assertEqual(self.client.post.call_count, 3)
        self.assertEqual(len(sleeps), 2)
        self.assertTrue(all(0 < s <= 30 for s in sleeps))
        self.assertEqual(captured.exception.status_code, 503)

    def test_timeout_maps_to_safe_category(self):
        import httpx
        self.client.post.side_effect = httpx.ReadTimeout("secret url")
        with self.assertRaises(provider.SocialEmbeddingError) as captured:
            provider.embed_query("x", timeout=.1)
        self.assertEqual(captured.exception.category, "timeout")
        self.assertIsNone(captured.exception.status_code)
        self.assertNotIn("secret", str(captured.exception))

    def test_wrong_dimensions_or_missing_rows_are_rejected(self):
        cases = [(ok(1, dims=2560), ["x"]),           # native size, dimensions ignored
                 (ok(1), ["x", "y"]),                 # one row for two inputs
                 (Response(200, {"data": []}), ["x"])]
        for response, texts in cases:
            self.client.post.return_value = response
            with self.assertRaises(provider.SocialEmbeddingError):
                provider.embed_texts(texts, attempts=1)

    def test_empty_text_never_calls_provider(self):
        with self.assertRaises(provider.SocialEmbeddingError):
            provider.embed_texts(["ok", "  "])
        self.client.post.assert_not_called()

    def test_packed_vectors_round_trip_and_filters_avoid_array_paths(self):
        vector = [0.5] * 1024
        packed = provider.pack(vector)
        post = {"embedding_vultr": packed, "embedding_model": VULTR.model, "dims": 1024}
        self.assertEqual(provider.stored_vector(post, VULTR), vector)
        self.assertNotIn("embedding_vultr.1023", json.dumps(provider.pending_filter(VULTR)))
        self.assertEqual(provider.embedded_filter(VULTR)["embedding_vultr"], {"$exists": True})
        self.assertIsNone(provider.unpack("not a vector"))

    def test_pack_existing_converts_only_valid_arrays(self):
        class Packing:
            def __init__(self):
                self.ops = []
            def find(self, query, projection):
                return [{"_id": 1, "embedding_vultr": [1.0] * 1024}, {"_id": 2, "embedding_vultr": [0.0] * 1024}]
            def bulk_write(self, ops, ordered):
                self.ops.extend(ops)
                return Mock(modified_count=len(ops))
        collection = Packing()
        report = embed.pack_existing(collection, spec=VULTR)
        self.assertEqual(report, {"packed": 1, "skipped_invalid": 1})
        self.assertEqual(len(provider.unpack(collection.ops[0]._doc["$set"]["embedding_vultr"])), 1024)

    def test_stored_vector_checks_field_model_and_dims(self):
        good = {"embedding_vultr": [0.1] * 1024, "embedding_model": VULTR.model, "dims": 1024}
        self.assertIsNotNone(provider.stored_vector(good, VULTR))
        self.assertIsNone(provider.stored_vector({**good, "embedding_model": "x"}, VULTR))
        self.assertIsNone(provider.stored_vector({**good, "dims": 2560}, VULTR))
        legacy = {"embedding": [0.1] * 768, **good}
        self.assertIsNotNone(provider.stored_vector(legacy, GEMINI))
        self.assertIsNone(provider.stored_vector({"embedding": [0.1] * 1024}, GEMINI))


if __name__ == "__main__":
    unittest.main()
