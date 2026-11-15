"""Tests for scripts/radar_agent.py. No paid calls: a scripted fake chat model drives the real deep agent."""
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import radar_agent as ra
import radar_spend
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

SKILL_NAMES = ("style-judge", "precision-auditor", "disagreement-analyst", "analyst-note-writer")


class ScriptedModel(BaseChatModel):
    """Returns pre-built AIMessages in order (each with usage_metadata), then a plain final answer.

    A script item may be a callable taking the messages so far, to cite ids returned by earlier tools.
    """

    script: list = Field(default_factory=list)
    idx: int = 0
    seen_system: list = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if messages and messages[0].type == "system":
            self.seen_system.append(str(messages[0].content))
        item = self.script[self.idx] if self.idx < len(self.script) else AIMessage(content="done")
        self.idx += 1
        msg = item(messages) if callable(item) else item
        msg = msg.model_copy(update={"usage_metadata": {"input_tokens": 1000, "output_tokens": 100,
                                                        "total_tokens": 1100}})
        return ChatResult(generations=[ChatGeneration(message=msg)])


def call(name, args, cid):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": cid}])


def last_evidence(messages):
    for m in reversed(messages):
        if isinstance(m, ToolMessage):
            try:
                ev = json.loads(m.content).get("evidence_id")
            except (TypeError, ValueError):
                continue
            if ev:
                return ev
    return None


def fake_tools_module():
    """Stand-in for radar_agent_tools with the same tool names and write_decision contract."""
    state = {"issued": set(), "decisions": []}

    def get_style_stats(style: str) -> dict:
        """Volume, weeks and peak for a style."""
        ev = f"ev:get_style_stats:{style}:01"
        state["issued"].add(ev)
        return {"ok": True, "evidence_id": ev, "posts": 500}

    def sample_posts(style: str, n: int = 8) -> dict:
        """Sample posts for a style."""
        ev = f"ev:sample_posts:{style}:02"
        state["issued"].add(ev)
        return {"ok": True, "evidence_id": ev, "posts": ["a", "b"]}

    def write_decision(style: str, agent_label: str, confidence: str, reasons: list[str], evidence_ids: list[str],
                       lifespan_source_url: str | None = None, model_id: str | None = None,
                       prompt_version: str | None = None) -> dict:
        """Store the agent decision."""
        if not evidence_ids or any(e not in state["issued"] for e in evidence_ids):
            return {"ok": False, "error": "evidence_ids not issued", "evidence_id": None}
        state["decisions"].append({"style": style, "model_id": model_id, "prompt_version": prompt_version})
        return {"ok": True, "evidence_id": "ev:write_decision:1", "agent_label": agent_label,
                "confidence": confidence, "reasons": reasons, "evidence_ids": evidence_ids,
                "rule_label": "established", "decision_source": "agent"}

    mod = types.SimpleNamespace(TOOLS=[get_style_stats, sample_posts, write_decision], write_decision=write_decision,
                                start_run=lambda *a, **k: None, write_manual_decisions=lambda s: [], state=state)
    return mod


RULE = {"rule_label": "established", "metric_version": "micro_trend_rules_v1", "config_hash": "abc",
        "confidence": "medium", "reasons": ["rule reason"]}


class TmpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        caps = self.dir / "caps.json"
        caps.write_text(json.dumps({"warn_fraction": 0.8, "providers": {"claude": {"cap_usd": 5}}}))
        self.book = radar_spend.Ledger(path=self.dir / "ledger.json", caps_path=caps,
                                       prices_path=ROOT / "config" / "radar_prices.json", vultr_embed_ledger=None)
        self.ledger = ra.Ledger(radar_spend, "claude", "claude-sonnet-5-5", "run1", book=self.book)
        self.store = ra.Store(None, self.dir / "decisions.jsonl")

    def tearDown(self):
        self.tmp.cleanup()


