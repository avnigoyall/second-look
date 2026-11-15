"""Spend ledger: pricing from config, warn at 80 percent, refuse at 100 percent, unpriced models refused,
cross-process lock, corrupt ledger blocks. No network, no paid calls."""
import contextlib
import io
import json
import multiprocessing
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import radar_spend
from radar_spend import BudgetExceeded, Ledger, LedgerError, UnpricedModel


def _record_many(path, n):
    led = Ledger(path=Path(path), vultr_embed_ledger=None)
    for _ in range(n):
        led.check_and_record("claude", "claude-haiku-5-5", 1000, 100)


class PricesTest(unittest.TestCase):
    def test_price_file_has_sources_and_dates(self):
        prices = radar_spend.load_prices()
        for provider in ("claude", "vultr"):
            p = prices["providers"][provider]
            self.assertTrue(p["source_url"].startswith("https://"))
            self.assertRegex(p["fetched_at"], r"^\d{4}-\d{2}-\d{2}$")

    def test_haiku_tiers(self):
        # 1M input + 1M output under the 100k prompt tier would be 0.10 + 0.50; use a small call instead.
        self.assertAlmostEqual(radar_spend.estimate_cost("claude", "claude-haiku-5-5", 10_000, 1_000),
                               10_000 * 0.10 / 1e6 + 1_000 * 0.50 / 1e6)
        self.assertAlmostEqual(radar_spend.estimate_cost("claude", "claude-haiku-5-5", 150_000, 1_000),
                               150_000 * 0.50 / 1e6 + 1_000 * 2.50 / 1e6)
        # Cache tokens count toward the prompt tier.
        cost = radar_spend.estimate_cost("claude", "claude-haiku-5-5", 1_000, 0, cache_read_tokens=120_000)
        self.assertAlmostEqual(cost, 1_000 * 0.50 / 1e6 + 120_000 * 0.05 / 1e6)

    def test_unpriced_model_refused(self):
        with self.assertRaises(UnpricedModel):
            radar_spend.estimate_cost("claude", "claude-made-up-9", 10, 10)
        with self.assertRaises(UnpricedModel):  # Vultr embedding has no cache price
            radar_spend.estimate_cost("vultr", "qwen3-embedding-4b", 10, 0, cache_read_tokens=5)

    def test_usage_from_langchain_removes_cache_double_count(self):
        u = radar_spend.usage_from_langchain({"input_tokens": 1500, "output_tokens": 20,
                                              "input_token_details": {"cache_read": 1000, "cache_creation": 300}})
        self.assertEqual(u, {"input_tokens": 200, "output_tokens": 20, "cache_write_5m_tokens": 300,
                             "cache_write_1h_tokens": 0, "cache_read_tokens": 1000})

    def test_usage_from_anthropic_dict(self):
        u = radar_spend.usage_from_anthropic({"input_tokens": 5, "output_tokens": 7, "cache_read_input_tokens": 3,
                                              "cache_creation_input_tokens": 2})
        self.assertEqual((u["input_tokens"], u["cache_write_5m_tokens"], u["cache_read_tokens"]), (5, 2, 3))


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.caps = d / "caps.json"
        self.caps.write_text(json.dumps({"warn_fraction": 0.8,
                                         "providers": {"claude": {"cap_usd": 1.0}, "vultr": {"cap_usd": 1.0}}}))
        self.path = d / "spend_ledger.json"
        self.led = Ledger(path=self.path, caps_path=self.caps, vultr_embed_ledger=None)

    def tearDown(self):
        self.tmp.cleanup()

    def test_records_running_total(self):
        e1 = self.led.check_and_record("claude", "claude-opus-5-5", 10_000, 1_000, run_id="r1", style="sambas")
        e2 = self.led.check_and_record("claude", "claude-opus-5-5", 10_000, 1_000, run_id="r1", style="sambas")
        self.assertAlmostEqual(e1["estimated_cost_usd"], 0.04 + 0.02)
        self.assertAlmostEqual(e2["running_total_usd"], 0.12)
        data = json.loads(self.path.read_text())
        self.assertEqual(len(data["entries"]), 2)
        self.assertEqual(data["entries"][0]["model"], "claude-opus-5-5")

    def test_warn_at_80_and_refuse_at_100(self):
        # 0.85 USD: 42,500 opus output tokens at 20 USD/MTok.
        with self.assertLogs("radar_spend", level="WARNING"):
            entry = self.led.check_and_record("claude", "claude-opus-5-5", 0, 42_500)
        self.assertTrue(entry["warn"])
        self.assertFalse(entry["exceeded"])
        with self.assertLogs("radar_spend", level="WARNING"):
            self.assertTrue(self.led.check_budget("claude")["warn"])
        with self.assertRaises(BudgetExceeded):  # coming call estimate would cross the cap
            self.led.check_budget("claude", estimated_cost_usd=0.2, run_id="r9")
        with self.assertLogs("radar_spend", level="WARNING"):
            self.led.check_and_record("claude", "claude-opus-5-5", 0, 10_000)  # recorded even past the cap
        with self.assertRaises(BudgetExceeded) as ctx:
            self.led.check_budget("claude", run_id="r9")
        self.assertIn("--run-id r9", str(ctx.exception))
        self.assertIn("r9", ctx.exception.resume)
        # Other providers are independent.
        self.assertFalse(self.led.check_budget("vultr")["warn"])

    def test_external_vultr_ledger_counts(self):
        ext = Path(self.tmp.name) / "vultr_usage.json"
        ext.write_text(json.dumps({"tokens": 400_000, "price_per_mtok": 2.75}))  # 1.10 USD
        led = Ledger(path=self.path, caps_path=self.caps, vultr_embed_ledger=ext)
        with self.assertRaises(BudgetExceeded):
            led.check_budget("vultr")

    def test_unknown_provider_refused(self):
        with self.assertRaises(radar_spend.SpendError):
            self.led.check_budget("openai")

    def test_corrupt_ledger_blocks(self):
        self.path.write_text("{not json")
        with self.assertRaises(LedgerError):
            self.led.check_budget("claude")
        with self.assertRaises(LedgerError):
            self.led.check_and_record("claude", "claude-haiku-5-5", 1, 1)
        self.assertEqual(self.path.read_text(), "{not json")  # never reset

    def test_concurrent_writers_lose_nothing(self):
        ctx = multiprocessing.get_context("spawn")
        procs = [ctx.Process(target=_record_many, args=(str(self.path), 25)) for _ in range(4)]
        for p in procs:
            p.start()
        for p in procs:
            p.join(60)
        data = json.loads(self.path.read_text())
        self.assertEqual(len(data["entries"]), 100)
        self.assertAlmostEqual(data["totals_usd"]["claude"], 100 * (1000 * 0.10 + 100 * 0.50) / 1e6)

    def test_stop_cleanly_exits_with_resume(self):
        exc = BudgetExceeded("claude", 1.0, 1.0, "resume-cmd")
        err = io.StringIO()
        with self.assertRaises(SystemExit) as ctx, contextlib.redirect_stderr(err):
            radar_spend.stop_cleanly(exc)
        self.assertIn("Resume: resume-cmd", err.getvalue())
        self.assertEqual(ctx.exception.code, 3)


if __name__ == "__main__":
    unittest.main()
