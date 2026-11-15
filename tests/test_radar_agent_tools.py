"""Radar evidence tools and write_decision rails, on a throwaway local Mongo database with fixtures.
No Atlas, no network, no paid calls. The FashionCLIP model and image fetch are mocked."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import radar_agent_tools as rt
from pymongo import MongoClient

LOCAL = "mongodb://127.0.0.1:27017"
SOURCE = "https://example.org/press/good-style"


def _weeks(counts, saturated=()):
    """53 UTC Monday weeks from 2025-11-10; first and last are partial edge windows."""
    from datetime import date, timedelta
    start = date(2025, 11, 10)
    out = []
    for i, c in enumerate(counts):
        out.append({"week": (start + timedelta(weeks=i)).isoformat(), "count": c, "corpus_count": 1000,
                    "normalized": c / 1000, "saturated": i in saturated, "complete": 0 < i < len(counts) - 1})
    return out


def _trend(term, counts, **extra):
    return {"term": term, "weekly_counts": _weeks(counts), "post_count": sum(counts), "authors": 10,
            "breadth": 4, "concentration": 0.3, "weeks_to_breadth_3": 2, "observed_weeks": 0,
            "confidence": "low", "momentum": "unknown", **extra}


def _client():
    try:
        c = MongoClient(LOCAL, serverSelectionTimeoutMS=5000)
        c.server_info()
        return c
    except Exception:  # noqa: BLE001
        return None


CLIENT = _client()


@unittest.skipIf(CLIENT is None, "local MongoDB not running")
class ToolsTest(unittest.TestCase):
    def setUp(self):
        self.db = CLIENT[f"radar_tools_test_{os.getpid()}"]
        CLIENT.drop_database(self.db.name)
        good = [3] * 53                       # 159 posts, 51 metric weeks with posts
        thin = [1] * 53                       # 53 posts: under 100
        bunched = [0] * 20 + [40] * 5 + [0] * 28   # 200 posts, 5 weeks with posts: under 8
        self.db.social_trends.insert_many([
            _trend("good style", good), _trend("thin style", thin), _trend("bunched style", bunched),
            _trend("mesh flats", good)])
        posts = []
        for i in range(30):
            posts.append({"uri": f"at://did:plc:a{i % 6}/app.bsky.feed.post/p{i}", "author_hash": f"h{i % 6}",
                          "term": "good style", "terms": ["good style"], "text": f"love my good style @someone {i}",
                          "created_at": f"2026-03-{(i % 28) + 1:02d}T10:00:00.000000Z", "like_count": i,
                          "repost_count": 0, "reply_parent_uri": "at://x" if i % 3 == 0 else None,
                          "quote_uri": None, "mention_author_hashes": []})
        self.db.social_posts.insert_many(posts)
        self.db.social_communities.insert_many(
            [{"author_hash": f"h{i}", "community_id": "c1" if i < 4 else "c2", "name": f"comm {i < 4}"}
             for i in range(6)])
        self.db.trends.insert_many([
            {"name": "Good style revival", "origin": "seed", "sources": [{"title": "Press", "url": SOURCE}],
             "lifespan_label": "a season"},
            {"name": "Unrelated thing", "origin": "seed", "sources": [{"title": "x", "url": "https://x.org"}]}])
        self.db.item_materials.insert_many([
            {"item_key": "k-stated", "title": "Good Style Shirt", "stated_composition": "100% cotton",
             "stated_group": "cotton_denim", "image_url": "https://img.example/a.jpg", "status": "ok"},
            {"item_key": "k-bare", "title": "The good style tee", "stated_composition": None,
             "image_url": "https://img.example/b.jpg", "status": "ok"}])
        self.tmp = tempfile.TemporaryDirectory()
        rt.start_run("test-run", db=self.db, out_dir=Path(self.tmp.name))

    def tearDown(self):
        CLIENT.drop_database(self.db.name)
        self.tmp.cleanup()

    # ----- evidence tools -----

    def test_tool_names_and_list(self):
        self.assertEqual(rt.TOOL_NAMES, ["get_style_stats", "get_weekly_series", "get_connectivity",
                                         "sample_posts", "get_press_sources", "run_rule_metrics",
                                         "get_item_materials", "estimate_material_from_photo",
                                         "write_decision", "record_event"])
        for t in rt.TOOLS:
            self.assertTrue(t.__doc__ and t.__annotations__)

    def test_every_tool_returns_evidence_id(self):
        out = [rt.get_style_stats("good style"), rt.get_weekly_series("good style", 10),
               rt.get_connectivity("good style"), rt.sample_posts("good style", 4),
               rt.get_press_sources("good style"), rt.run_rule_metrics("good style"),
               rt.get_item_materials("good style"), rt.record_event("good style", "note", "checked")]
        for r in out:
            self.assertTrue(r["ok"], r)
            self.assertTrue(r["evidence_id"].startswith("ev:"))
        self.assertEqual(len({r["evidence_id"] for r in out}), len(out))
        self.assertTrue((Path(self.tmp.name) / "evidence.jsonl").exists())

    def test_stats_use_metric_weeks_not_observed_weeks(self):
        r = rt.get_style_stats("good style")
        self.assertEqual((r["post_count"], r["metric_weeks"], r["weeks_with_posts"]), (159, 51, 51))
        self.assertNotIn("observed_weeks", r)

    def test_no_evaluation_labels_exposed(self):
        r = rt.get_style_stats("good style")
        for forbidden in ("label", "press_status"):
            self.assertNotIn(forbidden, r)

    def test_weekly_series_markers_and_limit(self):
        r = rt.get_weekly_series("good style", 100)
        self.assertEqual(len(r["weeks"]), 53)
        self.assertIn("partial_edge", r["weeks"][0]["markers"])
        self.assertIsNone(r["weeks"][0]["S_w"])
        self.assertAlmostEqual(r["weeks"][5]["S_w"], 0.003)
        self.assertEqual(len(rt.get_weekly_series("good style", 4)["weeks"]), 4)

    def test_connectivity(self):
        r = rt.get_connectivity("good style")
        self.assertEqual(r["posts"], 30)
        self.assertAlmostEqual(r["interaction_share"], 10 / 30, places=5)
        self.assertEqual(r["community_count"], 2)
        self.assertEqual(r["top_communities"][0]["community_id"], "c1")

    def test_sample_posts_capped_deterministic_sanitized(self):
        a = rt.sample_posts("good style", 50)
        self.assertEqual(len(a["posts"]), 20)
        self.assertTrue(all("@" not in p["snippet"] for p in a["posts"]))
        rt.start_run("test-run", db=self.db, out_dir=Path(self.tmp.name))
        b = rt.sample_posts("good style", 50)
        self.assertEqual([p["uri"] for p in a["posts"]], [p["uri"] for p in b["posts"]])
        w = rt.sample_posts("good style", 20, week="2026-03-02")
        self.assertTrue(all(p["created_at"] >= "2026-03-02" and p["created_at"] < "2026-03-09" for p in w["posts"]))

    def test_press_sources_match_all_words(self):
        r = rt.get_press_sources("good style")
        self.assertEqual(r["source_urls"], [SOURCE])

    def test_item_materials_by_name(self):
        r = rt.get_item_materials("good style")
        self.assertEqual({i["item_key"] for i in r["items"]}, {"k-stated", "k-bare"})
        self.assertEqual(rt.get_item_materials("thin style")["items"], [])

    def test_per_style_budget_of_12(self):
        for _ in range(12):
            self.assertTrue(rt.get_style_stats("good style")["ok"])
        r = rt.get_style_stats("good style")
        self.assertFalse(r["ok"])
        self.assertIn("tool budget", r["error"])
        self.assertTrue(rt.get_style_stats("thin style")["ok"])  # other styles unaffected
        rt.start_run("next-run", db=self.db, out_dir=Path(self.tmp.name))  # new run resets
        self.assertTrue(rt.get_style_stats("good style")["ok"])

    # ----- write_decision rails -----

    def _ev(self, style):
        return [rt.get_style_stats(style)["evidence_id"]]

    def test_decision_stored_with_fields(self):
        ev = self._ev("good style")
        r = rt.write_decision("good style", "established", "medium", ["steady share all year"], ev,
                              model_id="claude-haiku-5-5", prompt_version="p1")
        self.assertTrue(r["ok"], r)
        doc = self.db.radar_decisions.find_one({"style": "good style", "run_id": "test-run"})
        for field in ("rule_label", "agent_label", "final_label", "decision_source", "agree", "confidence",
                      "reasons", "evidence_ids", "metric_version", "prompt_version", "model_id", "run_id"):
            self.assertIn(field, doc)
        self.assertEqual(doc["decision_source"], "agent")
        self.assertEqual(doc["agree"], doc["rule_label"] == "established")
        self.assertIsNone(doc["lifespan_weeks_est"])
        idx = self.db.radar_decisions.index_information()["style_run_unique"]
        self.assertTrue(idx["unique"])
        # Rewriting in the same run replaces, not duplicates.
        rt.write_decision("good style", "unknown", "low", ["x"], ev)
        self.assertEqual(self.db.radar_decisions.count_documents({"style": "good style"}), 1)

    def test_rail1_thin_data_only_unknown(self):
        for style in ("thin style", "bunched style"):
            ev = self._ev(style)
            r = rt.write_decision(style, "micro_trend", "low", ["spike"], ev)
            self.assertFalse(r["ok"])
            self.assertIn("too thin", r["error"])
            self.assertTrue(rt.write_decision(style, "unknown", "low", ["thin"], ev)["ok"])

    def test_rail2_evidence_required_and_must_be_issued(self):
        self.assertFalse(rt.write_decision("good style", "unknown", "low", ["x"], [])["ok"])
        fake = rt.write_decision("good style", "unknown", "low", ["x"], ["ev:get_style_stats:good-style:01:abc"])
        self.assertFalse(fake["ok"])
        other = self._ev("thin style")  # issued, but for another style
        self.assertFalse(rt.write_decision("good style", "unknown", "low", ["x"], other)["ok"])

    def test_rail3_lifespan_measured_by_code_with_source(self):
        ev = self._ev("good style")
        run = {"start_week": "2026-01-05", "end_week": "2026-03-30", "weeks_active": 13, "peak_week": "2026-02-02"}
        with mock.patch.object(rt.micro_trend, "measured_active_run", return_value=run):
            bad = rt.write_decision("good style", "established", "low", ["x"], ev,
                                    lifespan_source_url="https://not-a-source.org")
            self.assertFalse(bad["ok"])
            ok = rt.write_decision("good style", "established", "low", ["x"], ev, lifespan_source_url=SOURCE)
        self.assertTrue(ok["ok"], ok)
        self.assertEqual((ok["lifespan_weeks_est"], ok["lifespan_bucket"]), (13, "short"))
        with mock.patch.object(rt.micro_trend, "measured_active_run", return_value=None):
            none = rt.write_decision("good style", "established", "low", ["x"], ev, lifespan_source_url=SOURCE)
        self.assertTrue(none["ok"])
        self.assertIsNone(none["lifespan_weeks_est"])
        self.assertIsNone(none["lifespan_bucket"])
        self.assertIsNone(none["lifespan_source_url"])
        # The agent cannot type a lifespan: write_decision has no such argument.
        with self.assertRaises(TypeError):
            rt.write_decision("good style", "unknown", "low", ["x"], ev, lifespan_weeks_est=4)

    def test_bucket_edges(self):
        self.assertEqual([rt.lifespan_bucket(w) for w in (16, 17, 52, 53, None)],
                         ["short", "medium", "medium", "long", None])

    def test_manual_styles_refuse_agent_and_store_no_lifespan(self):
        ev = self._ev("mesh flats")
        r = rt.write_decision("mesh flats", "micro_trend", "low", ["x"], ev)
        self.assertFalse(r["ok"])
        self.assertIn("manual", r["error"])
        out = rt.write_manual_decisions(["mesh flats", "plain black tee"])
        self.assertEqual(len(out), 2)
        for d in self.db.radar_decisions.find({"decision_source": "manual"}):
            self.assertIsNone(d["lifespan_weeks_est"])
            self.assertIsNone(d["lifespan_bucket"])
            self.assertTrue(d["excluded_from_agreement"])
        mesh = self.db.radar_decisions.find_one({"style": "mesh flats"})
        self.assertEqual(mesh["final_label"], "micro_trend")
        self.assertIsNone(self.db.radar_decisions.find_one({"style": "plain black tee"})["rule_label"])

    def test_jsonl_fallback_when_mongo_write_fails(self):
        ev = self._ev("good style")
        with mock.patch.object(type(self.db.radar_decisions), "replace_one", side_effect=RuntimeError("down")), \
                self.assertLogs("radar_agent_tools", level="WARNING"):
            r = rt.write_decision("good style", "unknown", "low", ["x"], ev)
        self.assertTrue(r["ok"])
        self.assertEqual(r["stored"], "jsonl_fallback")
        lines = (Path(self.tmp.name) / "decisions.jsonl").read_text().splitlines()
        self.assertIn('"stored": "jsonl_fallback"', lines[-1])

    # ----- photo material tool -----

    def test_photo_tool_disabled_by_flag(self):
        with mock.patch.dict(os.environ, {"MATERIALS_PHOTO_ENABLED": "0"}):
            r = rt.estimate_material_from_photo("good style", item_key="k-bare")
        self.assertEqual(r["status"], "disabled")
        self.assertIsNone(r["line"])

    def test_photo_tool_skips_stated_composition(self):
        with mock.patch.dict(os.environ, {"MATERIALS_PHOTO_ENABLED": "1"}), \
                mock.patch.object(rt, "_fetch_image") as fetch:
            r = rt.estimate_material_from_photo("good style", item_key="k-stated")
        fetch.assert_not_called()
        self.assertEqual(r["status"], "skipped_stated_composition")
        self.assertIsNone(r["line"])

    def test_photo_tool_one_line_never_changes_label(self):
        from backend.services import materials_photo as mp
        est = {"group": "wool_knit", "score": 0.9, "margin": 0.8, "confidence": "low", "status": "ok"}
        with mock.patch.dict(os.environ, {"MATERIALS_PHOTO_ENABLED": "1", "MATERIALS_STORE_USER_ITEMS": "0"}), \
                mock.patch.object(rt, "_fetch_image", return_value=b"img"), \
                mock.patch.object(mp, "estimate_image", return_value=est):
            r = rt.estimate_material_from_photo("good style", item_key="k-bare")
        self.assertTrue(r["ok"])
        self.assertFalse(r["affects_label"])
        self.assertTrue(r["line"].startswith("Estimated from photo: likely wool or knit, low confidence, about 57 "
                                             "percent accurate on a 30-item Shopify retailer test"))
        self.assertNotIn("Uniqlo", r["line"])
        self.assertEqual(r["line"].count("\n"), 0)
        for key in ("rule_label", "agent_label", "final_label", "lifespan_weeks_est", "lifespan_bucket", "verdict"):
            self.assertNotIn(key, r)
        # Not stored unless MATERIALS_STORE_USER_ITEMS is on.
        self.assertEqual(self.db.item_materials.count_documents({"source": "radar_agent"}), 0)
        # Citing the photo evidence does not change the decision fields.
        d = rt.write_decision("good style", "established", "low", ["x"], [r["evidence_id"]])
        self.assertEqual((d["agent_label"], d["final_label"]), ("established", "established"))

    def test_photo_tool_stores_only_when_flag_on(self):
        from backend.services import materials_photo as mp
        est = {"group": "linen", "score": 0.9, "margin": 0.8, "confidence": "medium", "status": "ok"}
        with mock.patch.dict(os.environ, {"MATERIALS_PHOTO_ENABLED": "1", "MATERIALS_STORE_USER_ITEMS": "1"}), \
                mock.patch.object(rt, "_fetch_image", return_value=b"img"), \
                mock.patch.object(mp, "estimate_image", return_value=est):
            rt.estimate_material_from_photo("good style", image_url="https://img.example/c.jpg")
        doc = self.db.item_materials.find_one({"source": "radar_agent"})
        self.assertIsNotNone(doc)
        self.assertNotIn("image_bytes", doc)


class MaterialsAccuracyTest(unittest.TestCase):
    def test_reads_measured_numbers_from_eval_doc(self):
        acc = rt.materials_accuracy()
        self.assertEqual((acc["correct"], acc["n"], acc["accuracy_pct"], acc["baseline_pct"]), (17, 30, 56.7, 16.7))
        self.assertEqual(acc["test_set"], "Shopify retailer")

    def test_low_confidence_when_not_above_baseline(self):
        est = {"group": "linen", "confidence": "medium"}
        weak = {"correct": 5, "n": 30, "accuracy_pct": 16.7, "baseline_pct": 16.7, "test_set": "Shopify retailer"}
        self.assertIn("likely linen, low confidence, about 17 percent", rt.materials_line(est, weak))
        strong = dict(weak, accuracy_pct=56.7)
        self.assertIn("medium confidence, about 57 percent", rt.materials_line(est, strong))
        self.assertIsNone(rt.materials_line({"group": "unknown", "confidence": None}, strong))


if __name__ == "__main__":
    unittest.main()