class SkillFilesTest(unittest.TestCase):
    def test_four_skills_with_frontmatter(self):
        found = {s["dir"]: s for s in ra.skill_summary()}
        self.assertEqual(set(found), set(SKILL_NAMES))
        for name, s in found.items():
            self.assertEqual(s["name"], name)
            self.assertTrue(s["description"])

    def test_skills_reference_config_and_copy_no_thresholds(self):
        cfg = json.loads((ROOT / "config" / "social_micro_trend.json").read_text())
        numbers = {str(v) for v in cfg.values() if isinstance(v, int | float) and not isinstance(v, bool) and v > 12}
        for name in SKILL_NAMES:
            text = (ROOT / "radar_skills" / name / "SKILL.md").read_text()
            self.assertNotIn(chr(0x2014), text, f"{name} has an em dash")
            self.assertIn("Bluesky", text)
            for n in numbers:
                self.assertNotRegex(text, rf"\b{n}\b", f"{name} copies threshold {n}")
        judge = (ROOT / "radar_skills" / "style-judge" / "SKILL.md").read_text()
        for word in ("config/social_micro_trend.json", "config/manual_labels.json", "leg warmers", "capri pants",
                     "crochet top", "heels", "balloon", "barrel", "Saturation", "unknown"):
            self.assertIn(word, judge)


class ToolBudgetTest(unittest.TestCase):
    def test_last_slot_reserved_for_final_write(self):
        b = ra.ToolBudget(limit=12)
        for _ in range(11):
            self.assertIsNone(b.take("get_style_stats"))
        refusal = json.loads(b.take("sample_posts"))
        self.assertFalse(refusal["ok"])
        self.assertIsNone(b.take("write_decision"))
        self.assertIsNotNone(b.take("write_decision"))
        self.assertEqual(b.used, 12)

    def test_counted_tool_keeps_schema_and_counts(self):
        b = ra.ToolBudget(limit=3)
        mod = fake_tools_module()
        t = ra.counted_tool(mod.TOOLS[0], b)
        self.assertEqual(t.name, "get_style_stats")
        self.assertIn("style", t.args)
        self.assertEqual(t.invoke({"style": "x"})["evidence_id"], "ev:get_style_stats:x:01")
        t.invoke({"style": "x"})
        self.assertIn("tool budget", t.invoke({"style": "x"}))

    def test_bound_write_decision_hides_model_and_prompt_version(self):
        mod = fake_tools_module()
        fns = ra.agent_tool_functions(mod, "anthropic:test-model")
        t = ra.counted_tool(fns[-1], ra.ToolBudget())
        self.assertNotIn("model_id", t.args)
        self.assertNotIn("prompt_version", t.args)
        mod.state["issued"].add("e1")
        t.invoke({"style": "s", "agent_label": "unknown", "confidence": "low", "reasons": ["r"], "evidence_ids": ["e1"]})
        self.assertEqual(mod.state["decisions"][-1]["model_id"], "anthropic:test-model")
        self.assertEqual(mod.state["decisions"][-1]["prompt_version"], ra.PROMPT_VERSION)


class RecordTest(unittest.TestCase):
    def test_agent_record(self):
        rec = ra.build_record(style="s", run_id="r", model_id="m", rule=RULE,
                              agent={"agent_label": "micro_trend", "confidence": "low", "reasons": ["x"],
                                     "evidence_ids": ["e"]})
        self.assertEqual((rec["decision_source"], rec["final_label"], rec["agree"]), ("agent", "micro_trend", False))
        self.assertEqual(rec["prompt_version"], ra.PROMPT_VERSION)

    def test_rule_fallback_record(self):
        rec = ra.build_record(style="s", run_id="r", model_id="m", rule=RULE, agent=None, error="boom")
        self.assertEqual((rec["decision_source"], rec["final_label"], rec["agree"]), ("rule", "established", None))

    def test_agree_only_when_both_labels_and_not_manual(self):
        self.assertTrue(ra.agree_value("unknown", "unknown", manual=False))
        self.assertIsNone(ra.agree_value(None, "unknown", manual=False))
        self.assertIsNone(ra.agree_value("established", "established", manual=True))

    def test_extract_decision_ignores_refused_calls(self):
        msgs = [call("write_decision", {"agent_label": "micro_trend"}, "c1"),
                ToolMessage(content=json.dumps({"ok": False, "error": "thin"}), tool_call_id="c1"),
                call("write_decision", {"agent_label": "unknown"}, "c2"),
                ToolMessage(content=json.dumps({"ok": True, "agent_label": "unknown", "evidence_id": "e"}),
                            tool_call_id="c2")]
        self.assertEqual(ra.extract_decision(msgs)["agent_label"], "unknown")
        self.assertIsNone(ra.extract_decision(msgs[:2]))

    def test_split_model_id(self):
        self.assertEqual(ra.split_model_id("anthropic:claude-sonnet-5-5"), ("claude", "claude-sonnet-5-5"))
        self.assertEqual(ra.split_model_id("claude-haiku-5-5"), ("claude", "claude-haiku-5-5"))


