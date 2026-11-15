"""GET /social/trends/{id}/timeline on local Mongo fixtures. No network, no paid call."""
import json
import os
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient
from pymongo import MongoClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_micro_trend import doc, micro

from backend import main
from backend.services import radar_read, social_ask

LOCAL = "mongodb://127.0.0.1:27017"  # always local: never create test databases in the .env database


def _client():
    try:
        c = MongoClient(LOCAL, serverSelectionTimeoutMS=2000, tz_aware=True)
        c.server_info()
        return c
    except Exception:  # noqa: BLE001
        return None


CLIENT = _client()


def tearDownModule():
    if CLIENT is not None:
        CLIENT.close()
STAPLE = [40] * 40


def decision(style, run_id, created_at, **changes):
    return {"style": style, "run_id": run_id, "rule_label": "micro_trend", "agent_label": "micro_trend",
            "final_label": "micro_trend", "decision_source": "agent", "agree": True, "confidence": "medium",
            "reasons": [f"reason from {run_id}"], "evidence_ids": ["ev:get_weekly_series:x:01:abc"],
            "metric_version": "micro_trend_rules_v1", "prompt_version": "p1", "model_id": "claude-haiku-5-5",
            "created_at": created_at, **changes}


@unittest.skipIf(CLIENT is None, "local MongoDB not running")
class TimelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mongo = CLIENT
        cls.db = cls.mongo[f"radar_timeline_test_{os.getpid()}"]

    @classmethod
    def tearDownClass(cls):
        # Drop the collections, not the database: works for users without the dropDatabase role.
        for name in cls.db.list_collection_names():
            cls.db.drop_collection(name)

    def setUp(self):
        self.db["social_trends"].delete_many({})
        self.db["radar_decisions"].delete_many({})
        self.db["social_trends"].insert_many([
            micro(term="test style"), micro(term="mesh flats"), doc(STAPLE, term="plain staple")])
        self.runs = tempfile.TemporaryDirectory()
        self.addCleanup(self.runs.cleanup)
        for patch in (mock.patch.object(social_ask, "database", return_value=self.db),
                      mock.patch.object(radar_read, "RUNS_DIR", Path(self.runs.name))):
            patch.start()
            self.addCleanup(patch.stop)
        self.client = TestClient(main.app)

    def get(self, style):
        return self.client.get(f"/social/trends/{style}/timeline")

    def test_no_decision_yet_falls_back_to_rule(self):
        r = self.get("test style")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIsNone(body["agent_label"])
        self.assertEqual((body["rule_label"], body["final_label"], body["decision_source"]),
                         ("micro_trend", "micro_trend", "rule"))
        self.assertEqual(body["reasons"], [])
        self.assertEqual(len(body["series"]), 42)
        self.assertTrue(body["series"][0]["partial_edge"])
        self.assertIsNone(body["series"][0]["smoothed_share"])
        self.assertIsNotNone(body["series"][20]["smoothed_share"])
        self.assertEqual({m["kind"] for m in body["markers"]}, {"peak", "active_start", "active_end"})
        self.assertEqual(body["sample_run"]["source"], "Bluesky sample")
        self.assertEqual(body["sample_lifespan"]["weeks_active"], body["sample_run"]["weeks_active"])
        self.assertIsNone(body["editorial"])
        self.assertIn("Bluesky sample", body["note"])

    def test_latest_agent_decision_is_used(self):
        self.db["radar_decisions"].insert_many([
            decision("test style", "run-old", datetime(2026, 11, 13, tzinfo=UTC), agent_label="unknown",
                     final_label="unknown", agree=False),
            decision("test style", "run-new", datetime(2026, 11, 14, tzinfo=UTC)),
        ])
        body = self.get("test style").json()
        self.assertEqual((body["agent_label"], body["final_label"], body["decision_source"]),
                         ("micro_trend", "micro_trend", "agent"))
        self.assertEqual(body["run_id"], "run-new")
        self.assertEqual(body["reasons"], ["reason from run-new"])
        self.assertEqual(body["evidence_count"], 1)
        self.assertEqual(body["decided_at"], "2026-11-14T00:00:00Z")

    def test_agent_disagreeing_with_rule_drops_sample_lifespan(self):
        self.db["radar_decisions"].insert_one(decision("test style", "run-1", datetime(2026, 11, 14, tzinfo=UTC),
                                                       agent_label="established", final_label="established",
                                                       agree=False))
        body = self.get("test style").json()
        self.assertEqual((body["rule_label"], body["agent_label"]), ("micro_trend", "established"))
        self.assertIsNone(body["sample_lifespan"])
        self.assertIsNotNone(body["sample_run"])

    def test_newer_jsonl_fallback_wins(self):
        self.db["radar_decisions"].insert_one(decision("test style", "run-db", datetime(2026, 11, 13, tzinfo=UTC)))
        folder = Path(self.runs.name) / "run-file"
        folder.mkdir()
        row = decision("test style", "run-file", "2026-11-14T05:00:00Z", agent_label="still_active",
                       final_label="still_active", stored="jsonl_fallback")
        (folder / "decisions.jsonl").write_text("not json\n" + json.dumps(row) + "\n")
        body = self.get("test style").json()
        self.assertEqual((body["run_id"], body["agent_label"]), ("run-file", "still_active"))

    def test_manual_style_shows_editorial_label_without_lifespan(self):
        self.db["radar_decisions"].insert_one(decision("mesh flats", "run-1", datetime(2026, 11, 14, tzinfo=UTC),
                                                       agent_label=None, decision_source="manual"))
        body = self.get("mesh flats").json()
        self.assertEqual(body["decision_source"], "manual")
        self.assertEqual(body["final_label"], "micro_trend")
        self.assertIsNone(body["agent_label"])
        self.assertEqual(body["editorial"]["display"], "Editorial label (team judgment, not Radar data)")
        self.assertEqual(body["editorial"]["source_text"], "Claude judgment, unverified")
        self.assertEqual(body["reasons"], ["Claude judgment, unverified"])
        self.assertIsNone(body["sample_run"])
        self.assertIsNone(body["sample_lifespan"])
        self.assertEqual([m["kind"] for m in body["markers"]], ["peak"])
        self.assertNotIn("lifespan_weeks_est", body)

    def test_staple_has_no_sample_lifespan(self):
        body = self.get("plain staple").json()
        self.assertEqual(body["rule_label"], "established")
        self.assertIsNone(body["sample_lifespan"])

    def test_case_insensitive_lookup_and_404(self):
        self.assertEqual(self.get("Test Style").json()["style"], "test style")
        self.assertEqual(self.get("nonsense query xyz").status_code, 404)

    def test_database_failure_is_503(self):
        with mock.patch.object(social_ask, "database", side_effect=RuntimeError("down")):
            self.assertEqual(self.get("test style").status_code, 503)

    def test_sample_lifespan_for_term_reads_db(self):
        out = radar_read.sample_lifespan_for_term("test style", db=self.db, runs_dir=Path(self.runs.name))
        self.assertEqual((out["final_label"], out["source"]), ("micro_trend", "Bluesky sample"))
        self.assertIsNone(radar_read.sample_lifespan_for_term("mesh flats", db=self.db))
        self.assertIsNone(radar_read.sample_lifespan_for_term("missing", db=self.db))


