"""Additive social evidence never changes the verdict engine's allowed set."""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_verdict import EngineTest, SHORT_TREND, reply
from backend.services import verdict as v


def context(**changes):
    return {"term": "barrel jeans", "similarity": .8, "first_seen_in_window": "2026-01-01T00:00:00Z",
            "weekly_counts": [], "peak_week": "2026-03-02", "decay_ratio": .2,
            "breadth": 3, "concentration": .4, "momentum": "fading", "confidence": "medium",
            "examples": [], "estimate": True, **changes}


class SocialVerdictTests(EngineTest):
    def test_confident_fading_is_supporting_only_when_micro_already_allowed(self):
        self.trends = [SHORT_TREND]
        self.replies = [reply("micro_trend", trend=SHORT_TREND["name"], reason="Barrel-leg jeans are likely short-lived.")]
        with mock.patch.object(v, "social_context", return_value=context()):
            response, record = self.run_engine()
        self.assertEqual(record["signals"]["allowed"], ["micro_trend", "good_buy"])
        self.assertTrue(self.calls[0]["payload"]["social_supporting_evidence"])
        self.assertEqual(response.social_context.term, "barrel jeans")
        self.assertEqual(response.verdict, "micro_trend")

    def test_social_evidence_does_not_allow_micro_without_a_short_trend(self):
        self.replies = [reply("micro_trend", trend="barrel jeans")]
        with mock.patch.object(v, "social_context", return_value=context()):
            response, record = self.run_engine()
        self.assertEqual(record["signals"]["allowed"], ["good_buy"])
        self.assertFalse(self.calls[0]["payload"]["social_supporting_evidence"])
        self.assertEqual(response.verdict, "good_buy")

    def test_low_confidence_or_rising_momentum_is_not_supporting(self):
        for changes in ({"confidence": "low"}, {"momentum": "rising"}):
            with self.subTest(changes=changes):
                self.trends = [SHORT_TREND]
                self.replies = [reply("good_buy")]
                with mock.patch.object(v, "social_context", return_value=context(**changes)):
                    self.run_engine()
                self.assertFalse(self.calls[-1]["payload"]["social_supporting_evidence"])

    def test_error_and_no_match_leave_payload_and_verdict_unchanged(self):
        outputs = []
        for result in (None, RuntimeError("provider unavailable")):
            self.replies = [reply("good_buy")]
            behavior = {"side_effect": result} if isinstance(result, Exception) else {"return_value": result}
            with mock.patch.object(v, "social_context", **behavior):
                response, record = self.run_engine()
            self.assertIsNone(response.social_context)
            self.assertNotIn("social_context", self.calls[-1]["payload"])
            self.assertNotIn("social_supporting_evidence", self.calls[-1]["payload"])
            outputs.append((response.verdict, response.reason, record["signals"]["allowed"]))
        self.assertEqual(outputs[0], outputs[1])

    def test_malformed_optional_context_is_ignored(self):
        self.replies = [reply("good_buy")]
        with mock.patch.object(v, "social_context", return_value={"term": "bad", "similarity": "nonsense"}):
            response, _ = self.run_engine()
        self.assertIsNone(response.social_context)
        self.assertNotIn("social_context", self.calls[-1]["payload"])

    def test_non_apparel_does_not_fetch_context(self):
        self.attrs = {"status": "ok", "is_apparel": False}
        with mock.patch.object(v, "social_context") as lookup:
            response, _ = self.run_engine()
        lookup.assert_not_called()
        self.assertIsNone(response.social_context)
