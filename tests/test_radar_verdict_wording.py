"""Verdict wording for Bluesky sample lifespans (PART F). Claude is mocked; no network, no paid call."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_verdict import SHORT_TREND, EngineTest, reply

from backend.services import radar_read
from backend.services import verdict as v

SAMPLE = {"term": "barrel jeans", "final_label": "micro_trend", "decision_source": "agent",
          "weeks_to_rise": 3, "weeks_active": 9, "source": "Bluesky sample"}
GOOD = "These fit the barrel-leg jeans trend. In a Bluesky sample, posts rose in 3 weeks and stayed active 9 weeks."


def context():
    return {"term": "barrel jeans", "similarity": .8, "weekly_counts": [], "momentum": "fading",
            "confidence": "medium", "examples": [], "estimate": True}


def metrics(**changes):
    base = {"term": "barrel jeans", "rule_label": "micro_trend", "manual": False, "reason_codes": ["tests_1_3_pass"],
            "peak_censored": False, "peak_week": "2026-03-02", "test1": {"result": "pass", "ramp_weeks": 3},
            "active_run": {"start_week": "2026-02-09", "end_week": "2026-04-06", "weeks_active": 9,
                           "touches_left": False, "touches_right": False}}
    return {**base, **changes}


def guard(reason, *, sample=SAMPLE, trends=(SHORT_TREND,), verdict="micro_trend"):
    trend = SHORT_TREND["name"] if verdict == "micro_trend" else None
    allowed = v.allowed_verdicts("clear", [t for t in trends if t.get("lifespan_bucket") == "short"])
    return v.apply_guardrails(reply(verdict, reason=reason, trend=trend), allowed=allowed, matches=[], best=None,
                              short_trends=[t for t in trends if t.get("lifespan_bucket") == "short"],
                              zone="clear", closet_small=False, sample=sample)


class PromptTextTests(unittest.TestCase):
    def test_prompt_allows_sample_attributed_lifespan_only(self):
        p = v.SYSTEM_PROMPT
        self.assertNotIn("Do not infer a trend's lifespan from sampled chatter alone", p)
        self.assertIn("only as Bluesky sample evidence", p)
        self.assertIn('"in a Bluesky sample"', p)
        self.assertIn("Never present it as market-wide fact", p)
        self.assertIn('"all shoppers"', p)
        self.assertIn("Choose only from allowed_verdicts", p)


class GuardrailTests(unittest.TestCase):
    def test_attributed_sample_numbers_stand(self):
        out = guard(GOOD)
        self.assertEqual(out["verdict"], "micro_trend")
        self.assertEqual(out["reason"], GOOD)
        self.assertEqual(out["overrides"], [])

    def test_unattributed_sample_numbers_are_replaced_by_template(self):
        out = guard("These barrel-leg jeans rose in 3 weeks and stayed active 9 weeks.")
        self.assertEqual([o["rule"] for o in out["overrides"]], ["sample_not_attributed"])
        self.assertEqual(out["verdict"], "micro_trend")
        self.assertIn("In a Bluesky sample, posts rose in 3 weeks and stayed active 9 weeks.", out["reason"])
        self.assertLessEqual(v.word_count(out["reason"]), v.REASON_MAX_WORDS)

    def test_market_wide_claims_are_replaced(self):
        for claim in ("Everyone is wearing barrel-leg jeans in a Bluesky sample.",
                      "All shoppers will drop barrel-leg jeans after 9 weeks in a Bluesky sample.",
                      "Barrel-leg jeans are fading nationwide.", "This is a market-wide fad."):
            with self.subTest(claim=claim):
                out = guard(claim)
                self.assertEqual(out["overrides"][0]["rule"], "market_wide_claim")
                self.assertNotRegex(v.normalize(out["reason"]), r"everyone|all shoppers|nationwide|market-wide")
                self.assertIn("Bluesky sample", out["reason"])

    def test_invented_week_numbers_are_replaced(self):
        out = guard("In a Bluesky sample, barrel-leg jeans lasted 5 weeks.")
        self.assertEqual(out["overrides"][0]["rule"], "unsourced_number")
        out = guard("Barrel-leg jeans last about six weeks.", sample=None)
        self.assertEqual(out["overrides"][0]["rule"], "unsourced_number")
        self.assertNotIn("Bluesky", out["reason"])  # no stored sample: the plain hedged template

    def test_number_from_listed_lifespan_label_still_allowed(self):
        trend = {**SHORT_TREND, "lifespan_label": "Vogue gives it 12 weeks"}
        out = guard("Vogue gives barrel-leg jeans 12 weeks.", sample=None, trends=(trend,))
        self.assertEqual(out["overrides"], [])

    def test_micro_trend_only_with_a_short_trend(self):
        out = guard(GOOD, trends=())
        self.assertEqual(out["verdict"], "good_buy")
        self.assertEqual(out["overrides"][0]["rule"], "verdict_not_allowed")
        self.assertNotIn("Bluesky sample", out["reason"])  # sample numbers are for micro_trend only

    def test_sample_numbers_not_allowed_on_other_verdicts(self):
        out = guard("In a Bluesky sample, posts stayed active 9 weeks.", verdict="good_buy")
        self.assertEqual(out["overrides"][0]["rule"], "unsourced_number")

    def test_template_wording(self):
        text = v.template_reason("micro_trend", trend_name="Barrel-leg jeans", sample=SAMPLE)
        self.assertEqual(text, "This fits the Barrel-leg jeans trend. In a Bluesky sample, posts rose in 3 weeks "
                               "and stayed active 9 weeks. A classic version may get more wear.")
        one = v.sample_sentence({"weeks_to_rise": None, "weeks_active": 1})
        self.assertEqual(one, "In a Bluesky sample, posts stayed active 1 week.")


class SampleLifespanTests(unittest.TestCase):
    def test_short_micro_trend_gives_numbers(self):
        out = radar_read.sample_lifespan(metrics(), {"final_label": "micro_trend", "decision_source": "agent"})
        self.assertEqual((out["weeks_to_rise"], out["weeks_active"], out["source"]), (3, 9, "Bluesky sample"))

    def test_no_numbers_for_staple_long_run_manual_or_edge(self):
        cases = [
            (metrics(rule_label="established"), None),
            (metrics(), {"final_label": "established", "decision_source": "agent"}),
            (metrics(active_run={**metrics()["active_run"], "weeks_active": 20}), None),
            (metrics(manual=True), None),
            (metrics(), {"final_label": "micro_trend", "decision_source": "manual"}),
            (metrics(active_run={**metrics()["active_run"], "touches_left": True}), None),
            (metrics(peak_censored=True), None),
        ]
        for m, decision in cases:
            with self.subTest(m=m, decision=decision):
                self.assertIsNone(radar_read.sample_lifespan(m, decision))

    def test_rule_label_used_when_no_decision(self):
        self.assertEqual(radar_read.sample_lifespan(metrics(), None)["decision_source"], "rule")


class EngineWordingTests(EngineTest):
    def run_with(self, sample):
        with mock.patch.object(v, "social_context", return_value=context()), \
                mock.patch.object(v.radar_read, "sample_lifespan_for_term", return_value=sample) as lookup:
            response, record = self.run_engine()
        return response, record, lookup

    def test_short_trend_micro_trend_cites_sample_with_attribution(self):
        self.trends = [SHORT_TREND]
        self.replies = [reply("micro_trend", trend=SHORT_TREND["name"], reason=GOOD)]
        response, record, _ = self.run_with(SAMPLE)
        self.assertEqual(response.verdict, "micro_trend")
        self.assertIn("Bluesky sample", response.reason)
        self.assertEqual(self.calls[0]["payload"]["social_sample_lifespan"], SAMPLE)
        self.assertEqual(record["signals"]["social_sample_lifespan"], SAMPLE)

    def test_market_claim_from_claude_is_overridden_and_logged(self):
        self.trends = [SHORT_TREND]
        self.replies = [reply("micro_trend", trend=SHORT_TREND["name"], reason="Everyone wore barrel-leg jeans for 9 weeks.")]
        response, record, _ = self.run_with(SAMPLE)
        self.assertEqual(response.verdict, "micro_trend")
        self.assertIn("In a Bluesky sample", response.reason)
        self.assertNotIn("veryone", response.reason)
        self.assertEqual(record["overrides"][0]["rule"], "market_wide_claim")

    def test_fallback_template_is_sample_attributed(self):
        self.trends = [SHORT_TREND]
        self.attrs = {**self.attrs, "cut": "wide", "fabric": "denim"}
        self.replies = [RuntimeError("down"), RuntimeError("down")]
        response, _, _ = self.run_with(SAMPLE)
        self.assertEqual(response.verdict, "micro_trend")
        self.assertIn("In a Bluesky sample, posts rose in 3 weeks and stayed active 9 weeks.", response.reason)

    def test_staple_without_short_trend_never_fetches_sample(self):
        self.replies = [reply("micro_trend", trend="barrel jeans", reason=GOOD)]
        response, record, lookup = self.run_with(SAMPLE)
        lookup.assert_not_called()
        self.assertEqual(record["signals"]["allowed"], ["good_buy"])
        self.assertEqual(response.verdict, "good_buy")
        self.assertNotIn("social_sample_lifespan", self.calls[0]["payload"])
        self.assertNotIn("Bluesky sample", response.reason)


if __name__ == "__main__":
    unittest.main()
