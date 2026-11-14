"""Offline ingestion tests: all Bluesky requests use an httpx mock transport."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

from scripts.social_ingest import (ApiFailure, BlueskyClient, BudgetExceeded, author_hash,
                                   enrich_threads, initial_state, ingest, normalize_post, persist_post,
                                   audit_first_pages, extend_terms, insertion_budget, iso, normalize_times, persist_page, prepare_resume, probe, quality_report, requeue_verification, thread_posts, weekly_windows)


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
    def test_thread_addition_cannot_close_empty_search_week(self):
        thread = normalize_post(raw_post("thread"), "cargo pants", "salt")
        thread["source"] = "thread"
        search = normalize_post(raw_post("search"), "cargo pants", "salt")
        search["created_at"] = "2026-01-13T12:00:00.000000Z"
        collection = Mock()
        collection.find.return_value = [thread, search]
        state = initial_state(["cargo pants"], datetime(2026, 2, 1, tzinfo=timezone.utc))
        with tempfile.TemporaryDirectory() as directory, patch("scripts.social_ingest.save_json"):
            report = quality_report(collection, state, {}, Path(directory) / "quality.md")
        self.assertEqual(report["posts_per_term"]["cargo pants"], 2)
        self.assertEqual(report["search_posts_per_term"]["cargo pants"], 1)
        self.assertEqual(report["thread_posts_per_term"]["cargo pants"], 1)
        self.assertIn("2026-01-05", report["coverage_gaps"]["cargo pants"])
        self.assertNotIn("2026-01-12", report["coverage_gaps"]["cargo pants"])

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
        self.assertEqual(calls[1]["until"], "2026-01-06T12:00:00.000001Z")
        self.assertFalse(state["tasks"][0]["equal_timestamp_boundary_risk"])
        self.assertEqual(state["tasks"][0]["stop_reason"], "confirmed_empty")

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
        self.assertEqual(state["tasks"][0]["stop_reason"], "confirmed_empty")
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

    def test_two_hundred_threads_five_new_each_global_and_request_caps(self):
        class Cursor(list):
            def sort(self, *args):
                return self
            def limit(self, count):
                return self[:count]
        class Collection(FakeCollection):
            def find(self, query, projection):
                if "uri" in query:
                    return Cursor([self.posts[uri] for uri in query["uri"]["$in"] if uri in self.posts])
                return Cursor([{k: p[k] for k in ("uri", "term")} for p in self.posts.values()
                               if p.get("reply_count", 0) > 0 and p.get("source") != "thread"])
        collection = Collection()
        for number in range(200):
            post = normalize_post(raw_post(f"at://did:plc:one/app.bsky.feed.post/root{number}"), "cargo pants", "salt")
            post["terms"] = ["cargo pants"]
            collection.posts[post["uri"]] = post
        def handler(request):
            uri = request.url.params["uri"]
            tree = {"post": raw_post(uri), "replies": [{"post": raw_post(uri + f"-reply{i}")} for i in range(10)]}
            return httpx.Response(200, json={"thread": tree})
        state = initial_state(["cargo pants"], datetime(2026, 1, 7, tzinfo=timezone.utc))
        passed = {"endpoints": {"app.bsky.feed.getPostThread": {"passed": True}}}
        api = self.make_client(handler)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            enrich_threads(api, collection, state, passed, "salt", True, 1200, path)
            self.assertEqual(len(collection.posts), 1200)
            self.assertEqual(api.requests, 200)
            self.assertEqual(len(state["thread"]["fetched"]), 200)
            self.assertEqual(state["thread"]["new_posts"], 1000)
            self.assertEqual(state["thread"]["skipped_new_posts"], 1000)
            self.assertEqual(state["thread"]["truncated_threads"], 200)
            # Resuming never fetches the same successful candidate again.
            enrich_threads(api, collection, state, passed, "salt", True, 1200, path)
            self.assertEqual(api.requests, 200)
            state["thread"]["fetched"] = []
            state["thread"]["requests"] = 399
            api = self.make_client(handler)
            enrich_threads(api, collection, state, passed, "salt", True, 1200, path)
            self.assertEqual(api.requests, 1)
            self.assertEqual(state["thread"]["requests"], 400)


if __name__ == "__main__":
    unittest.main()

class WindowAuditTests(unittest.TestCase):
    make_client = RequestTests.make_client

    def state(self):
        state = initial_state(['cargo pants'], datetime(2026, 1, 7, tzinfo=timezone.utc))
        state['tasks'] = state['tasks'][:1]
        state['pagination'] = 'keyset'
        return state

    def run_ingest(self, state, collection, responses, **kwargs):
        pages = iter(responses)
        api = self.make_client(lambda request: httpx.Response(200, json={'posts': next(pages)}))
        with tempfile.TemporaryDirectory() as directory:
            ingest(api, collection, state, 'salt', True, kwargs.pop('cap', 35000),
                   Path(directory) / 'state.json', **kwargs)
        return api

    def test_confirmation_request_at_full_global_cap(self):
        state, collection = self.state(), FakeCollection()
        persist_post(collection, normalize_post(raw_post(), 'cargo pants', 'salt'), 1)
        api = self.run_ingest(state, collection, [[]], cap=1, confirm_only=True)
        self.assertEqual(api.requests, 1)
        self.assertTrue(state['tasks'][0]['done'])
        self.assertEqual(len(collection.posts), 1)
        self.assertEqual(state['tasks'][0]['stop_reason'], 'confirmed_empty')

    def test_zero_new_duplicate_confirmation(self):
        state, collection = self.state(), FakeCollection()
        post = raw_post()
        persist_post(collection, normalize_post(post, 'cargo pants', 'salt'), 1)
        self.run_ingest(state, collection, [[post]], cap=1, confirm_only=True)
        self.assertEqual(state['tasks'][0]['stop_reason'], 'confirmed_no_new')
        self.assertFalse(state['tasks'][0]['partial'])

    def test_short_nonempty_page_requires_another_request(self):
        state, collection = self.state(), FakeCollection()
        post = raw_post()
        api = self.run_ingest(state, collection, [[post], [post]], max_new_posts=1)
        self.assertEqual(api.requests, 2)
        self.assertEqual(state['tasks'][0]['first_page_count'], 1)
        self.assertFalse(state['tasks'][0]['saturated'])
        self.assertEqual(state['tasks'][0]['stop_reason'], 'confirmed_no_new')

    def test_full_identical_tie_page_remains_unresolved(self):
        state, collection = self.state(), FakeCollection()
        posts = [raw_post(f'at://did:plc:one/app.bsky.feed.post/{n}') for n in range(100)]
        for post in posts:
            persist_post(collection, normalize_post(post, 'cargo pants', 'salt'), 100)
        api = self.run_ingest(state, collection, [posts], cap=100, confirm_only=True)
        task = state['tasks'][0]
        self.assertEqual(api.requests, 1)
        self.assertFalse(task['done'])
        self.assertTrue(task['unresolved'])
        self.assertEqual(task['stop_reason'], 'unresolved_full_page')
        self.assertIsNone(task['keyset_until'])

    def test_quota_guard_preserves_unseen_position_and_continues_windows(self):
        state, collection = self.state(), FakeCollection()
        other = dict(state['tasks'][0], term='other', seen_cursors=[])
        state['tasks'].append(other)
        posts = [raw_post('one'), raw_post('two')]
        api = self.run_ingest(state, collection, [posts, []], cap=1, max_new_posts=1)
        self.assertEqual(api.requests, 2)
        self.assertEqual(len(collection.posts), 1)
        task = state['tasks'][0]
        self.assertIsNone(task['keyset_until'])
        self.assertEqual(task['pending_unseen_count'], 1)
        self.assertEqual(task['stop_reason'], 'new_posts_require_capacity')
        self.assertTrue(other['done'])

    def test_first_page_twenty_records_saturation(self):
        state, collection = self.state(), FakeCollection()
        posts = [raw_post(str(n)) for n in range(20)]
        self.run_ingest(state, collection, [posts, []])
        self.assertTrue(state['tasks'][0]['saturated'])
        self.assertEqual(state['tasks'][0]['first_page_count'], 20)

    def test_legacy_first_page_audit_does_not_invent_multipage_count(self):
        state = self.state()
        one = state['tasks'][0]
        one.update(pages=1, returned=7, cursor='keep')
        two = dict(one, term='other', pages=2, returned=120, equal_timestamp_boundary_risk=True)
        state['tasks'].append(two)
        api = self.make_client(lambda request: httpx.Response(200, json={'posts': [raw_post(str(i)) for i in range(5)]}))
        with tempfile.TemporaryDirectory() as directory:
            audit_first_pages(api, state, state_path=Path(directory) / 'state.json')
        self.assertEqual(api.requests, 1)
        self.assertEqual(one['first_page_count'], 7)
        self.assertEqual(two['first_page_count'], 5)
        self.assertFalse(two['saturated'])
        requeue_verification(state)
        self.assertEqual(two['keyset_until'], two['until'])
        self.assertTrue(two['equal_timestamp_boundary_risk'])
        self.assertEqual(one['cursor'], 'keep')

    def test_press_seed_extension_preserves_exact_saved_window_and_cursors(self):
        state = initial_state(['cargo pants'], datetime(2026, 1, 7, tzinfo=timezone.utc))
        old_windows = {(t['since'], t['until']) for t in state['tasks']}
        state['tasks'][0]['cursor'] = 'preserve'
        added = extend_terms(state, [{'term': 'user seed', 'press_status': 'emerged', 'label': 'unknown'}])
        new_windows = {(t['since'], t['until']) for t in state['tasks'] if t['term'] == 'user seed'}
        self.assertEqual(added, len(old_windows))
        self.assertEqual(new_windows, old_windows)
        self.assertEqual(state['tasks'][0]['cursor'], 'preserve')

    def test_globally_existing_other_term_is_new_term_progress(self):
        state, collection = self.state(), FakeCollection()
        post = raw_post()
        persist_post(collection, normalize_post(post, 'other', 'salt'), 1)
        api = self.run_ingest(state, collection, [[post], [post]], cap=1, confirm_only=True)
        self.assertEqual(api.requests, 2)
        self.assertIn('cargo pants', collection.posts[post['uri']]['terms'])
        self.assertEqual(state['tasks'][0]['stop_reason'], 'confirmed_no_new')

class FirstPagePhaseTests(unittest.TestCase):
    make_client = RequestTests.make_client
    state = WindowAuditTests.state
    run_ingest = WindowAuditTests.run_ingest

    def test_fresh_search_and_threads_preserve_entire_press_reserve(self):
        budget = insertion_budget(35000, 0, 10000, pending_threads=200)
        self.assertEqual(budget, 24000)
        self.assertEqual(35000 - budget - 200 * 5, 10000)
        self.assertEqual(insertion_budget(35000, 24081, 10000), 919)
        self.assertEqual(insertion_budget(35000, 24081, 10000, press_only=True), 10919)
        self.assertEqual(insertion_budget(35000, 35000, 10000), 0)
    def test_two_terms_two_windows_exactly_one_page_each_fair_order(self):
        state = initial_state(['beta', 'alpha'], datetime(2026, 1, 7, tzinfo=timezone.utc))
        state['tasks'] = state['tasks'][:4]
        state['tasks'] = sorted(state['tasks'], key=lambda task: task['term'])
        state['pagination'] = 'keyset'
        calls = []
        def handler(request):
            params = dict(request.url.params)
            calls.append(params)
            post = raw_post(params['q'] + params['since'])
            post['record']['createdAt'] = params['since']
            return httpx.Response(200, json={'posts': [post]})
        collection = FakeCollection()
        api = self.make_client(handler)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            ingest(api, collection, state, 'salt', True, 35000, path, first_pages_only=True)
            self.assertEqual(len(calls), 4)
            self.assertTrue(all(call['limit'] == '20' for call in calls))
            self.assertEqual([call['q'] for call in calls], ['"alpha"', '"beta"', '"alpha"', '"beta"'])
            self.assertTrue(all(task['pages'] == 1 and task['partial'] and not task['done'] for task in state['tasks']))
            ingest(api, collection, state, 'salt', True, 35000, path, first_pages_only=True)
            self.assertEqual(len(calls), 4)
            self.assertEqual(len(collection.posts), 4)

    def test_captured_overlap_is_skipped_without_changing_its_cursor(self):
        state = self.state()
        overlap = dict(state['tasks'][0], term='overlap', pages=2, first_page_count=20,
                       saturated=True, cursor='saved-cursor', keyset_until='saved-keyset', returned=120)
        state['tasks'].append(overlap)
        before = dict(overlap)
        api = self.run_ingest(state, FakeCollection(), [[]], first_pages_only=True)
        self.assertEqual(api.requests, 1)
        self.assertEqual(overlap, before)

    def test_first_page_quota_interruption_resumes_same_twenty_result_page(self):
        state, collection = self.state(), FakeCollection()
        posts = [raw_post('one'), raw_post('two')]
        self.run_ingest(state, collection, [posts], cap=2, max_new_posts=1, first_pages_only=True)
        task = state['tasks'][0]
        self.assertTrue(task['first_page_insertion_pending'])
        self.assertIsNone(task['keyset_until'])
        self.run_ingest(state, collection, [posts], cap=2, max_new_posts=1, first_pages_only=True)
        self.assertFalse(task['first_page_insertion_pending'])
        self.assertTrue(task['first_page_ingested'])
        self.assertEqual(task['first_page_count'], 2)
        self.assertEqual(len(collection.posts), 2)
