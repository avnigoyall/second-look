"""radar_to_trends: idempotence, manual exclusion, unmappable skip, name and signature guards.
Throwaway local Mongo database. No Atlas, no network, no paid calls."""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import radar_to_trends as rtt
from pymongo import MongoClient

from backend.trends import ensure_trend_indexes

LOCAL = "mongodb://127.0.0.1:27017"
RUN = "run-test"
RUNS = {"barrel jeans": {"start_week": "2026-03-02", "end_week": "2026-05-18", "weeks_active": 12,
                         "peak_week": "2026-04-06"}}


def _client():
    try:
        c = MongoClient(LOCAL, serverSelectionTimeoutMS=5000)
        c.server_info()
        return c
    except Exception:  # noqa: BLE001
        return None


CLIENT = _client()


def _dec(style, source="agent", label="micro_trend", run_id=RUN):
    return {"style": style, "run_id": run_id, "final_label": label, "decision_source": source}


@unittest.skipIf(CLIENT is None, "local MongoDB not running")
class RadarToTrendsTest(unittest.TestCase):
    def setUp(self):
        self.db = CLIENT[f"radar_to_trends_test_{os.getpid()}"]
        CLIENT.drop_database(self.db.name)
        ensure_trend_indexes(self.db.trends)
        self.db.radar_decisions.insert_many([
            _dec("barrel jeans"),                   # agent micro_trend, measured run -> trend with lifespan
            _dec("bubble skirt", source="rule"),    # rule micro_trend, no measured run -> trend, no lifespan
            _dec("mesh flats", source="manual"),    # manual label -> never a trend
            _dec("micro bag"),                      # in manual_labels.json even if the source says agent
            _dec("cherry red"),                     # no category -> unmappable
            _dec("cargo pants", label="established"),   # not micro_trend -> not a candidate
            _dec("kitten heels", run_id="other-run"),   # other run -> not a candidate
        ])

    def tearDown(self):
        CLIENT.drop_database(self.db.name)

    def run_once(self, dry_run=False):
        return {r["style"]: r for r in rtt.write_trends(
            decisions=self.db.radar_decisions, trends=self.db.trends, run_id=RUN,
            active_run_fn=lambda s: RUNS.get(s), is_manual_fn=lambda s: s in {"mesh flats", "micro bag"},
            dry_run=dry_run)}

    def test_writes_validated_agent_trends(self):
        rows = self.run_once()
        self.assertEqual(set(rows), {"barrel jeans", "bubble skirt", "mesh flats", "micro bag", "cherry red"})
        self.assertEqual(rows["barrel jeans"]["action"], "inserted")
        self.assertEqual(rows["bubble skirt"]["action"], "inserted")
        doc = self.db.trends.find_one({"name": "barrel jeans"})
        url = "https://bsky.app/search?q=barrel%20jeans"
        self.assertEqual(doc["origin"], "agent")
        self.assertEqual(doc["category"], "jeans")
        self.assertEqual(doc["features"], {"cut": "wide", "color": None, "fabric": "denim"})
        self.assertEqual((doc["lifespan_weeks_est"], doc["lifespan_bucket"]), (12, "short"))
        self.assertEqual(doc["lifespan_source_url"], url)
        self.assertIn(url, [s["url"] for s in doc["sources"]])
        skirt = self.db.trends.find_one({"name": "bubble skirt"})
        self.assertIsNone(skirt["lifespan_weeks_est"])
        self.assertIsNone(skirt["lifespan_source_url"])
        self.assertEqual(skirt["radar_decision_source"], "rule")

    def test_idempotent(self):
        self.run_once()
        before = list(self.db.trends.find({}, {"_id": 0}).sort("name", 1))
        rows = self.run_once()
        self.assertEqual(rows["barrel jeans"]["action"], "unchanged")
        self.assertEqual(rows["bubble skirt"]["action"], "unchanged")
        self.assertEqual(list(self.db.trends.find({}, {"_id": 0}).sort("name", 1)), before)
        self.assertEqual(self.db.trends.count_documents({}), 2)

    def test_manual_never_written(self):
        rows = self.run_once()
        for style in ("mesh flats", "micro bag"):
            self.assertEqual(rows[style]["action"], "skipped")
            self.assertIn("manual", rows[style]["detail"])
            self.assertIsNone(self.db.trends.find_one({"name": style}))

    def test_unmappable_skipped_and_recorded(self):
        rows = self.run_once()
        self.assertEqual(rows["cherry red"]["action"], "skipped")
        self.assertIn("unmappable", rows["cherry red"]["detail"])
        self.assertIsNone(self.db.trends.find_one({"name": "cherry red"}))
        orig = rtt.SKIPS_DIR
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            rtt.SKIPS_DIR = Path(tmp)
            try:
                path = rtt.record_skips(RUN, list(rows.values()))
            finally:
                rtt.SKIPS_DIR = orig
            lines = path.read_text().splitlines()
        self.assertEqual(len(lines), 3)  # mesh flats, micro bag, cherry red

    def test_seed_name_and_signature_not_overwritten(self):
        seed = {"name": "Barrel Jeans", "category": "jeans", "features": {}, "origin": "seed",
                "lifespan_label": "x", "sources": [{"title": "t", "url": "https://example.org"}]}
        twin = {"name": "Puff skirt", "category": "skirt", "features": {"cut": None, "color": None, "fabric": None},
                "origin": "seed", "lifespan_label": "x", "sources": [{"title": "t", "url": "https://example.org"}]}
        self.db.trends.insert_many([seed, twin])
        rows = self.run_once()
        self.assertIn("name held by a seed trend", rows["barrel jeans"]["detail"])
        self.assertIn("Puff skirt", rows["bubble skirt"]["detail"])
        self.assertEqual(self.db.trends.count_documents({}), 2)
        self.assertEqual(self.db.trends.find_one({"name": "Barrel Jeans"})["origin"], "seed")

    def test_dry_run_writes_nothing(self):
        rows = self.run_once(dry_run=True)
        self.assertEqual(rows["barrel jeans"]["action"], "would write")
        self.assertEqual(self.db.trends.count_documents({}), 0)

    def test_map_style(self):
        self.assertEqual(rtt.map_style("sambas")[0], "shoes")
        self.assertEqual(rtt.map_style("quarter zip")[0], "sweater")
        self.assertEqual(rtt.map_style("butter yellow"), (None, {"cut": None, "color": "yellow", "fabric": None}))
        self.assertEqual(rtt.map_style("poplin shirt"), ("shirt", {"cut": None, "color": None, "fabric": "cotton"}))


if __name__ == "__main__":
    unittest.main()
