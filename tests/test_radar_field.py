"""Optional radar field: stored agent decisions only, omitted when the flag is off or the lookup times out."""
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient
from pymongo.errors import ExecutionTimeout

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_social_verdict import context
from test_verdict import EngineTest, reply

from backend import main, models
from backend.services import radar_field as rf
from backend.services import social
from backend.services import verdict as v


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, key, direction):
        self.rows = sorted(self.rows, key=lambda r: r[key], reverse=direction == -1)
        return self

    def limit(self, n):
        return iter(self.rows[:n])


class Collection:
    def __init__(self, rows, error=None):
        self.rows, self.error = rows, error

    def _match(self, query):
        if self.error:
            raise self.error
        return [r for r in self.rows if all(r.get(k) == val for k, val in query.items())]

    def find(self, query, projection=None):
        return Cursor(self._match(query))

    def find_one(self, query, projection=None):
        rows = self._match(query)
        return rows[0] if rows else None


class FakeDb(dict):
    pass


def decision(**changes):
    return {"style": "barrel jeans", "run_id": "radar-main-1", "rule_label": "established",
            "agent_label": "micro_trend", "final_label": "micro_trend", "decision_source": "agent",
            "confidence": "medium", "reasons": ["r"], "created_at": "2026-11-14T10:00:00Z", **changes}


def db_with(decisions, trend=None, error=None):
    trend = trend if trend is not None else {"term": "barrel jeans", "post_count": 412}
    return FakeDb(radar_decisions=Collection(decisions, error), social_trends=Collection([trend], error))


MATCHED = {"state": "matched"}
SOCIAL = {"term": "barrel jeans", "similarity": 0.71}
RUN = {"weeks_active": 9, "weeks_to_rise": 3}


class RadarForTests(unittest.TestCase):
    def test_matched_uses_agent_label_never_rule_or_rule_source(self):
        rows = [decision(), decision(decision_source="rule", agent_label=None, final_label="established",
                                     created_at="2026-11-15T00:00:00Z")]
        with mock.patch.object(rf.micro_trend, "compute_rule_metrics", return_value={}), \
                mock.patch.object(rf.radar_read, "sample_run", return_value=RUN):
            out = rf.radar_for(SOCIAL, MATCHED, db_with(rows))
        self.assertEqual(out["label"], "micro_trend")
        self.assertEqual(out["matched_style"], "barrel jeans")
        self.assertEqual(out["similarity"], 0.71)
        self.assertEqual(out["confidence"], "medium")
        self.assertEqual((out["weeks_to_rise"], out["weeks_active"]), (3, 9))
        self.assertEqual(out["evidence_note"], "Bluesky sample, 412 posts")
        self.assertIn("Bluesky sample", out["summary"])
        self.assertIsNone(out["timeline_url"])
        models.RadarField.model_validate(out)

    def test_latest_agent_decision_wins(self):
        rows = [decision(agent_label="established", created_at="2026-11-15T00:00:00Z"), decision()]
        out = rf.radar_for(SOCIAL, MATCHED, db_with(rows))
        self.assertEqual(out["label"], "established")
        self.assertIsNone(out["weeks_active"])  # weeks only for micro_trend

    def test_agent_unknown_is_returned_as_decided(self):
        out = rf.radar_for(SOCIAL, MATCHED, db_with([decision(agent_label="unknown", confidence="high")]))
        self.assertEqual((out["label"], out["confidence"]), ("unknown", "high"))
        self.assertNotEqual(out["summary"], rf.NO_DECISION_SUMMARY)
        self.assertIsNone(out["weeks_to_rise"])

    def test_style_without_agent_decision_is_unknown(self):
        rows = [decision(decision_source="manual", agent_label=None, final_label="micro_trend"),
                decision(decision_source="rule", agent_label=None)]
        out = rf.radar_for(SOCIAL, MATCHED, db_with(rows))
        self.assertEqual((out["label"], out["confidence"], out["summary"]),
                         ("unknown", "low", "No agent decision for this style."))
        self.assertEqual(out["matched_style"], "barrel jeans")

    def test_manual_style_is_unknown_even_with_a_stray_agent_row(self):
        with mock.patch.object(rf.micro_trend, "is_manual", return_value=True):
            out = rf.radar_for(SOCIAL, MATCHED, db_with([decision()]))
        self.assertEqual((out["label"], out["summary"]), ("unknown", rf.NO_DECISION_SUMMARY))

    def test_no_match(self):
        out = rf.radar_for(None, {"state": "no_match"}, db_with([decision()]))
        self.assertEqual(out, {"matched_style": None, "similarity": None, "label": "unknown", "confidence": "low",
                               "summary": "No matching style in our Bluesky sample.", "weeks_to_rise": None,
                               "weeks_active": None, "evidence_note": "Bluesky sample, 0 posts",
                               "timeline_url": None})

    def test_omitted_for_flag_off_timeout_error_and_busy(self):
        for state in ("off", "timeout", "error", "busy", "no_query", None):
            self.assertIsNone(rf.radar_for(None, {"state": state} if state else {}, db_with([decision()])), state)

    def test_mongo_timeout_omits_field(self):
        self.assertIsNone(rf.radar_for(SOCIAL, MATCHED, db_with([decision()], error=ExecutionTimeout("slow"))))


