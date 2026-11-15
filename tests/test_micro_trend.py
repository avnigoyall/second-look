"""Rule baseline tests on synthetic social_trends docs. No network, no database."""
import copy
import unittest
from datetime import date, timedelta

from backend.services import micro_trend as mt

N = 40  # metric weeks; two partial edge windows are added around them


def doc(counts, *, term="test style", corpus=1000, saturated=(), post_count=None, wtb3=2, conc=0.3,
        edge_counts=(0, 0)):
    start = date(2025, 11, 10)
    weekly = []
    full = [edge_counts[0], *counts, edge_counts[1]]
    for i, c in enumerate(full):
        weekly.append({"week": (start + timedelta(weeks=i)).isoformat(), "count": c, "corpus_count": corpus,
                       "normalized": c / corpus, "saturated": (i - 1) in saturated,
                       "complete": 0 < i < len(full) - 1})
    return {"term": term, "weekly_counts": weekly, "observed_weeks": 0,
            "post_count": sum(full) if post_count is None else post_count,
            "weeks_to_breadth_3": wtb3, "concentration": conc}


def series(at, values, n=N):
    out = [0] * n
    out[at:at + len(values)] = values
    return out


SPIKE = [2, 10, 60, 80, 60, 20, 6, 2, 1]


def micro(**kw):
    return doc(series(15, SPIKE), **kw)


class MetricWeeksTests(unittest.TestCase):
    def test_metric_weeks_exclude_partial_edges_and_ignore_observed_weeks(self):
        d = doc(series(0, [1] * 5), edge_counts=(7, 9))
        weeks = mt.metric_weeks(d)
        self.assertEqual(len(weeks), N)
        self.assertTrue(all(w["complete"] for w in weeks))
        self.assertEqual(mt.weeks_with_posts(d), 5)
        self.assertEqual(mt.compute_rule_metrics(d)["metric_weeks"], N)

    def test_metric_weeks_without_complete_flags_drop_first_and_last(self):
        d = doc([1] * 5)
        for w in d["weekly_counts"]:
            del w["complete"]
        self.assertEqual(len(mt.metric_weeks(d)), 5)

    def test_term_string_is_looked_up(self):
        target = micro(term="cargo pants")

        class Coll:
            def find_one(self, query, projection=None):
                return target if query == {"term": "cargo pants"} else None

        self.assertEqual(mt.compute_rule_metrics("cargo pants", collection=Coll())["rule_label"], "micro_trend")
        with self.assertRaises(LookupError):
            mt.compute_rule_metrics("missing", collection=Coll())