class StoreAndResumeTest(TmpCase):
    def test_resume_matches_versions(self):
        rec = ra.build_record(style="s", run_id="r1", model_id="m", rule=RULE,
                              agent={"agent_label": "unknown", "confidence": "low"})
        self.assertEqual(self.store.save(rec), "jsonl_fallback")
        self.assertTrue(ra.already_decided(self.store, "s", "micro_trend_rules_v1", "m"))
        self.assertFalse(ra.already_decided(self.store, "s", "micro_trend_rules_v2", "m"))
        self.assertFalse(ra.already_decided(self.store, "s", "micro_trend_rules_v1", "other-model"))

    def test_rule_fallback_is_not_resumed(self):
        self.store.save(ra.build_record(style="s", run_id="r1", model_id="m", rule=RULE, agent=None))
        self.assertFalse(ra.already_decided(self.store, "s", "micro_trend_rules_v1", "m"))


class ProjectionTest(unittest.TestCase):
    def test_projection_gate_after_pilot(self):
        st = ra.RunState(run_id="r", model_id="m", measured=[{"cost_usd": 0.5}] * 2)
        self.assertFalse(ra.projection_exceeds(st, 40, budget_usd=10, pilot=3))
        st.measured.append({"cost_usd": 0.5})
        self.assertTrue(ra.projection_exceeds(st, 40, budget_usd=10, pilot=3))   # 20 > 10 - 1.5
        self.assertFalse(ra.projection_exceeds(st, 10, budget_usd=10, pilot=3))  # 5 <= 8.5
        self.assertFalse(ra.projection_exceeds(st, 40, budget_usd=None, pilot=3))

    def test_resume_command(self):
        args = ra.parse_args(["--all", "--cap-usd", "20"])
        self.assertEqual(ra.resume_command(args, "R"), ".venv/bin/python scripts/radar_agent.py --all --run-id R --cap-usd 20.0")
        args = ra.parse_args(["--resume", "--run-id", "R"])
        self.assertTrue(args.all)


class DeepAgentEndToEndTest(TmpCase):
    """The real create_deep_agent graph with a scripted model and stand-in tools."""

    def run_style(self, script, ledger=None):
        mod = fake_tools_module()
        budget = ra.ToolBudget()
        model = ScriptedModel(script=script, idx=0, seen_system=[])
        agent = ra.build_agent(model, [ra.counted_tool(f, budget) for f in ra.agent_tool_functions(mod, "m")])
        state = ra.RunState(run_id="run1", model_id="m")
        cb = ra.LedgerCallback(ledger or self.ledger)
        rec = ra.decide_style(agent, "leg warmers", state=state, rule=RULE, budget=budget, callback=cb, store=self.store)
        return rec, model, state

    def test_decision_is_stored_with_measured_cost(self):
        script = [
            call("read_file", {"file_path": "/radar_skills/style-judge/SKILL.md"}, "c0"),
            call("get_style_stats", {"style": "leg warmers"}, "c1"),
            lambda msgs: call("write_decision", {"style": "leg warmers", "agent_label": "micro_trend",
                                                 "confidence": "medium", "reasons": ["rose and fell"],
                                                 "evidence_ids": [last_evidence(msgs)]}, "c2"),
            AIMessage(content="stored"),
        ]
        rec, model, _ = self.run_style(script)
        self.assertEqual((rec["decision_source"], rec["agent_label"], rec["final_label"]),
                         ("agent", "micro_trend", "micro_trend"))
        self.assertIs(rec["agree"], False)
        self.assertEqual(rec["evidence_ids"], ["ev:get_style_stats:leg warmers:01"])
        self.assertEqual(rec["tool_calls"], ["get_style_stats", "write_decision"])
        self.assertEqual(rec["usage"]["model_calls"], 4)
        self.assertEqual(rec["usage"]["input_tokens"], 4000)
        expected = 4 * radar_spend.estimate_cost("claude", "claude-sonnet-5-5", 1000, 100)
        self.assertAlmostEqual(rec["usage"]["cost_usd"], expected, places=6)
        ledger_data = json.loads((self.dir / "ledger.json").read_text())
        self.assertEqual(len(ledger_data["entries"]), 4)
        self.assertEqual(ledger_data["entries"][0]["style"], "leg warmers")
        # skills reached the prompt through the FilesystemBackend rooted at the repo
        self.assertTrue(any("style-judge" in s and "analyst-note-writer" in s for s in model.seen_system))
        saved = self.store.find({"style": "leg warmers", "run_id": "run1"})
        self.assertEqual(saved[0]["decision_source"], "agent")

    def test_agent_cannot_read_secrets_or_write_files(self):
        script = [
            call("read_file", {"file_path": "/.env"}, "c0"),
            call("write_file", {"file_path": "/config/social_micro_trend.json", "content": "{}"}, "c1"),
            AIMessage(content="gave up"),
        ]
        before = (ROOT / "config" / "social_micro_trend.json").read_text()
        rec, _, _ = self.run_style(script)
        self.assertEqual((ROOT / "config" / "social_micro_trend.json").read_text(), before)
        self.assertEqual((rec["decision_source"], rec["final_label"]), ("rule", "established"))
        self.assertIn("without a stored write_decision", rec["agent_error"])

    def test_budget_stop_raises_before_any_model_call(self):
        self.book.check_and_record("claude", "claude-sonnet-5-5", 3_000_000, 0)  # 6 USD > 5 USD cap
        with self.assertRaises(ra.BudgetStop):
            self.run_style([AIMessage(content="never")])
        self.assertEqual(self.store.find({"style": "leg warmers"}), [])

    def test_missing_ledger_refuses(self):
        with self.assertRaises(ra.BudgetStop):
            self.run_style([AIMessage(content="never")], ledger=ra.Ledger(None, "claude", "x", "r"))


