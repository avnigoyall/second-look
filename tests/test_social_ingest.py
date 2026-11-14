"""Offline ingestion tests: all Bluesky requests use an httpx mock transport."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

import httpx

from scripts.social_ingest import (ApiFailure, BlueskyClient, BudgetExceeded, author_hash,
                                   initial_state, ingest, normalize_post, persist_post,
                                   iso, normalize_times, persist_page, prepare_resume, probe, thread_posts, weekly_windows)


def raw_post(uri="at://did:plc:one/app.bsky.feed.post/a"):
    return {"uri": uri, "author": {"did": "did:plc:one", "handle": "never-store.example"},
            "record": {"text": "cargo pants", "createdAt": "2026-01-06T12:00:00+00:00",
                       "reply": {"parent": {"uri": "at://did:plc:two/app.bsky.feed.post/b"}},
                       "facets": [{"features": [{"$type": "app.bsky.richtext.facet#mention", "did": "did:plc:three"}]}],
                       "embed": {"$type": "app.bsky.embed.recordWithMedia", "record": {"record": {"uri": "at://did:plc:four/app.bsky.feed.post/c"}}}},
            "likeCount": 2, "repostCount": 3, "replyCount": 4}


class FakeCollection:
    def __init__(self):
        self.posts = {}

    def count_documents(self, query):
        return len(self.posts)

    def find_one(self, query, projection=None):
        return self.posts.get(query["uri"])

    def update_one(self, query, update, upsert=False):
        uri = query["uri"]
        if "$setOnInsert" in update and uri not in self.posts:
            self.posts[uri] = dict(update["$setOnInsert"])
        if "$addToSet" in update:
            term = update["$addToSet"]["terms"]
            if term not in self.posts[uri].setdefault("terms", []):
                self.posts[uri]["terms"].append(term)
        if "$set" in update:
            self.posts[uri].update(update["$set"])


class NormalizationTests(unittest.TestCase):
    def test_hash_stable_and_salt_sensitive(self):
        self.assertEqual(author_hash("did:plc:one", "secret"), author_hash("did:plc:one", "secret"))
        self.assertNotEqual(author_hash("did:plc:one", "secret"), author_hash("did:plc:one", "different"))
        self.assertEqual(len(author_hash("did:plc:one", "secret")), 64)

    def test_real_relationships_normalized_without_handles(self):
        post = normalize_post(raw_post(), "cargo pants", "salt")
        self.assertEqual(post["created_at"], "2026-01-06T12:00:00.000000Z")
        self.assertEqual(post["reply_parent_author_hash"], author_hash("did:plc:two", "salt"))
        self.assertEqual(post["quote_author_hash"], author_hash("did:plc:four", "salt"))
        self.assertEqual(post["mention_author_hashes"], [author_hash("did:plc:three", "salt")])
        self.assertNotIn("never-store.example", str(post))
        self.assertNotIn("did", post)
        self.assertEqual(post["repost_count"], 3)

    def test_text_opt_out(self):
        self.assertEqual(normalize_post(raw_post(), "cargo pants", "salt", False)["text"], "")

    def test_malformed_dates_skipped(self):
        post = raw_post()
        post["record"]["createdAt"] = "bad"
        self.assertIsNone(normalize_post(post, "cargo pants", "salt"))

    def test_unique_uri_keeps_all_terms_and_cap(self):
        collection = FakeCollection()
        post = normalize_post(raw_post(), "cargo pants", "salt")
        self.assertTrue(persist_post(collection, post, 1))
        self.assertFalse(persist_post(collection, {**post, "term": "baggy jeans"}, 1))
        self.assertEqual(collection.posts[post["uri"]]["terms"], ["cargo pants", "baggy jeans"])
        with self.assertRaises(BudgetExceeded):
            persist_post(collection, {**post, "uri": "at://other"}, 1)

    def test_thread_visible_post_walk(self):
        tree = {"post": raw_post(), "parent": {"$type": "blockedPost"},
                "replies": [{"post": raw_post("reply")}, {"$type": "notFoundPost"}]}
        self.assertEqual([p["uri"] for p in thread_posts(tree)], [raw_post()["uri"], "reply"])

    def test_search_promotes_previously_thread_only_post(self):
        collection = FakeCollection()
        post = normalize_post(raw_post(), "cargo pants", "salt")
        persist_post(collection, {**post, "source": "thread"}, 10)
        persist_post(collection, {**post, "term": "baggy jeans"}, 10)
        self.assertEqual(collection.posts[post["uri"]]["source"], "search")
        self.assertEqual(collection.posts[post["uri"]]["term"], "baggy jeans")

    def test_bulk_page_preserves_terms_promotion_and_global_cap(self):
        class BulkCollection(FakeCollection):
            def find(self, query, projection):
                return [self.posts[uri] for uri in query["uri"]["$in"] if uri in self.posts]

            def bulk_write(self, operations, ordered):
                for operation in operations:
                    self.update_one(operation._filter, operation._doc, upsert=True)
        collection = BulkCollection()
        post = normalize_post(raw_post(), "cargo pants", "salt")
        persist_page(collection, [{**post, "source": "thread"}], 2)
        self.assertEqual(collection.posts[post["uri"]]["terms"], ["cargo pants"])
        persist_page(collection, [{**post, "term": "baggy jeans"}], 2)
        self.assertEqual(collection.posts[post["uri"]]["source"], "search")
        self.assertEqual(collection.posts[post["uri"]]["terms"], ["cargo pants", "baggy jeans"])
        with self.assertRaises(BudgetExceeded):
            persist_page(collection, [{**post, "uri": "second"}, {**post, "uri": "third"}], 2)
        self.assertEqual(len(collection.posts), 2)


class WindowTests(unittest.TestCase):
    def test_year_windows_are_contiguous_and_monday_aligned(self):
        end = datetime(2026, 11, 14, 12, 34, tzinfo=timezone.utc)
        windows = weekly_windows(end)
        self.assertEqual(windows[0][0], "2025-11-14T12:34:00.000000Z")
        self.assertEqual(windows[-1][1], "2026-11-14T12:34:00.000000Z")
        for left, right in zip(windows, windows[1:]):
            self.assertEqual(left[1], right[0])
            self.assertEqual(datetime.fromisoformat(right[0].replace("Z", "+00:00")).weekday(), 0)

    def test_leap_day_window(self):
        self.assertEqual(weekly_windows(datetime(2024, 2, 29, tzinfo=timezone.utc))[0][0], "2023-02-28T00:00:00.000000Z")

    def test_fixed_microsecond_timestamps_sort_chronologically(self):
        exact = iso(datetime(2026, 1, 6, 12, tzinfo=timezone.utc))
        fraction = iso(datetime(2026, 1, 6, 12, microsecond=123000, tzinfo=timezone.utc))
        self.assertEqual(exact, "2026-01-06T12:00:00.000000Z")
        self.assertEqual(fraction, "2026-01-06T12:00:00.123000Z")
        self.assertLess(exact, fraction)

    def test_time_migration_changes_only_timestamp_fields(self):
        class Cursor(list):
            def limit(self, count):
                return self[:count]
        collection = Mock()
        collection.find.return_value = Cursor([{"uri": "post", "created_at": "2026-01-06T12:00:00Z",
                                                "ingested_at": "2026-01-06T12:00:00.123Z"}])
        collection.bulk_write.return_value.modified_count = 1
        result = normalize_times(collection)
        self.assertEqual(result["modified"], 1)
        operation = collection.bulk_write.call_args.args[0][0]
        self.assertEqual(operation._doc, {"$set": {"created_at": "2026-01-06T12:00:00.000000Z",
                                                   "ingested_at": "2026-01-06T12:00:00.123000Z"}})


class RequestTests(unittest.TestCase):
    def make_client(self, handler, **kwargs):
        return BlueskyClient(httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _: None,
                             monotonic=lambda: 0, **kwargs)

    def test_rate_limit_backoff_then_success(self):
        responses = iter([httpx.Response(429), httpx.Response(429), httpx.Response(200, json={"posts": []})])
        sleeps = []
        api = self.make_client(lambda request: next(responses))
        api.sleep = sleeps.append
        self.assertEqual(api.get("search", {}), {"posts": []})
        self.assertEqual(api.requests, 3)
        self.assertEqual(api.rate_limits, 2)
        self.assertIn(2, sleeps)

    def test_exact_second_and_fraction_both_inside_legacy_precision_window(self):
        exact, fraction = raw_post("exact"), raw_post("fraction")
        fraction["record"]["createdAt"] = "2026-01-06T12:00:00.123Z"
        state = initial_state(["cargo pants"], datetime(2026, 1, 7, tzinfo=timezone.utc))
        state["tasks"] = [{"term": "cargo pants", "since": "2026-01-06T12:00:00Z", "until": "2026-01-06T12:00:01Z",
                            "cursor": None, "done": False, "pages": 0, "seen_cursors": [], "returned": 0}]
        collection = FakeCollection()
        with tempfile.TemporaryDirectory() as directory:
            ingest(self.make_client(lambda request: httpx.Response(200, json={"posts": [exact, fraction]})),
                   collection, state, "salt", True, 25000, Path(directory) / "state.json")
        self.assertEqual(set(collection.posts), {"exact", "fraction"})

    def test_probe_capped_at_twenty_physical_attempts(self):
        api = self.make_client(lambda request: httpx.Response(429), max_requests=20)
        result = probe(api)
        self.assertLessEqual(result["requests"], 20)
        self.assertFalse(result["cursor_pagination"]["passed"])

    def test_probe_checks_endpoint_shapes_and_pagination(self):
        calls = []
        def handler(request):
            calls.append(str(request.url))
            name = request.url.path.split("/")[-1]
            if name.endswith("searchPosts"):
                page2 = "cursor" in request.url.params
                return httpx.Response(200, json={"posts": [raw_post("second" if page2 else "first")], "cursor": "b" if page2 else "a"})
            shapes = {"getPostThread": {"thread": {}}, "getRepostedBy": {"repostedBy": []},
                      "getQuotes": {"posts": []}, "getFollows": {"follows": []}}
            return httpx.Response(200, json=shapes[name.split(".")[-1]])
        result = probe(self.make_client(handler))
        self.assertEqual(result["requests"], 6)
        self.assertTrue(result["cursor_pagination"]["passed"])
        self.assertTrue(all(e["passed"] for e in result["endpoints"].values()))

    def test_error_does_not_include_response_or_url(self):
        api = self.make_client(lambda request: httpx.Response(403, text="private-secret"))
        with self.assertRaisesRegex(ApiFailure, "^http_403$"):
            api.get("search", {"sensitive": "secret"})

    def test_failed_cursor_probe_can_use_verified_keyset_without_sending_cursor(self):
        calls = []
        post = raw_post()
        def handler(request):
            calls.append(dict(request.url.params))
            self.assertNotIn("cursor", request.url.params)
            if request.url.params["until"] == "2026-01-12T00:00:00Z":
                return httpx.Response(200, json={"posts": [post], "cursor": "blocked-cursor"})
            return httpx.Response(200, json={"posts": []})
        state = initial_state(["cargo pants"], datetime(2026, 1, 7, tzinfo=timezone.utc))
        state["pagination"] = "keyset"
        state["tasks"] = [{"term": "cargo pants", "since": "2026-01-05T00:00:00Z", "until": "2026-01-12T00:00:00Z",
                            "cursor": None, "done": False, "pages": 0, "seen_cursors": [], "returned": 0}]
        with tempfile.TemporaryDirectory() as directory:
            ingest(self.make_client(handler), FakeCollection(), state, "salt", True, 25000, Path(directory) / "state.json")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]["until"], "2026-01-06T12:00:00.000000Z")
        self.assertTrue(state["tasks"][0]["equal_timestamp_boundary_risk"])
        self.assertEqual(state["tasks"][0]["stop_reason"], "empty_keyset_page")

    def test_first_page_failure_does_not_skip_other_windows(self):
        calls = []
        def handler(request):
            calls.append(dict(request.url.params))
            return httpx.Response(403) if len(calls) == 1 else httpx.Response(200, json={"posts": []})
        state = initial_state(["cargo pants"], datetime(2026, 1, 7, tzinfo=timezone.utc))
        state["tasks"] = state["tasks"][:2]
        with tempfile.TemporaryDirectory() as directory:
            ingest(self.make_client(handler), FakeCollection(), state, "salt", True, 25000, Path(directory) / "state.json")
        self.assertEqual(len(calls), 2)
        self.assertTrue(state["tasks"][0]["partial"])
        self.assertEqual(state["tasks"][0]["last_error"], "http_403")
        self.assertTrue(state["tasks"][1]["done"])

    def test_restart_retries_failed_window_and_preserves_cursor(self):
        state = initial_state(["cargo pants"], datetime(2026, 1, 7, tzinfo=timezone.utc))
        state["tasks"] = state["tasks"][:1]
        state["tasks"][0].update(cursor="saved-cursor", keyset_until="2026-01-06T00:00:00Z")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            ingest(self.make_client(lambda request: httpx.Response(403)), FakeCollection(), state, "salt", True, 25000, path)
            self.assertTrue(state["tasks"][0]["done"])
            self.assertEqual(prepare_resume(state), 1)
            self.assertFalse(state["tasks"][0]["done"])
            self.assertEqual(state["tasks"][0]["cursor"], "saved-cursor")
            self.assertEqual(state["tasks"][0]["keyset_until"], "2026-01-06T00:00:00Z")
            calls = []
            def success(request):
                calls.append(dict(request.url.params))
                return httpx.Response(200, json={"posts": []})
            ingest(self.make_client(success), FakeCollection(), state, "salt", True, 25000, path)
        self.assertEqual(calls[0]["cursor"], "saved-cursor")
        self.assertEqual(state["tasks"][0]["stop_reason"], "no_cursor")
        self.assertFalse(state["tasks"][0]["partial"])

    def test_short_page_does_not_stop_cursor_then_resume(self):
        calls = []
        post = raw_post()
        def handler(request):
            calls.append(dict(request.url.params))
            if "cursor" not in request.url.params:
                return httpx.Response(200, json={"posts": [post], "cursor": "page2"})
            return httpx.Response(200, json={"posts": [], "cursor": "page2"})
        state = initial_state(["cargo pants"], datetime(2026, 1, 7, tzinfo=timezone.utc))
        state["tasks"] = [{"term": "cargo pants", "since": "2026-01-05T00:00:00Z", "until": "2026-01-12T00:00:00Z",
                            "cursor": None, "done": False, "pages": 0, "seen_cursors": [], "returned": 0}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            with self.assertRaises(BudgetExceeded):
                ingest(self.make_client(handler, max_requests=1), FakeCollection(), state, "salt", True, 25000, path)
            self.assertFalse(state["tasks"][0]["done"])
            self.assertEqual(state["tasks"][0]["cursor"], "page2")
            ingest(self.make_client(handler), FakeCollection(), state, "salt", True, 25000, path)
        self.assertEqual(calls[1]["cursor"], "page2")
        self.assertTrue(state["tasks"][0]["done"])


if __name__ == "__main__":
    unittest.main()