class OutcomeTests(unittest.TestCase):
    def test_micro_trend_passes_tests_1_to_3(self):
        m = mt.compute_rule_metrics(micro())
        self.assertEqual(m["rule_label"], "micro_trend")
        for t in ("test1", "test2", "test3"):
            self.assertEqual(m[t]["result"], "pass", t)
        self.assertEqual(m["tests_1_3"], "micro_trend")
        self.assertEqual(m["tests_1_4"], "micro_trend")
        self.assertLessEqual(m["test1"]["ramp_weeks"], 4)
        self.assertFalse(m["peak_censored"])
        run = mt.measured_active_run(micro())
        self.assertIsNotNone(run)
        self.assertEqual(set(run), {"start_week", "end_week", "weeks_active", "peak_week"})
        self.assertEqual(run["peak_week"], m["peak_week"])

    def test_established_when_present_at_window_start(self):
        m = mt.compute_rule_metrics(doc([10] * N))
        self.assertEqual(m["rule_label"], "established")
        self.assertEqual(m["test3"]["result"], "fail")
        self.assertIn("left_censored_staple", m["reason_codes"])
        self.assertIsNone(mt.measured_active_run(doc([10] * N)))

    def test_still_active_when_right_censored_above_half_peak(self):
        counts = [0] * 30 + [3, 6, 10, 15, 20, 20, 19, 18, 18, 17]
        m = mt.compute_rule_metrics(doc(counts))
        self.assertEqual(m["rule_label"], "still_active")
        self.assertTrue(m["active_run"]["touches_right"])
        self.assertIn("right_censored_active", m["reason_codes"])

    def test_unknown_when_posts_below_min(self):
        m = mt.compute_rule_metrics(doc(series(10, [1, 2, 3, 4, 5, 4, 3, 2, 1])))
        self.assertEqual(m["rule_label"], "unknown")
        self.assertIn("posts_below_min", m["reason_codes"])
        self.assertEqual(m["confidence"], "low")

    def test_unknown_when_weeks_with_posts_below_min(self):
        m = mt.compute_rule_metrics(doc(series(10, [50, 80, 60, 40, 20])))
        self.assertEqual(m["rule_label"], "unknown")
        self.assertEqual(m["weeks_with_posts"], 5)
        self.assertIn("weeks_with_posts_below_min", m["reason_codes"])

    def test_unknown_for_seasonal_category(self):
        m = mt.compute_rule_metrics(micro(term="wool coats"))
        self.assertEqual(m["rule_label"], "unknown")
        self.assertEqual(m["category"], "coats")
        self.assertIn("seasonal_category", m["reason_codes"])
        self.assertEqual(m["test3"]["result"], "unknown")
        # Token match: bootcut is not boots.
        self.assertFalse(mt.compute_rule_metrics(micro(term="bootcut jeans"))["seasonal"])

    def test_slow_rise_fails_test1(self):
        ramp = [1, 2, 3, 4, 6, 8, 10, 13, 16, 20, 25, 30, 6, 1]
        m = mt.compute_rule_metrics(doc(series(10, ramp)))
        self.assertEqual(m["test1"]["result"], "fail")
        self.assertGreater(m["test1"]["ramp_weeks"], 4)
        self.assertEqual(m["rule_label"], "unknown")
        self.assertIn("test1_fail", m["reason_codes"])

    def test_long_life_fails_test2(self):
        m = mt.compute_rule_metrics(doc(series(10, [10, 40] + [40] * 20 + [5])))
        self.assertEqual(m["test2"]["result"], "fail")
        self.assertEqual(m["rule_label"], "unknown")
        self.assertIn("test2_fail", m["reason_codes"])

    def test_fall_with_rebound_does_not_pass_fall_path(self):
        shape = [5, 60, 80, 60, 10, 5, 5, 40, 80, 40, 10, 5, 5, 40, 80, 40, 10, 2]
        m = mt.compute_rule_metrics(doc(series(8, shape)))
        self.assertEqual(m["test2"]["path_fall"], "fail")

    def test_run_touching_left_edge_is_unresolved(self):
        counts = series(10, SPIKE)
        counts[:10] = [8] * 10  # low chatter (>= 10 percent of peak) from the window start
        m = mt.compute_rule_metrics(doc(counts))
        self.assertTrue(m["active_run"]["touches_left"])
        self.assertEqual(m["test1"]["result"], "unknown")
        self.assertEqual(m["rule_label"], "unknown")
        self.assertIn("test1_edge_unresolved", m["reason_codes"])
        self.assertIsNone(mt.measured_active_run(doc(counts)))


class SaturationTests(unittest.TestCase):
    def test_saturated_peak_is_censored_not_absence(self):
        base = micro()
        peak_index = [w["week"] for w in mt.metric_weeks(base)].index(mt.compute_rule_metrics(base)["peak_week"])
        d = micro(saturated={peak_index})
        m = mt.compute_rule_metrics(d)
        self.assertTrue(m["peak_censored"])
        for t in ("test1", "test2"):
            self.assertEqual(m[t]["result"], "unknown")
            self.assertEqual(m[t]["cause"], "censored at cap")
        self.assertEqual(m["rule_label"], "unknown")
        self.assertIn("test1_censored_at_cap", m["reason_codes"])
        self.assertIsNone(mt.measured_active_run(d))

    def test_saturated_share_caps_confidence_at_medium(self):
        counts = series(10, [5, 30, 60, 80, 60, 30, 20, 15, 12, 10, 9, 8, 8, 7, 6, 5, 4, 3, 2, 1])
        high = doc(counts, post_count=600)
        self.assertEqual(mt.compute_rule_metrics(high)["confidence"], "high")
        capped = doc(counts, post_count=600, saturated=set(range(12)))
        m = mt.compute_rule_metrics(capped)
        self.assertGreater(m["saturated_share"], 0.25)
        self.assertEqual(m["confidence"], "medium")
        self.assertIn("saturated share above cap", m["confidence_caps"])