class SocialStatusTests(unittest.TestCase):
    def setUp(self):
        social.clear_cache()
        self.addCleanup(social.clear_cache)

    def test_flag_off(self):
        status = {}
        with mock.patch.dict(os.environ, {"SOCIAL_RADAR_ENABLED": "0"}):
            self.assertIsNone(social.social_context({"name": "Barrel jeans"}, status=status))
        self.assertEqual(status["state"], "off")

    def test_no_match_error_and_matched_keep_return_values(self):
        cases = [(lambda q, c: None, "no_match", None), (mock.Mock(side_effect=RuntimeError("x")), "error", None),
                 (lambda q, c: {"term": "t"}, "matched", {"term": "t"})]
        for lookup, state, result in cases:
            social.clear_cache()
            for _ in range(2):  # second call is a cache hit with the same status
                status = {}
                with mock.patch.dict(os.environ, {"SOCIAL_RADAR_ENABLED": "1"}), \
                        mock.patch.object(social, "_lookup", lookup):
                    self.assertEqual(social.social_context({"name": "Barrel jeans"}, status=status), result)
                self.assertEqual(status["state"], state)

    def test_timeout(self):
        status = {}
        cfg = {**social.momentum_config(), "lookup_timeout_seconds": 0.01}
        with mock.patch.dict(os.environ, {"SOCIAL_RADAR_ENABLED": "1"}), \
                mock.patch.object(social, "momentum_config", return_value=cfg), \
                mock.patch.object(social, "_lookup", side_effect=lambda q, c: time.sleep(0.2)):
            self.assertIsNone(social.social_context({"name": "Slow jeans"}, status=status))
        self.assertEqual(status["state"], "timeout")


def social_with(state, result=None):
    def fake(item, status=None):
        if status is not None and state:
            status["state"] = state
        return result
    return fake


FIELD = {"matched_style": "barrel jeans", "similarity": 0.8, "label": "established", "confidence": "high",
         "summary": "s", "weeks_to_rise": None, "weeks_active": None, "evidence_note": "Bluesky sample, 5 posts",
         "timeline_url": None}


class EngineRadarTests(EngineTest):
    def run_with(self, fake, radar=None, delay=0.0):
        self.replies = [reply("good_buy")]
        self.calls = []

        def radar_for(social_result, status):
            time.sleep(delay)
            return radar

        with mock.patch.object(v, "social_context", side_effect=fake), \
                mock.patch.object(v.radar_field, "radar_for", side_effect=radar_for):
            return self.run_engine()

    def test_flag_off_response_identical_and_field_absent(self):
        before, _ = self.run_with(lambda item, status=None: None)  # old behavior: no status at all
        off, _ = self.run_with(social_with("off"), radar=FIELD)
        self.assertIsNone(off.radar)
        self.assertNotIn("radar", off.model_dump())
        self.assertEqual(before.model_dump_json(), off.model_dump_json())

    def test_matched_adds_field_without_changing_verdict(self):
        base, base_record = self.run_with(social_with("matched", context()))
        out, record = self.run_with(social_with("matched", context()), radar=FIELD)
        self.assertEqual(out.radar.label, "established")
        self.assertEqual((out.verdict, out.reason, record["signals"]["allowed"]),
                         (base.verdict, base.reason, base_record["signals"]["allowed"]))
        self.assertNotIn("radar", self.calls[-1]["payload"])

    def test_slow_radar_lookup_omits_field(self):
        out, _ = self.run_with(social_with("matched", context()), radar=FIELD, delay=0.8)
        self.assertNotIn("radar", out.model_dump())


class ApiRadarTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        main.app.dependency_overrides[main.current_user] = lambda: {"_id": "test-user"}
        self.addCleanup(main.app.dependency_overrides.clear)

    def post(self, radar):
        result = models.AnalyzeResponse(status="ok", verdict_id="t", verdict="good_buy", reason="r", radar=radar)
        with mock.patch.object(main, "decide", return_value=(result, {"signals": {"timings": {}}})):
            return self.client.post("/analyze", json={"item": {"name": "Barrel jeans"}}).json()

    def test_absent_when_none(self):
        self.assertNotIn("radar", self.post(None))

    def test_timeline_url_encoded(self):
        body = self.post({**FIELD, "matched_style": "y2k / baggy"})
        self.assertEqual(body["radar"]["timeline_url"], "http://testserver/social/trends/y2k%20%2F%20baggy/timeline")
        self.assertIsNone(self.post(rf.no_match())["radar"]["timeline_url"])


if __name__ == "__main__":
    unittest.main()