if __name__ == "__main__":
    unittest.main()


class PrewarmTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        import prewarm_social
        self.p = prewarm_social

    def test_missing_products_file_skips_cleanly(self):
        import io
        from contextlib import redirect_stdout
        out = io.StringIO()
        with redirect_stdout(out):
            code = self.p.main(["--products", str(Path(tempfile.gettempdir()) / "no_such_demo_products.json")])
        self.assertEqual(code, 0)
        self.assertIn("skipped:", out.getvalue())
        self.assertIn("not found", out.getvalue())

    def test_check_uses_budget_then_lookup_and_reports_label(self):
        calls = []
        row = self.p.check({"name": "Barrel leg jeans", "category": "jeans", "expected_term": "barrel jeans"},
                           budget=lambda: calls.append("budget"),
                           lookup=lambda q: calls.append("lookup") or {"term": "barrel jeans", "similarity": .81},
                           label_for=lambda term: {"final_label": "established", "decision_source": "agent",
                                                   "sample_lifespan": None})
        self.assertEqual(calls, ["budget", "lookup"])
        self.assertEqual((row["status"], row["term"], row["final_label"], row["expected_ok"]),
                         ("match", "barrel jeans", "established", True))

    def test_no_match_and_malformed_file(self):
        row = self.p.check({"name": "zzz qqq"}, budget=lambda: None, lookup=lambda q: None)
        self.assertEqual(row["status"], "no_match")
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write('{"products": [{"category": "jeans"}]}')
        self.addCleanup(os.unlink, fh.name)
        with self.assertRaises(ValueError):
            self.p.load_products(Path(fh.name))