class IndicatorTests(unittest.TestCase):
    def test_test4_and_tests_1_4_stored_separately(self):
        slow_spread = mt.compute_rule_metrics(micro(wtb3=10))
        self.assertEqual(slow_spread["test4"]["result"], "fail")
        self.assertEqual(slow_spread["tests_1_3"], "micro_trend")
        self.assertEqual(slow_spread["tests_1_4"], "unknown")
        missing = mt.compute_rule_metrics(micro(wtb3=None))
        self.assertEqual(missing["test4"]["result"], "unknown")
        self.assertEqual(missing["test4"]["interaction_only"], "not computed")

    def test_span_75_and_share_first_2_weeks(self):
        m = mt.compute_rule_metrics(doc(series(5, [25, 25, 25, 25])))
        self.assertEqual(m["span_75"], 3)
        self.assertEqual(m["share_first_2_weeks"], 0.5)

    def test_required_keys_version_and_config_hash(self):
        m = mt.compute_rule_metrics(micro())
        for key in ("rule_label", "reasons", "reason_codes", "test1", "test2", "test3", "test4", "tests_1_3",
                    "tests_1_4", "confidence", "metric_weeks", "weeks_with_posts", "peak_week", "peak_censored",
                    "saturated_share", "span_75", "share_first_2_weeks", "metric_version", "config_hash"):
            self.assertIn(key, m)
        cfg = mt.rule_config()
        self.assertEqual(m["metric_version"], cfg["metric_version"])
        self.assertEqual(m["config_hash"], mt.config_hash(cfg))
        changed = copy.deepcopy(cfg)
        changed["test1_max_ramp_weeks"] = 0
        self.assertNotEqual(mt.config_hash(changed), m["config_hash"])
        self.assertEqual(mt.compute_rule_metrics(micro(), config=changed)["test1"]["result"], "fail")


class ManualLabelTests(unittest.TestCase):
    EXPECTED = {
        "plain black tee": ("established", "medium"), "mid rise jeans": ("established", "medium"),
        "mesh flats": ("micro_trend", "low"), "micro bag": ("micro_trend", "low"),
        "balloon jeans": ("micro_trend", "low"), "slim sneakers": ("still_active", "low"),
        "soft suiting": ("established", "low"),
    }

    def test_exactly_seven_entries(self):
        labels = mt.load_manual_labels()
        self.assertEqual({k: (v["final_label"], v["confidence"]) for k, v in labels.items()}, self.EXPECTED)
        self.assertTrue(mt.is_manual("Micro Bag"))
        self.assertTrue(mt.is_manual({"term": "soft suiting"}))
        self.assertFalse(mt.is_manual("cargo pants"))
        self.assertIsNone(mt.manual_decision("cargo pants"))

    def test_manual_never_yields_lifespan_and_is_excluded_from_agreement(self):
        for term in self.EXPECTED:
            decision = mt.manual_decision(term)
            self.assertEqual(decision["decision_source"], "manual")
            self.assertEqual(decision["display"], "Editorial label (team judgment, not Radar data)")
            self.assertTrue(decision["excluded_from_agreement"])
            self.assertTrue(decision["source_text"])
            self.assertFalse({k for k in decision if k.startswith("lifespan")})
            # Even with a clean micro-trend shape, a manual style gets no measured run (no lifespan).
            shaped = micro(term=term)
            m = mt.compute_rule_metrics(shaped)
            self.assertEqual(m["rule_label"], "micro_trend")  # rule_label stays what the rule gives
            self.assertTrue(m["manual"])
            self.assertTrue(m["excluded_from_agreement"])
            self.assertIsNone(mt.measured_active_run(shaped))
            self.assertFalse({k for k in m if k.startswith("lifespan")})


if __name__ == "__main__":
    unittest.main()