class DisagreementWriterTest(TmpCase):
    def setUp(self):
        super().setUp()
        self.budget = ra.ToolBudget()
        self.dstore = ra.Store(None, self.dir / "dis.jsonl")
        self.w = ra.DisagreementWriter(self.dstore, "run1", "m", self.budget)
        self.w.style = "capri pants"
        self.w.decision = {"run_id": "run0", "rule_label": "established", "agent_label": "micro_trend",
                           "evidence_ids": ["e0"]}
        self.hyp = [{"statement": "Seasonal peak read as one wave", "evidence_ids": ["e0"],
                     "tool_check": "get_weekly_series: peaks in two summers", "confidence": "medium"}]

    def test_requires_a_tool_check(self):
        out = self.w.write(self.hyp, "medium", "rule")
        self.assertFalse(out["ok"])
        self.assertIn("data tool check", out["error"])

    def test_stores_ranked_hypotheses(self):
        self.budget.take("get_weekly_series")
        out = self.w.write(self.hyp, "medium", "rule")
        self.assertTrue(out["ok"])
        doc = self.dstore.find({"style": "capri pants"})[0]
        self.assertEqual(doc["hypotheses"][0]["rank"], 1)
        self.assertTrue(doc["hypotheses"][0]["is_hypothesis"])
        self.assertEqual(doc["tool_checks_run"], ["get_weekly_series"])

    def test_rejects_bad_fields(self):
        self.budget.take("sample_posts")
        bad = [{"statement": "", "evidence_ids": [], "tool_check": "", "confidence": "sure"}]
        err = self.w.write(bad, "certain", "neither")["error"]
        for part in ("no statement", "no evidence_ids", "confidence must be", "tool_check", "favors"):
            self.assertIn(part, err)

    def test_latest_disagreements_picks_agent_disagreements(self):
        store = ra.Store(None, self.dir / "d.jsonl")
        store.save(ra.build_record(style="a", run_id="r1", model_id="m", rule=RULE,
                                   agent={"agent_label": "micro_trend"}))
        store.save(ra.build_record(style="b", run_id="r1", model_id="m", rule=RULE,
                                   agent={"agent_label": "established"}))
        store.save(ra.build_record(style="c", run_id="r1", model_id="m", rule=RULE, agent=None))
        self.assertEqual(set(ra.latest_disagreements(store, "r1", None)), {"a"})


if __name__ == "__main__":
    unittest.main()
