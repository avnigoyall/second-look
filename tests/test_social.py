"""Offline numeric metrics and bounded runtime retrieval tests. No Bluesky requests."""
import os
import io
import json
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from backend.services import social as s
from contextlib import redirect_stdout
from scripts.social_metrics import calibrate, separation_report, main as metrics_main

START = datetime(2026, 1, 5, tzinfo=timezone.utc)


def post(n, term="cargo pants", week=0, author=None, terms=None):
    return {"uri": f"at://did:plc:abc/app.bsky.feed.post/{n}", "term": term,
            "terms": terms or [term], "author_hash": author or f"author-{n}",
            "created_at": s.iso(START + timedelta(weeks=week, hours=1)), "text": "A clothing post"}


def hit(term="cargo pants", similarity=.8, n=1):
    return {**post(n, term=term), "similarity": similarity}


def trend():
    return {"term": "cargo pants", "first_seen_in_window": s.iso(START), "peak_week": "2026-01-05",
            "weekly_counts": [{"week": "2026-01-05", "count": 1, "corpus_count": 2,
                               "normalized": .5}] * 52,
            "decay_ratio": .3, "breadth": 3, "concentration": .5,
            "momentum": "fading", "confidence": "medium"}


class MetricsTests(unittest.TestCase):
    def metrics(self, rows, weeks=12, communities=(), **kwargs):
        return s.compute_trends(rows, communities, start=START, end=START + timedelta(weeks=weeks), **kwargs)

    def test_dedupe_preserves_multiple_terms_and_unique_corpus(self):
        first = post(1, term="cargo pants")
        second = post(1, term="chinos")
        rows = self.metrics([first, second, post(2, "chinos")])
        by_term = {r["term"]: r for r in rows}
        self.assertEqual(by_term["cargo pants"]["post_count"], 1)
        self.assertEqual(by_term["chinos"]["post_count"], 2)
        self.assertEqual(by_term["cargo pants"]["weekly_counts"][0]["corpus_count"], 2)
        self.assertEqual(by_term["cargo pants"]["weekly_counts"][0]["normalized"], .5)
        self.assertEqual(by_term["chinos"]["weekly_counts"][0]["normalized"], 1)

    def test_platform_volume_growth_normalizes_out(self):
        rows = []
        for w in range(12):
            volume = 10 if w < 6 else 20
            for i in range(volume):
                rows.append(post(f"{w}-{i}", term="cargo pants" if i < volume // 2 else "chinos", week=w))
        result = self.metrics(rows)[0]
        self.assertTrue(all(r["normalized"] == .5 for r in result["weekly_counts"]))
        self.assertEqual(result["decay_ratio"], 1)
        self.assertEqual(result["momentum"], "steady")

    def test_missing_corpus_week_is_not_evidence_of_fading(self):
        rows = [post(w, week=w) for w in range(11)]
        result = self.metrics(rows)[0]
        self.assertEqual(result["weekly_counts"][-1]["count"], 0)
        self.assertEqual(result["weekly_counts"][-1]["corpus_count"], 0)
        self.assertIsNone(result["decay_ratio"])
        self.assertEqual(result["momentum"], "unknown")

    def test_partial_current_week_does_not_create_fading(self):
        rows = [post(f"{w}-{i}", week=w) for w in range(12) for i in range(10)]
        result = s.compute_trends(rows, start=START, end=START + timedelta(weeks=12, days=2))[0]
        self.assertFalse(result["weekly_counts"][-1]["complete"])
        self.assertEqual(result["decay_ratio"], 1)
        self.assertEqual(result["confidence"], "medium")

    def test_confidence_under_100_is_always_low(self):
        rows = [post(w, week=w) for w in range(12)]
        self.assertEqual(self.metrics(rows)[0]["confidence"], "low")

    def test_real_decay_is_detected_against_the_whole_corpus(self):
        rows = []
        for w in range(16):
            term_volume = 80 if w < 4 else 10
            for i in range(100):
                rows.append(post(f"{w}-{i}", term="cargo pants" if i < term_volume else "chinos", week=w))
        result = self.metrics(rows, weeks=16)[0]
        self.assertAlmostEqual(result["decay_ratio"], .125)
        self.assertEqual(result["momentum"], "fading")
        self.assertEqual(result["confidence"], "medium")  # 440 posts, below the high-confidence threshold.

    def test_breadth_threshold_and_time_to_third_community(self):
        rows = []
        communities = [{"author_hash": f"a{i}", "community_id": str(i)} for i in range(3)]
        for community in range(3):
            for i in range(3):
                rows.append(post(f"{community}-{i}", week=community, author=f"a{community}"))
        rows.append(post("unknown", week=3, author="unassigned"))
        result = self.metrics(rows, communities=communities)[0]
        self.assertEqual(result["breadth"], 3)
        self.assertEqual(result["weeks_to_breadth_3"], 2)
        self.assertEqual(result["concentration"], .3)
        self.assertEqual(result["authors"], 4)
        self.assertEqual(result["first_seen_in_window"], s.iso(START + timedelta(hours=1)))

    def test_empty_terms_are_retained_with_unknown_momentum(self):
        result = self.metrics([], terms=["white t-shirt"])[0]
        self.assertEqual(len(result["weekly_counts"]), 12)
        self.assertIsNone(result["first_seen_in_window"])
        self.assertIsNone(result["peak_week"])
        self.assertEqual(result["momentum"], "unknown")
        self.assertIsNone(result["concentration"])

    def test_thread_enrichment_does_not_bias_search_counts(self):
        result = self.metrics([post(1), {**post(2), "source": "thread"}])[0]
        self.assertEqual(result["post_count"], 1)
        self.assertEqual(result["weekly_counts"][0]["corpus_count"], 1)

    def complete_coverage(self, terms=("cargo pants",), weeks=12):
        return {"tasks": [{"term": term, "since": s.iso(START + timedelta(weeks=w)),
                           "until": s.iso(START + timedelta(weeks=w + 1)), "pages": 1,
                           "done": True, "stop_reason": "no_cursor", "first_page_count": 10, "saturated": False}
                          for term in terms for w in range(weeks)]}

    def test_unvisited_term_window_is_not_zero_activity(self):
        rows = [post(f"{w}-{i}", week=w) for w in range(12) for i in range(10)]
        coverage = self.complete_coverage()
        coverage["tasks"][-1].update(pages=0, done=False, stop_reason=None)
        result = self.metrics(rows, coverage=coverage)[0]
        self.assertFalse(result["query_coverage_complete"])
        self.assertEqual(result["weekly_counts"][-1]["query_coverage"], "unvisited")
        self.assertEqual(result["confidence"], "low")
        self.assertEqual(result["momentum"], "unknown")
        self.assertIsNone(result["decay_ratio"])

    def test_truncated_historical_peak_cannot_create_confident_decay(self):
        rows = [post(f"{w}-{i}", week=w) for w in range(12) for i in range(10)]
        coverage = self.complete_coverage()
        coverage["tasks"][0].update(stop_reason="repeating_cursor")
        result = self.metrics(rows, coverage=coverage)[0]
        self.assertEqual(result["weekly_counts"][0]["query_coverage"], "partial")
        self.assertEqual(result["confidence"], "low")
        self.assertEqual(result["momentum"], "unknown")
        self.assertIsNone(result["decay_ratio"])

    def test_incomplete_other_term_makes_normalization_uncertain(self):
        rows = [post(f"{w}-{i}", week=w) for w in range(12) for i in range(10)]
        coverage = self.complete_coverage(terms=("cargo pants", "chinos"))
        coverage["tasks"][-1].update(done=False, stop_reason=None)
        result = {m["term"]: m for m in self.metrics(rows, terms=("cargo pants", "chinos"), coverage=coverage)}
        cargo = result["cargo pants"]
        self.assertEqual(cargo["weekly_counts"][-1]["query_coverage"], "complete")
        self.assertEqual(cargo["weekly_counts"][-1]["corpus_query_coverage"], "incomplete")
        self.assertEqual(cargo["confidence"], "low")
        self.assertEqual(cargo["momentum"], "unknown")

    def test_exhausted_queries_allow_confidence_but_missing_state_does_not(self):
        rows = [post(f"{w}-{i}", week=w) for w in range(12) for i in range(10)]
        complete = self.metrics(rows, coverage=self.complete_coverage())[0]
        missing = self.metrics(rows, coverage={})[0]
        self.assertTrue(complete["query_coverage_complete"])
        self.assertEqual(complete["confidence"], "medium")
        self.assertEqual(missing["confidence"], "low")
        self.assertEqual(missing["weekly_counts"][0]["query_coverage"], "unknown")
        report = separation_report([missing], {"cargo pants": "micro"})
        self.assertEqual(report["excluded_for_query_coverage"], 1)
        self.assertEqual(report["eligible_terms"], 0)

    def test_empty_first_keyset_page_is_observed_but_boundary_risk_is_partial(self):
        since, until = START, START + timedelta(days=7)
        task = {"since": since.isoformat(), "until": until.isoformat(), "pages": 1,
                "done": True, "stop_reason": "empty_keyset_page"}
        self.assertEqual(s.query_window_coverage([task], since, until), "complete")
        task["equal_timestamp_boundary_risk"] = True
        self.assertEqual(s.query_window_coverage([task], since, until), "partial")

    def test_classification_never_reads_labels_or_terms(self):
        numeric = {"post_count": 200, "observed_weeks": 12, "decay_ratio": .2,
                   "recent_growth": .2, "peak_weeks_ago": 8}
        self.assertEqual(s.classify_momentum({**numeric, "term": "white t-shirt", "label": "staple"}), "fading")
        self.assertEqual(s.classify_momentum({**numeric, "term": "barrel jeans", "label": "micro"}), "fading")

    def test_calibration_does_not_claim_success_with_small_sample(self):
        metrics = [{"term": "white t-shirt", "post_count": 200, "observed_weeks": 12,
                    "decay_ratio": .9, "breadth": 1, "momentum": "steady"}]
        cfg = s.momentum_config()
        updated, report = calibrate(metrics, {"white t-shirt": "staple"}, cfg)
        self.assertEqual(updated, cfg)
        self.assertFalse(report["calibration_applied"])
        self.assertFalse(report["separates"])
        self.assertIn("do not separate", report["interpretation"])

    def test_saturation_above_quarter_caps_high_confidence_at_medium(self):
        rows = [post(f"{w}-{i}", week=w) for w in range(16) for i in range(40)]
        coverage = self.complete_coverage(weeks=16)
        for task in coverage["tasks"][:5]:
            task.update(first_page_count=20, saturated=True)
        result = self.metrics(rows, weeks=16, coverage=coverage)[0]
        self.assertEqual(result["confidence"], "medium")
        self.assertEqual(result["saturated_week_share"], 5 / 16)
        self.assertEqual(result["saturation_confidence_cap"], "medium")
        self.assertEqual(result["saturated_windows"], 5)
        self.assertEqual(result["completed_windows"], 16)
        self.assertTrue(result["weekly_counts"][0]["counts_are_lower_bound"])
        self.assertIn("capped at medium", result["coverage_note"])

    def test_exactly_quarter_saturated_does_not_cap_high_confidence(self):
        rows = [post(f"{w}-{i}", week=w) for w in range(16) for i in range(40)]
        coverage = self.complete_coverage(weeks=16)
        for task in coverage["tasks"][:4]:
            task.update(first_page_count=20, saturated=True)
        result = self.metrics(rows, weeks=16, coverage=coverage)[0]
        self.assertEqual(result["confidence"], "high")
        self.assertEqual(result["saturated_week_share"], .25)
        self.assertIsNone(result["saturation_confidence_cap"])
        self.assertTrue(result["weekly_counts"][0]["counts_are_lower_bound"])

    def test_unknown_saturation_cannot_silently_allow_high_confidence(self):
        rows = [post(f"{w}-{i}", week=w) for w in range(16) for i in range(40)]
        coverage = self.complete_coverage(weeks=16)
        coverage["tasks"][0].pop("first_page_count")
        coverage["tasks"][0].pop("saturated")
        result = self.metrics(rows, weeks=16, coverage=coverage)[0]
        self.assertTrue(result["query_coverage_complete"])
        self.assertEqual(result["confidence"], "medium")
        self.assertEqual(result["saturation_unknown_windows"], 1)
        self.assertEqual(result["saturation_confidence_cap"], "medium")
        self.assertIn("until saturation metadata is verified", result["coverage_note"])

    def test_saturation_denominator_includes_both_partial_boundary_weeks(self):
        start = START + timedelta(days=2)
        end = start + timedelta(days=365)
        windows = []
        week = s.monday(start)
        while week < end:
            windows.append((max(start, week), min(end, week + timedelta(days=7))))
            week += timedelta(days=7)
        self.assertEqual(len(windows), 53)
        tasks = [{"term": "cargo pants", "since": s.iso(left), "until": s.iso(right), "pages": 1,
                  "done": True, "stop_reason": "confirmed_empty", "first_page_count": 10, "saturated": False}
                 for left, right in windows]
        for task in tasks[1:14]:
            task.update(first_page_count=20, saturated=True)
        rows = [post(f"{w}-{i}", week=w) for w in range(1, 52) for i in range(40)]
        result = s.compute_trends(rows, start=start, end=end, coverage={"tasks": tasks})[0]
        self.assertEqual(result["calendar_complete_weeks"], 51)
        self.assertEqual(result["saturation_week_denominator"], 53)
        self.assertEqual(result["saturated_week_share"], 13 / 53)
        self.assertEqual(result["confidence"], "high")
        self.assertIsNone(result["saturation_confidence_cap"])
        for task in (tasks[0], tasks[-1]):
            task.update(first_page_count=20, saturated=True)
        result = s.compute_trends(rows, start=start, end=end, coverage={"tasks": tasks})[0]
        self.assertEqual(result["saturated_week_share"], 15 / 53)
        self.assertEqual(result["confidence"], "medium")
        self.assertEqual(result["saturation_confidence_cap"], "medium")
        self.assertIn("including partial boundary weeks", result["coverage_note"])

    def test_confirmed_exhaustion_preserves_saturation_lower_bound(self):
        coverage = self.complete_coverage(weeks=12)
        for task in coverage["tasks"]:
            task.update(stop_reason="confirmed_empty", first_page_count=20, saturated=True)
        rows = [post(f"{w}-{i}", week=w) for w in range(12) for i in range(10)]
        result = self.metrics(rows, coverage=coverage)[0]
        self.assertTrue(result["query_coverage_complete"])
        self.assertTrue(all(w["counts_are_lower_bound"] for w in result["weekly_counts"]))
        task = coverage["tasks"][0]
        task.update(stop_reason="confirmed_no_new")
        self.assertEqual(s.query_window_coverage([task], s.utc(task["since"]), s.utc(task["until"])), "complete")
        task.update(unresolved=True)
        self.assertEqual(s.query_window_coverage([task], s.utc(task["since"]), s.utc(task["until"])), "partial")

    def test_preview_can_measure_recent_sample_without_publishing_policy_change(self):
        coverage = self.complete_coverage()
        for task in coverage["tasks"]:
            task.update(done=False, stop_reason=None, first_page_count=20, saturated=True)
        rows = []
        for w in range(12):
            for i in range(100):
                rows.append(post(f"{w}-{i}", week=w, term="cargo pants" if i < (10 if w < 8 else 40) else "chinos"))
        coverage["tasks"] += [{**t, "term": "chinos"} for t in coverage["tasks"]]
        metrics = self.metrics(rows, coverage=coverage)
        preview = s.preview_recent_momentum(metrics)
        cargo = next(r for r in preview["terms"] if r["term"] == "cargo pants")
        self.assertEqual(cargo["published_momentum"], "unknown")
        self.assertEqual(cargo["proposed_recent_momentum"], "rising")
        self.assertEqual(cargo["proposed_confidence"], "low")
        self.assertIsNone(cargo["historical_decay_ratio_unchanged"])
        self.assertFalse(preview["published_policy_changed"])
        self.assertTrue(all(m["momentum"] == "unknown" for m in metrics))

    def test_preview_never_treats_unvisited_or_partial_zero_as_observed_absence(self):
        base = {"week": "2026-01-05", "complete": True, "query_visited": True,
                "corpus_query_visited": True, "count": 20, "corpus_count": 100,
                "normalized": .2, "query_coverage": "partial", "counts_are_lower_bound": True}
        for change in ({"count": 0, "normalized": 0}, {"query_visited": False}, {"corpus_query_visited": False}):
            weekly = [dict(base) for _ in range(8)]
            weekly[0].update(change)
            preview = s.preview_recent_momentum([{"term": "cargo pants", "momentum": "unknown", "weekly_counts": weekly}])
            self.assertEqual(preview["proposed_non_unknown"], 0)

    def test_preview_cli_never_writes_database_or_config(self):
        posts, communities, trends = mock.Mock(), mock.Mock(), mock.Mock()
        posts.find.return_value = [post(w, week=w) for w in range(12)]
        communities.find.return_value = []
        state = {**self.complete_coverage(), "window_start": s.iso(START),
                 "window_end": s.iso(START + timedelta(weeks=12))}
        cfg = s.momentum_config()
        output = io.StringIO()
        with mock.patch("backend.db.db", {"social_posts": posts, "social_communities": communities,
                                           "social_trends": trends}), \
                mock.patch("scripts.social_metrics.load_labels", return_value={"cargo pants": "micro"}), \
                mock.patch("scripts.social_metrics.Path.exists", return_value=True), \
                mock.patch("scripts.social_metrics.Path.read_text", return_value=json.dumps(state)), \
                mock.patch("scripts.social_metrics.momentum_config", return_value=cfg), \
                mock.patch("scripts.social_metrics.Path.write_text") as write_config, redirect_stdout(output):
            self.assertEqual(metrics_main(["--preview-momentum"]), 0)
        trends.create_index.assert_not_called()
        trends.update_one.assert_not_called()
        write_config.assert_not_called()
        self.assertTrue(json.loads(output.getvalue())["dry_run"])


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        s.clear_cache()
        provider_env = mock.patch.dict(os.environ, {"SOCIAL_EMBED_PROVIDER": "vultr"})
        provider_env.start()
        self.addCleanup(provider_env.stop)

    def test_vector_search_uses_atlas_and_converts_cosine_score(self):
        collection = mock.Mock()
        collection.aggregate.return_value = [{"uri": post(1)["uri"], "atlas_score": .85}]
        rows = s.vector_search_posts([1.0] + [0.0] * 1023, collection=collection, term="cargo pants",
                                     since=START, until=START + timedelta(weeks=1))
        self.assertAlmostEqual(rows[0]["similarity"], .7)
        pipeline = collection.aggregate.call_args.args[0]
        stage = pipeline[0]["$vectorSearch"]
        self.assertEqual(stage["limit"], 30)
        self.assertEqual(stage["filter"], {"term": "cargo pants", "created_at": {
            "$gte": s.iso(START), "$lt": s.iso(START + timedelta(weeks=1))}})
        self.assertEqual((stage["index"], stage["path"]), ("social_posts_vultr_vector", "embedding_vultr"))
        self.assertNotIn("embedding", pipeline[1]["$project"])
        self.assertNotIn("embedding_vultr", pipeline[1]["$project"])

    def test_query_vectors_are_cached_per_provider_and_failures_are_not(self):
        from backend.services import social_embeddings as provider
        calls = []
        def fake(text, timeout):
            calls.append(text)
            if text == "bad":
                raise provider.SocialEmbeddingError("timeout")
            return [0.5] * 1024
        with mock.patch.object(provider, "embed_query", side_effect=fake):
            self.assertEqual(s.embed_query("barrel jeans"), [0.5] * 1024)
            self.assertEqual(s.embed_query("barrel jeans"), [0.5] * 1024)
            for _ in range(2):
                with self.assertRaises(provider.SocialEmbeddingError):
                    s.embed_query("bad")
        self.assertEqual(calls, ["barrel jeans", "bad", "bad"])

    def test_bad_vectors_fail_without_database_call(self):
        for vector in ([0.0], [1.0] * 768, [float("nan")] * 1024):
            with self.assertRaises(ValueError):
                s.vector_search_posts(vector)

    def test_timestamp_filters_sort_exact_and_fractional_seconds_chronologically(self):
        exact = s.iso(START)
        fraction = s.iso(START + timedelta(microseconds=123000))
        following = s.iso(START + timedelta(seconds=1))
        self.assertEqual(exact, "2026-01-05T00:00:00.000000Z")
        self.assertEqual(fraction, "2026-01-05T00:00:00.123000Z")
        self.assertLess(exact, fraction)
        self.assertLess(fraction, following)
        collection = mock.Mock()
        collection.aggregate.return_value = []
        s.vector_search_posts([1.0] + [0.0] * 1023, collection=collection,
                              since="2026-01-05T00:00:00Z", until="2026-01-05T00:00:00.123Z")
        filters = collection.aggregate.call_args.args[0][0]["$vectorSearch"]["filter"]["created_at"]
        self.assertEqual(filters, {"$gte": exact, "$lt": fraction})
        self.assertEqual(s.iso(s.utc(fraction)), fraction)

    def test_vote_share_and_cosine_both_gate_matches(self):
        trend_collection = mock.Mock()
        trend_collection.find_one.return_value = trend()
        mixed = [hit(f"term{i % 3}", n=i) for i in range(30)]
        self.assertIsNone(s.context_from_hits(mixed, trends=trend_collection))
        low = [hit(similarity=.2, n=i) for i in range(30)]
        self.assertIsNone(s.context_from_hits(low, trends=trend_collection))
        trend_collection.find_one.assert_not_called()

    def test_context_limits_examples_and_weeks_and_removes_handles(self):
        trend_collection = mock.Mock()
        trend_collection.find_one.return_value = trend()
        rows = [hit(n=i) for i in range(30)]
        rows[0]["text"] = "@alice.bsky.social says https://bsky.app/profile/bob.test/post/a " + "x" * 200
        result = s.context_from_hits(rows, trends=trend_collection)
        self.assertEqual(len(result["weekly_counts"]), 26)
        self.assertEqual(len(result["examples"]), 2)
        self.assertNotIn("alice", result["examples"][0]["snippet"])
        self.assertNotIn("bob", result["examples"][0]["snippet"])
        self.assertLessEqual(len(result["examples"][0]["snippet"]), 160)
        self.assertEqual(result["term"], "cargo pants")
        self.assertTrue(result["estimate"])

    def test_no_trend_means_no_match(self):
        trend_collection = mock.Mock()
        trend_collection.find_one.return_value = None
        self.assertIsNone(s.context_from_hits([hit()], trends=trend_collection))

    def test_disabled_runtime_makes_no_provider_calls(self):
        with mock.patch.dict(os.environ, {"SOCIAL_RADAR_ENABLED": "0"}), mock.patch.object(s, "_lookup") as lookup:
            self.assertIsNone(s.social_context({"name": "cargo pants"}))
            lookup.assert_not_called()

    def test_runtime_caches_results_for_ten_minutes_and_copies(self):
        with mock.patch.dict(os.environ, {"SOCIAL_RADAR_ENABLED": "1"}), \
                mock.patch.object(s, "_lookup", return_value={"term": "cargo pants"}) as lookup:
            first = s.social_context({"name": "cargo pants"})
            first["term"] = "changed"
            self.assertEqual(s.social_context({"name": "cargo pants"})["term"], "cargo pants")
            self.assertEqual(lookup.call_count, 1)
            self.assertGreater(s._CACHE["cargo pants"][0] - time.monotonic(), 590)

    def test_provider_error_returns_none_and_is_cached(self):
        with mock.patch.dict(os.environ, {"SOCIAL_RADAR_ENABLED": "1"}), \
                mock.patch.object(s, "_lookup", side_effect=RuntimeError("private provider error")) as lookup:
            self.assertIsNone(s.social_context({"name": "cargo pants"}))
            self.assertIsNone(s.social_context({"name": "cargo pants"}))
            self.assertEqual(lookup.call_count, 1)

    def test_timeout_does_not_block_and_completed_call_is_evicted(self):
        release = threading.Event()
        started = threading.Event()
        def blocked(query, cfg):
            started.set()
            release.wait(timeout=2)
            return {"term": "cargo pants"}
        cfg = {**s.momentum_config(), "lookup_timeout_seconds": .02}
        with mock.patch.dict(os.environ, {"SOCIAL_RADAR_ENABLED": "1"}), \
                mock.patch.object(s, "momentum_config", return_value=cfg), \
                mock.patch.object(s, "_lookup", side_effect=blocked):
            began = time.monotonic()
            try:
                self.assertIsNone(s.social_context({"name": "cargo pants"}))
                self.assertLess(time.monotonic() - began, .2)
                self.assertTrue(started.is_set())
                self.assertIn("cargo pants", s._INFLIGHT)
            finally:
                release.set()
            for _ in range(50):
                if "cargo pants" not in s._INFLIGHT:
                    break
                time.sleep(.005)
            self.assertNotIn("cargo pants", s._INFLIGHT)
            self.assertEqual(s.social_context({"name": "cargo pants"})["term"], "cargo pants")

    def test_sanitization_and_post_links(self):
        text = s.sanitize_snippet("Hi @alice.example \u2014 see https://site.example/u/bob and carol.bsky.social")
        self.assertNotIn("alice", text)
        self.assertNotIn("bob", text)
        self.assertNotIn("carol", text)
        self.assertNotIn("\u2014", text)
        self.assertEqual(s.post_url("at://did:plc:abc/app.bsky.feed.post/xyz"),
                         "https://bsky.app/profile/did:plc:abc/post/xyz")
        self.assertIsNone(s.post_url("javascript:bad"))


if __name__ == "__main__":
    unittest.main()
