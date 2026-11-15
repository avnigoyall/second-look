"""Social Trend Radar deep agent: decides one label per style from Bluesky sample chatter.

Run from the project root:
    .venv/bin/python scripts/radar_agent.py --styles "a,b,c"      # pilot on named styles
    .venv/bin/python scripts/radar_agent.py --random 5            # pilot on random non-manual styles
    .venv/bin/python scripts/radar_agent.py --all                 # every style, stops on projected cap
    .venv/bin/python scripts/radar_agent.py --all --redecide      # ignore earlier decisions
    .venv/bin/python scripts/radar_agent.py --resume --run-id ID  # continue a run (implies --all)
    .venv/bin/python scripts/radar_agent.py --dry-run --random 3  # build the agent, no model call
    .venv/bin/python scripts/radar_agent.py --disagreements [--run-id ID]   # disagreement analyst

Model id comes from RADAR_AGENT_MODEL ("provider:model", for example "anthropic:claude-sonnet-5-5").
Evidence tools come from scripts/radar_agent_tools.py (TOOLS). Spend goes through scripts/radar_spend.py:
check_budget before every model call (main agent and subagents) and check_and_record after it, with tokens
from the response usage_metadata.

Per style in radar_decisions (unique on style + run_id, plus JSONL fallbacks): rule_label, agent_label,
final_label, decision_source ("agent", "rule" or "manual"), agree (only when both labels are non-null and the
style is not manual), confidence, reasons, evidence_ids, metric_version, config_hash, prompt_version,
model_id, usage (tokens and measured cost).

The agent can read only radar_skills/ and config/ and cannot write any file. It never edits config or
trend documents. See docs/radar_agent.md.
"""
from __future__ import annotations

import argparse
import importlib
import inspect
import json
import os
import random
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langchain_core.tools import tool as as_tool
from pymongo.errors import PyMongoError

PROMPT_VERSION = "radar_prompt_v1"
SKILLS_DIR = "/radar_skills/"  # virtual path under the FilesystemBackend root (the repo)
MAX_TOOL_CALLS_PER_STYLE = 12
MAX_MODEL_CALLS_PER_STYLE = 25  # runaway guard on the main agent; money is guarded by the ledger
RECURSION_LIMIT = 80
DEFAULT_PILOT = 3
OUT_DIR = ROOT / "scripts" / "out"
DECISIONS_JSONL = OUT_DIR / "radar_final_decisions.jsonl"
DISAGREEMENTS_JSONL = OUT_DIR / "radar_disagreements.jsonl"
DECISIONS_COLLECTION = "radar_decisions"
DISAGREEMENTS_COLLECTION = "radar_disagreements"
LABELS = ("micro_trend", "established", "still_active", "unknown")
CONFIDENCES = ("low", "medium", "high")
WRITE_DECISION = "write_decision"
WRITE_DISAGREEMENT = "write_disagreement"
FINAL_TOOLS = (WRITE_DECISION, WRITE_DISAGREEMENT)


def now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------- prompts

JUDGE_PROMPT = """You are the Social Trend Radar style judge. You decide, for ONE fashion style at a time,
whether the Bluesky sample chatter shows a micro_trend, established, still_active, or unknown.

Workflow:
1. Read the style-judge skill (/radar_skills/style-judge/SKILL.md) before your first data tool call, and the
   analyst-note-writer skill before writing reasons. Read precision-auditor when you sample posts.
2. Use the data tools. Every tool result carries an evidence_id. Cite only evidence_ids you received.
3. Finish by calling write_decision exactly once with agent_label, confidence, reasons and evidence_ids.

Rails (enforced in code too):
- At most 12 data tool calls for this style, write_decision included. Plan; skip tools that cannot change
  the outcome.
- Thresholds live in /config/social_micro_trend.json. Never invent numbers. Lifespan is computed by tools only.
- Describe results only as Bluesky sample chatter, never as the market.
- You cannot and must not edit any file.
"""

DISAGREEMENT_PROMPT = """You are the Social Trend Radar disagreement analyst. For ONE style, the rule label and the
agent label differ. Explain why, as ranked hypotheses about Bluesky sample chatter.

Workflow:
1. Read the disagreement-analyst skill (/radar_skills/disagreement-analyst/SKILL.md) and the
   analyst-note-writer skill first.
2. Run at least one data tool check that could falsify your top hypothesis. Cite evidence_ids you received
   (or the evidence_ids of the stored decision).
3. Finish by calling write_disagreement exactly once.

Rails: at most 12 data tool calls. Everything you write is a hypothesis. Never edit config, trend documents
or decisions; you cannot write files.
"""

PRECISION_SUBAGENT_PROMPT = """You audit the precision of sampled Bluesky posts for one style.
Follow the precision-auditor skill. Return: on-topic count over sample size, the off-topic patterns,
overlap with other styles, and the evidence_ids. Do not write decisions."""


# ---------------------------------------------------------------- sibling modules

def _import_optional(name: str):
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        if exc.name != name:
            raise  # the module exists but one of its own imports failed: surface it
        return None


def load_tools_module():
    return _import_optional("radar_agent_tools")


def load_spend_module():
    return _import_optional("radar_spend")


def micro_trend():
    from backend.services import micro_trend as mt
    return mt


# ---------------------------------------------------------------- spend ledger

class BudgetStop(Exception):
    """The ledger refused a call (cap reached, unpriced model, unreadable ledger) or none is available."""


class Ledger:
    """Adapter over scripts/radar_spend.py. Fails closed: no ledger, no paid call.

    check(): radar_spend Ledger.check_budget(provider) before every model call.
    record(): usage_from_langchain(usage_metadata), then Ledger.check_and_record(...) after every call.
    The returned estimated_cost_usd (prices from config/radar_prices.json) is the measured per-call cost.
    """

    def __init__(self, spend_module, provider: str, model: str, run_id: str, *, book=None,
                 resume: str | None = None):
        self.spend = spend_module
        self.provider = provider
        self.model = model
        self.run_id = run_id
        self.resume = resume
        self.book = book if book is not None else (spend_module.default_ledger() if spend_module else None)

    @property
    def ready(self) -> bool:
        return self.spend is not None and self.book is not None

    def _guard(self, fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except self.spend.SpendError as exc:
            raise BudgetStop(str(exc)) from exc

    def check(self) -> dict:
        if not self.ready:
            raise BudgetStop("spend ledger not available (scripts/radar_spend.py); refusing paid calls")
        return self._guard(self.book.check_budget, self.provider, run_id=self.run_id, resume=self.resume)

    def record(self, usage_metadata: dict, style: str | None = None) -> dict:
        tokens = self.spend.usage_from_langchain(usage_metadata or {})
        return self._guard(self.book.check_and_record, self.provider, self.model, run_id=self.run_id,
                           style=style, note="radar_agent", **tokens)

    def remaining_usd(self) -> float | None:
        if not self.ready:
            return None
        st = self._guard(self.book.status, self.provider)
        return st["cap_usd"] - st["total_usd"]


@dataclass
class StyleUsage:
    """Measured usage for one style, summed from ledger entries (main agent and subagents)."""
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, entry: dict) -> None:
        self.model_calls += 1
        self.input_tokens += int(entry.get("input_tokens") or 0)
        self.output_tokens += int(entry.get("output_tokens") or 0)
        self.cache_read_tokens += int(entry.get("cache_read_tokens") or 0)
        self.cache_write_tokens += (int(entry.get("cache_write_5m_tokens") or 0)
                                    + int(entry.get("cache_write_1h_tokens") or 0))
        self.cost_usd += float(entry.get("estimated_cost_usd") or 0.0)

    def as_dict(self) -> dict:
        return {"model_calls": self.model_calls, "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens, "cache_read_tokens": self.cache_read_tokens,
                "cache_write_tokens": self.cache_write_tokens, "cost_usd": round(self.cost_usd, 6)}


class LedgerCallback(BaseCallbackHandler):
    """Budget check before every chat model call and a ledger record after it.

    Callbacks passed in the invoke config propagate into deepagents subagents, so their calls are checked
    and recorded too. raise_error makes a refused check abort the run instead of being logged and ignored.
    """

    raise_error = True

    def __init__(self, ledger: Ledger):
        self.ledger = ledger
        self.style: str | None = None
        self.usage = StyleUsage()

    def reset(self, style: str | None) -> None:
        self.style = style
        self.usage = StyleUsage()

    def on_chat_model_start(self, serialized, messages, **kwargs):
        self.ledger.check()

    def on_llm_end(self, response, **kwargs):
        for gens in response.generations or []:
            for gen in gens:
                msg = getattr(gen, "message", None)
                if msg is not None:
                    self.usage.add(self.ledger.record(getattr(msg, "usage_metadata", None) or {}, self.style))


def find_budget_stop(exc: BaseException | None) -> BudgetStop | None:
    seen = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, BudgetStop):
            return exc
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return None


# ---------------------------------------------------------------- per-style tool budget

class ToolBudget:
    """Counts radar tool calls for one style, across the main agent and its subagents.

    radar_agent_tools also enforces 12 calls per style; this copy keeps the last slot for the final write,
    so the agent can always store a decision.
    """

    def __init__(self, limit: int = MAX_TOOL_CALLS_PER_STYLE):
        self.limit = limit
        self.calls: list[str] = []

    @property
    def used(self) -> int:
        return len(self.calls)

    def reset(self) -> None:
        self.calls = []

    def take(self, name: str) -> str | None:
        """Return None if the call may run, else a refusal message for the model."""
        cap = self.limit if name in FINAL_TOOLS else self.limit - 1
        if self.used >= cap:
            return json.dumps({"ok": False, "evidence_id": None, "error": (
                f"tool budget: {self.used} of {self.limit} tool calls used for this style. "
                f"Call {WRITE_DECISION} (or {WRITE_DISAGREEMENT}) now with the evidence you have.")})
        self.calls.append(name)
        return None


def counted_tool(fn: Callable | BaseTool, budget: ToolBudget) -> BaseTool:
    base = fn if isinstance(fn, BaseTool) else as_tool(fn)

    def run(**kwargs):
        refusal = budget.take(base.name)
        if refusal is not None:
            return refusal
        return base.invoke(kwargs)

    return StructuredTool.from_function(func=run, name=base.name, description=base.description,
                                        args_schema=base.args_schema, handle_tool_error=True)


def bound_write_decision(tools_module, model_id: str) -> Callable:
    """write_decision with model_id and prompt_version filled in by code, not by the model."""
    inner = tools_module.write_decision

    def write_decision(style: str, agent_label: str, confidence: str, reasons: list[str],
                       evidence_ids: list[str], lifespan_source_url: str | None = None) -> dict:
        return inner(style, agent_label, confidence, reasons, evidence_ids,
                     lifespan_source_url=lifespan_source_url, model_id=model_id, prompt_version=PROMPT_VERSION)

    write_decision.__doc__ = inner.__doc__
    return write_decision


def agent_tool_functions(tools_module, model_id: str) -> list:
    if tools_module is None or not getattr(tools_module, "TOOLS", None):
        raise RuntimeError("scripts/radar_agent_tools.py with a TOOLS list is required")
    return [bound_write_decision(tools_module, model_id) if f.__name__ == WRITE_DECISION else f
            for f in tools_module.TOOLS]


# ---------------------------------------------------------------- stores

def _jsonable(doc: dict) -> dict:
    return json.loads(json.dumps({k: v for k, v in doc.items() if k != "_id"}, default=str))


class Store:
    """Mongo collection (upsert with $set on style + run_id) and an append-only JSONL fallback."""

    def __init__(self, collection, jsonl_path: Path):
        self.collection = collection
        self.jsonl_path = jsonl_path
        self._indexed = False

    def save(self, doc: dict) -> str:
        """Persist now. Returns "db" or "jsonl_fallback" (the JSONL line is always written)."""
        doc = {**doc, "updated_at": now()}
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with self.jsonl_path.open("a") as fh:
            fh.write(json.dumps(_jsonable(doc)) + "\n")
        if self.collection is None:
            return "jsonl_fallback"
        try:
            if not self._indexed:
                self.collection.create_index([("style", 1), ("run_id", 1)], unique=True, name="style_run_unique")
                self._indexed = True
            self.collection.update_one({"style": doc["style"], "run_id": doc["run_id"]}, {"$set": doc}, upsert=True)
            return "db"
        except PyMongoError as exc:
            print(f"  warning: Mongo write failed ({type(exc).__name__}); kept in {self.jsonl_path.name}")
            return "jsonl_fallback"

    def find(self, query: dict) -> list[dict]:
        """Docs matching query from Mongo; from the JSONL (latest merged line per style + run) if Mongo fails."""
        if self.collection is not None:
            try:
                return list(self.collection.find(query))
            except PyMongoError as exc:
                print(f"  warning: Mongo read failed ({type(exc).__name__}); reading {self.jsonl_path.name}")
        if not self.jsonl_path.exists():
            return []
        latest: dict[tuple, dict] = {}
        for line in self.jsonl_path.read_text().splitlines():
            if line.strip():
                d = json.loads(line)
                key = (d.get("style"), d.get("run_id"))
                latest[key] = {**latest.get(key, {}), **d}
        return [d for d in latest.values() if all(d.get(k) == v for k, v in query.items())]


def already_decided(store: Store, style: str, metric_version: str, model_id: str) -> bool:
    """Resume rule: an agent decision exists with the same metric_version, prompt_version and model_id."""
    return bool(store.find({"style": style, "decision_source": "agent", "metric_version": metric_version,
                            "prompt_version": PROMPT_VERSION, "model_id": model_id}))


def mongo_db():
    """The app database (backend.db loads .env). None if it is not reachable."""
    try:
        from backend.db import db
        db.client.admin.command("ping")
        return db
    except (PyMongoError, RuntimeError) as exc:
        print(f"warning: MongoDB not reachable ({type(exc).__name__}); using JSONL only")
        return None


# ---------------------------------------------------------------- records

def agree_value(rule_label: str | None, agent_label: str | None, manual: bool) -> bool | None:
    if manual or rule_label is None or agent_label is None:
        return None
    return rule_label == agent_label


def build_record(*, style: str, run_id: str, model_id: str, rule: dict, agent: dict | None,
                 error: str | None = None, usage: dict | None = None, tool_calls: list[str] | None = None) -> dict:
    """Final radar_decisions fields for a non-manual style. "agent" if write_decision stored a label, else "rule".

    Manual styles are written by radar_agent_tools.write_manual_decisions (decision_source "manual").
    """
    rule_label = rule.get("rule_label")
    agent_label = (agent or {}).get("agent_label")
    rec = {"style": style, "run_id": run_id, "model_id": model_id, "prompt_version": PROMPT_VERSION,
           "metric_version": rule.get("metric_version"), "config_hash": rule.get("config_hash"),
           "rule_label": rule_label, "agent_label": agent_label,
           "agree": agree_value(rule_label, agent_label, manual=False),
           "excluded_from_agreement": False, "describes": "Bluesky sample chatter",
           "agent_error": error, "usage": usage, "tool_calls": tool_calls, "decided_at": now()}
    if agent_label is not None:
        rec.update(final_label=agent_label, decision_source="agent", confidence=agent.get("confidence"),
                   reasons=agent.get("reasons"), evidence_ids=agent.get("evidence_ids"))
    else:
        rec.update(final_label=rule_label, decision_source="rule", confidence=rule.get("confidence"),
                   reasons=rule.get("reasons"), evidence_ids=[])
    return rec


def _parse_json(text) -> Any:
    if isinstance(text, dict | list):
        return text
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def _successful_results(messages: list, tool_name: str) -> list[dict]:
    """JSON results (merged over call args) of calls to tool_name whose result says ok."""
    results = {m.tool_call_id: m for m in messages if isinstance(m, ToolMessage)}
    out = []
    for m in messages:
        if not isinstance(m, AIMessage):
            continue
        for call in m.tool_calls or []:
            res = results.get(call.get("id"))
            if call.get("name") != tool_name or res is None or getattr(res, "status", "success") == "error":
                continue
            parsed = _parse_json(res.content)
            if isinstance(parsed, dict) and parsed.get("ok") and not parsed.get("error"):
                out.append({**(call.get("args") or {}), **parsed})
    return out


def extract_decision(messages: list) -> dict | None:
    """The last stored write_decision. The tool's result wins over the model's args."""
    found = _successful_results(messages, WRITE_DECISION)
    if not found or found[-1].get("agent_label") not in LABELS:
        return None
    return found[-1]


def extract_disagreement(messages: list) -> dict | None:
    found = _successful_results(messages, WRITE_DISAGREEMENT)
    return found[-1] if found else None


# ---------------------------------------------------------------- write_disagreement tool

class DisagreementWriter:
    """Owns the write_disagreement tool. Persists to radar_disagreements + JSONL. Never touches config,
    trend documents or decisions."""

    def __init__(self, store: Store, run_id: str, model_id: str, budget: ToolBudget, tools_module=None):
        self.store = store
        self.run_id = run_id
        self.model_id = model_id
        self.budget = budget
        self.tools_module = tools_module
        self.style: str | None = None
        self.decision: dict = {}

    def as_tool(self) -> BaseTool:
        writer = self

        def write_disagreement(hypotheses: list[dict], overall_confidence: str, favors: str) -> dict:
            """Store ranked hypotheses for why rule_label and agent_label differ for the current style.

            Args:
                hypotheses: Ranked list, best first. Each item: {"statement": str, "evidence_ids": [str],
                    "tool_check": str (the tool you ran and what it showed, or ""),
                    "confidence": "low" | "medium" | "high"}. At least one item needs a non-empty tool_check.
                overall_confidence: "low", "medium" or "high".
                favors: Which label the evidence favors: "rule", "agent" or "unclear".
            """
            return writer.write(hypotheses, overall_confidence, favors)

        return as_tool(write_disagreement)

    def _known_evidence(self) -> set[str] | None:
        """Ids issued in this run for the style plus the stored decision's ids. None if not checkable."""
        tm = self.tools_module
        if tm is None or not hasattr(tm, "current_run"):
            return None
        ctx = tm.current_run()
        issued = set(ctx.evidence.get(tm._norm(self.style), set()))
        return issued | set(self.decision.get("evidence_ids") or [])

    def write(self, hypotheses: list[dict], overall_confidence: str, favors: str) -> dict:
        problems = []
        hypotheses = list(hypotheses or [])
        if not self.style:
            problems.append("no current style")
        if not hypotheses:
            problems.append("hypotheses must not be empty")
        known = self._known_evidence()
        for i, h in enumerate(hypotheses, 1):
            if not h.get("statement"):
                problems.append(f"hypothesis {i} has no statement")
            ids = h.get("evidence_ids") or []
            if not ids:
                problems.append(f"hypothesis {i} has no evidence_ids")
            elif known is not None and (bad := [x for x in ids if x not in known]):
                problems.append(f"hypothesis {i} cites unknown evidence_ids {bad}")
            if h.get("confidence") not in CONFIDENCES:
                problems.append(f"hypothesis {i} confidence must be low, medium or high")
        if hypotheses and not any(str(h.get("tool_check") or "").strip() for h in hypotheses):
            problems.append("at least one hypothesis needs a tool_check")
        checks = [c for c in self.budget.calls if c not in FINAL_TOOLS]
        if not checks:
            problems.append("run at least one data tool check before writing")
        if overall_confidence not in CONFIDENCES:
            problems.append("overall_confidence must be low, medium or high")
        if favors not in ("rule", "agent", "unclear"):
            problems.append("favors must be rule, agent or unclear")
        if problems:
            return {"ok": False, "evidence_id": None, "error": "; ".join(problems)}
        doc = {"style": self.style, "run_id": self.run_id, "model_id": self.model_id,
               "prompt_version": PROMPT_VERSION, "kind": "hypotheses",
               "note": "Hypotheses about Bluesky sample chatter, not findings.",
               "decision_run_id": self.decision.get("run_id"), "rule_label": self.decision.get("rule_label"),
               "agent_label": self.decision.get("agent_label"),
               "metric_version": self.decision.get("metric_version"),
               "hypotheses": [{**h, "rank": i, "is_hypothesis": True} for i, h in enumerate(hypotheses, 1)],
               "overall_confidence": overall_confidence, "favors": favors, "tool_checks_run": checks,
               "created_at": now()}
        stored = self.store.save(doc)
        return {"ok": True, "evidence_id": None, "stored": stored, "style": self.style,
                "hypotheses": len(hypotheses)}


# ---------------------------------------------------------------- agent construction

def model_id_from_env() -> str | None:
    return (os.environ.get("RADAR_AGENT_MODEL") or "").strip() or None


def split_model_id(model_id: str) -> tuple[str, str]:
    """"anthropic:claude-sonnet-5-5" -> ledger provider "claude", ledger model "claude-sonnet-5-5"."""
    provider, _, name = model_id.partition(":")
    if not name:
        provider, name = "anthropic", model_id
    return ("claude" if provider in ("anthropic", "claude") else provider), name


def make_chat_model(model_id: str):
    """Construct the chat model. Construction makes no API call."""
    from langchain.chat_models import init_chat_model
    return init_chat_model(model_id, max_tokens=4096)


def read_only_permissions():
    """Agent filesystem: read radar_skills/ and config/ only, write nothing (first matching rule wins)."""
    from deepagents.middleware.filesystem import FilesystemPermission
    return [
        FilesystemPermission(operations=["write"], paths=["/**"], mode="deny"),
        FilesystemPermission(operations=["read"], paths=["/radar_skills/**", "/config/**"], mode="allow"),
        FilesystemPermission(operations=["read"], paths=["/**"], mode="deny"),
    ]


def subagent_specs(data_tools: list[BaseTool]) -> list[dict]:
    """deepagents subagents do not inherit skills, so each gets its own skills list."""
    sampler = [t for t in data_tools if t.name in ("sample_posts", "record_event")]
    return [{
        "name": "precision-auditor",
        "description": "Audits whether sampled Bluesky posts for one style are on-topic. Give it the style name.",
        "system_prompt": PRECISION_SUBAGENT_PROMPT,
        "tools": sampler,
        "skills": [SKILLS_DIR + "precision-auditor/", SKILLS_DIR + "analyst-note-writer/"],
    }]


def build_agent(model, data_tools: list[BaseTool], *, mode: str = "judge", extra_tools: list[BaseTool] | None = None):
    """Build the deep agent. mode "judge" decides styles; mode "disagreement" is the disagreement analyst."""
    from deepagents import create_deep_agent
    from deepagents.backends import FilesystemBackend
    from langchain.agents.middleware import ModelCallLimitMiddleware

    backend = FilesystemBackend(root_dir=str(ROOT), virtual_mode=True)
    if mode == "disagreement":
        prompt = DISAGREEMENT_PROMPT
        skills = [SKILLS_DIR + "disagreement-analyst/", SKILLS_DIR + "analyst-note-writer/"]
        tools = [t for t in data_tools if t.name != WRITE_DECISION]
        subagents = []
    else:
        prompt, skills, tools, subagents = JUDGE_PROMPT, [SKILLS_DIR], list(data_tools), subagent_specs(data_tools)
    return create_deep_agent(
        model=model,
        tools=tools + list(extra_tools or []),
        system_prompt=prompt,
        skills=skills,
        subagents=subagents,
        backend=backend,
        permissions=read_only_permissions(),
        middleware=[ModelCallLimitMiddleware(run_limit=MAX_MODEL_CALLS_PER_STYLE, exit_behavior="end")],
        name=f"radar-{mode}",
    )


# ---------------------------------------------------------------- style selection

def all_styles(db) -> list[str]:
    if db is None:
        raise RuntimeError("MongoDB is required to list styles (social_trends)")
    return sorted({d["term"] for d in db["social_trends"].find({}, {"term": 1}) if d.get("term")})


def select_styles(args, db) -> list[str]:
    if args.styles:
        return [s.strip() for s in args.styles.split(",") if s.strip()]
    mt = micro_trend()
    pool = all_styles(db)
    if args.random:
        candidates = [s for s in pool if not mt.is_manual(s)]
        return sorted(random.Random(args.seed).sample(candidates, min(args.random, len(candidates))))
    return pool


def safe_rule(style: str) -> dict | None:
    try:
        return micro_trend().compute_rule_metrics(style)
    except LookupError:
        return None


# ---------------------------------------------------------------- cost projection

@dataclass
class RunState:
    run_id: str
    model_id: str
    measured: list[dict] = field(default_factory=list)  # one usage dict per style run this process

    def spent(self) -> float:
        return sum(m.get("cost_usd") or 0.0 for m in self.measured)

    def mean_cost(self) -> float | None:
        return self.spent() / len(self.measured) if self.measured else None


def projection(state: RunState, remaining: int) -> dict:
    mean = state.mean_cost()
    return {"measured_styles": len(state.measured), "mean_cost_usd": mean, "spent_usd": state.spent(),
            "remaining_styles": remaining, "projected_remaining_usd": mean * remaining if mean is not None else None}


def print_projection(state: RunState, remaining: int, total_agent_styles: int | None = None) -> dict:
    p = projection(state, remaining)
    if p["mean_cost_usd"] is None:
        print("projection: no style measured yet")
        return p
    line = (f"projection: mean ${p['mean_cost_usd']:.4f}/style over {p['measured_styles']} styles, "
            f"spent ${p['spent_usd']:.4f}; {remaining} styles left -> ${p['projected_remaining_usd']:.4f}")
    if total_agent_styles:
        line += f"; all {total_agent_styles} agent styles -> ${p['mean_cost_usd'] * total_agent_styles:.4f}"
    print(line)
    return p


def projection_exceeds(state: RunState, remaining: int, budget_usd: float | None, pilot: int) -> bool:
    """True once at least `pilot` styles are measured and mean cost x remaining styles > budget left."""
    if budget_usd is None or len(state.measured) < pilot:
        return False
    p = projection(state, remaining)
    return p["projected_remaining_usd"] is not None and p["projected_remaining_usd"] > budget_usd - state.spent()


# ---------------------------------------------------------------- running

def invoke_agent(agent, message: str, callback: LedgerCallback, thread: str) -> list:
    out = agent.invoke({"messages": [{"role": "user", "content": message}]},
                       config={"callbacks": [callback], "recursion_limit": RECURSION_LIMIT,
                               "configurable": {"thread_id": thread}})
    return out.get("messages", [])


def decide_style(agent, style: str, *, state: RunState, rule: dict, budget: ToolBudget,
                 callback: LedgerCallback, store: Store) -> dict:
    """Run the agent on one style and persist the record at once. Raises BudgetStop on a refused call."""
    budget.reset()
    callback.reset(style)
    msg = (f"Decide the style {style!r}.\nrun_id: {state.run_id}\nmetric_version: {rule.get('metric_version')}\n"
           f"Use only evidence from tools. Finish with {WRITE_DECISION}.")
    error, decision, t0 = None, None, time.monotonic()
    try:
        decision = extract_decision(invoke_agent(agent, msg, callback, f"{state.run_id}:{style}"))
        if decision is None:
            error = "agent finished without a stored write_decision"
    except Exception as exc:
        if (stop := find_budget_stop(exc)) is not None:
            raise stop from exc
        error = f"{type(exc).__name__}: {exc}"[:500]
    usage = {**callback.usage.as_dict(), "seconds": round(time.monotonic() - t0, 1)}
    rec = build_record(style=style, run_id=state.run_id, model_id=state.model_id, rule=rule, agent=decision,
                       error=error, usage=usage, tool_calls=list(budget.calls))
    rec["stored"] = store.save(rec)
    state.measured.append({"style": style, **usage})
    print(f"  {style}: final={rec['final_label']} source={rec['decision_source']} rule={rec['rule_label']} "
          f"agent={rec['agent_label']} agree={rec['agree']} tools={len(budget.calls)} "
          f"calls={usage['model_calls']} in={usage['input_tokens']} out={usage['output_tokens']} "
          f"cost=${usage['cost_usd']:.4f}" + (f" error={error}" if error else ""))
    return rec


def resume_command(args, run_id: str) -> str:
    parts = [".venv/bin/python scripts/radar_agent.py"]
    if args.disagreements:
        parts.append("--disagreements")
    elif args.styles:
        parts.append(f'--styles "{args.styles}"')
    elif args.random:
        parts.append(f"--random {args.random} --seed {args.seed}")
    else:
        parts.append("--all")
    parts.append(f"--run-id {run_id}")
    if args.cap_usd is not None:
        parts.append(f"--cap-usd {args.cap_usd}")
    return " ".join(parts)


def run_decisions(args, *, db, model, model_id: str, tools_module, ledger: Ledger, store: Store) -> int:
    mt = micro_trend()
    state = RunState(run_id=args.run_id, model_id=model_id)
    styles = select_styles(args, db)
    manual_styles = [s for s in styles if mt.is_manual(s)]
    agent_styles = [s for s in styles if not mt.is_manual(s)]
    print(f"run_id={state.run_id} model_id={model_id} prompt_version={PROMPT_VERSION} "
          f"styles={len(styles)} (agent {len(agent_styles)}, manual {len(manual_styles)})")

    tools_module.start_run(args.run_id, db=db)
    if manual_styles:
        for doc in tools_module.write_manual_decisions(manual_styles):
            print(f"  {doc['style']}: manual final={doc['final_label']} (agent skipped, stored={doc.get('stored')})")

    todo = []
    for style in agent_styles:
        rule = safe_rule(style)
        if rule is None:
            print(f"  {style}: skipped, no social_trends doc")
        elif not args.redecide and already_decided(store, style, rule["metric_version"], model_id):
            print(f"  {style}: skipped, already decided (same metric_version, prompt_version, model_id)")
        else:
            todo.append((style, rule))
    if not todo:
        print("nothing to decide")
        return 0

    budget = ToolBudget()
    agent = build_agent(model, [counted_tool(f, budget) for f in agent_tool_functions(tools_module, model_id)])
    callback = LedgerCallback(ledger)
    budget_usd = args.cap_usd if args.cap_usd is not None else ledger.remaining_usd()
    print(f"budget for projection: {f'${budget_usd:.2f}' if budget_usd is not None else 'none'} "
          f"({'--cap-usd' if args.cap_usd is not None else 'ledger remaining'}); pilot={args.pilot}")
    for i, (style, rule) in enumerate(todo):
        left = len(todo) - i
        if projection_exceeds(state, left, budget_usd, args.pilot):
            print_projection(state, left, len(agent_styles))
            print(f"STOP: projected spend for the {left} remaining styles exceeds the budget left "
                  f"(${budget_usd - state.spent():.4f}).")
            print(f"resume with: {resume_command(args, state.run_id)}")
            return 3
        try:
            decide_style(agent, style, state=state, rule=rule, budget=budget, callback=callback, store=store)
        except BudgetStop as exc:
            print(f"STOP: spend ledger refused the call: {exc}")
            print_projection(state, left, len(agent_styles))
            print(f"resume with: {resume_command(args, state.run_id)}")
            return 3
    print_projection(state, 0, len(agent_styles))
    return 0


def latest_disagreements(store: Store, run_id: str | None, styles: str | None) -> dict[str, dict]:
    query: dict = {"agree": False, "decision_source": "agent"}
    if run_id:
        query["run_id"] = run_id
    latest: dict[str, dict] = {}
    for r in store.find(query):
        if r["style"] not in latest or str(r.get("decided_at")) > str(latest[r["style"]].get("decided_at")):
            latest[r["style"]] = r
    if styles:
        wanted = {s.strip() for s in styles.split(",")}
        latest = {k: v for k, v in latest.items() if k in wanted}
    return latest


def run_disagreements(args, *, db, model, model_id: str, tools_module, ledger: Ledger, store: Store,
                      disagreement_store: Store) -> int:
    targets = latest_disagreements(store, args.run_id if args.run_id_given else None, args.styles)
    done = set() if args.redecide else {
        d["style"] for d in disagreement_store.find({"prompt_version": PROMPT_VERSION, "model_id": model_id})}
    print(f"disagreements: {len(targets)} styles where rule_label differs from agent_label")
    tools_module.start_run(args.run_id, db=db)
    budget = ToolBudget()
    writer = DisagreementWriter(disagreement_store, args.run_id, model_id, budget, tools_module)
    data_tools = [counted_tool(f, budget) for f in agent_tool_functions(tools_module, model_id)]
    agent = build_agent(model, data_tools, mode="disagreement", extra_tools=[counted_tool(writer.as_tool(), budget)])
    callback = LedgerCallback(ledger)
    state = RunState(run_id=args.run_id, model_id=model_id)
    for style, dec in sorted(targets.items()):
        if style in done:
            print(f"  {style}: skipped, hypotheses already stored")
            continue
        budget.reset()
        callback.reset(style)
        writer.style, writer.decision = style, dec
        msg = (f"Style {style!r}: rule_label={dec.get('rule_label')}, agent_label={dec.get('agent_label')}.\n"
               f"Stored agent reasons: {json.dumps(dec.get('reasons'), default=str)}\n"
               f"Stored evidence_ids: {json.dumps(dec.get('evidence_ids'), default=str)}\n"
               f"Explain the disagreement and finish with {WRITE_DISAGREEMENT}.")
        saved = None
        try:
            saved = extract_disagreement(invoke_agent(agent, msg, callback, f"{args.run_id}:disagree:{style}"))
        except Exception as exc:  # noqa: BLE001 - one failed style must not end the batch
            if (stop := find_budget_stop(exc)) is not None:
                print(f"STOP: spend ledger refused the call: {stop}")
                print(f"resume with: {resume_command(args, args.run_id)}")
                return 3
            print(f"  {style}: error {type(exc).__name__}: {str(exc)[:200]}")
        usage = callback.usage.as_dict()
        state.measured.append({"style": style, **usage})
        print(f"  {style}: hypotheses={'stored' if saved else 'NOT stored'} tools={len(budget.calls)} "
              f"in={usage['input_tokens']} out={usage['output_tokens']} cost=${usage['cost_usd']:.4f}")
    print_projection(state, 0)
    return 0


# ---------------------------------------------------------------- dry run

def skill_summary() -> list[dict]:
    """Parse each radar_skills/*/SKILL.md frontmatter the way deepagents does (YAML)."""
    import yaml
    out = []
    for path in sorted((ROOT / "radar_skills").glob("*/SKILL.md")):
        text = path.read_text()
        meta = yaml.safe_load(text.split("---")[1]) if text.startswith("---") else {}
        out.append({"dir": path.parent.name, "name": (meta or {}).get("name"),
                    "description": (meta or {}).get("description")})
    return out


def dry_run(args, *, db, model_id: str | None, tools_module) -> int:
    from deepagents import create_deep_agent
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

    print(f"create_deep_agent{inspect.signature(create_deep_agent)}")
    print(f"model_id={model_id or '(RADAR_AGENT_MODEL not set)'} prompt_version={PROMPT_VERSION}")
    for s in skill_summary():
        ok = s["name"] == s["dir"] and bool(s["description"])
        print(f"skill {s['dir']}: name={s['name']} {'ok' if ok else 'INVALID (name must match dir, needs description)'}")
    model = None
    if model_id:
        try:
            model = make_chat_model(model_id)
        except Exception as exc:  # noqa: BLE001 - dry run only, falls back to a fake model
            print(f"note: could not construct {model_id} ({type(exc).__name__}); using a fake model")
    model = model or GenericFakeChatModel(messages=iter([]))
    budget = ToolBudget()
    tools = [counted_tool(f, budget) for f in agent_tool_functions(tools_module, model_id or "dry-run")]
    agent = build_agent(model, tools)
    print(f"built agent {agent.name!r} with {len(tools)} tools: {[t.name for t in tools]}")
    provider, name = split_model_id(model_id or "anthropic:unset")
    ledger = Ledger(load_spend_module(), provider, name, args.run_id)
    print(f"spend ledger: {'ready' if ledger.ready else 'NOT ready (paid runs will refuse)'}; "
          f"remaining {provider} budget: {ledger.remaining_usd() if ledger.ready else 'n/a'}")
    if db is not None and (args.styles or args.random or args.all):
        mt = micro_trend()
        store = Store(db[DECISIONS_COLLECTION], DECISIONS_JSONL)
        for style in select_styles(args, db):
            if mt.is_manual(style):
                print(f"  {style}: manual, agent skips")
            elif (rule := safe_rule(style)) is None:
                print(f"  {style}: no social_trends doc")
            else:
                skip = model_id and not args.redecide and already_decided(store, style, rule["metric_version"], model_id)
                print(f"  {style}: rule_label={rule['rule_label']} -> {'skip (already decided)' if skip else 'decide'}")
    print("dry run: no model call made")
    return 0


# ---------------------------------------------------------------- CLI

def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Social Trend Radar deep agent")
    sel = p.add_mutually_exclusive_group()
    sel.add_argument("--styles", help="comma-separated style terms")
    sel.add_argument("--all", action="store_true", help="every style in social_trends")
    sel.add_argument("--random", type=int, help="N random non-manual styles")
    p.add_argument("--seed", type=int, default=None, help="seed for --random (printed in the resume command)")
    p.add_argument("--redecide", action="store_true", help="decide again even if already decided with the same versions")
    p.add_argument("--run-id", help="run id (default: new). Reuse to continue a run")
    p.add_argument("--resume", action="store_true", help="continue --run-id; implies --all when no selection is given")
    p.add_argument("--dry-run", action="store_true", help="build the agent, make no model call")
    p.add_argument("--disagreements", action="store_true",
                   help="disagreement analyst on styles where rule_label differs from agent_label")
    p.add_argument("--pilot", type=int, default=DEFAULT_PILOT, help="styles measured before the cost projection gates")
    p.add_argument("--cap-usd", type=float, default=None,
                   help="budget for the projection gate (default: remaining Claude cap in the spend ledger)")
    args = p.parse_args(argv)
    if args.resume and not args.run_id:
        p.error("--resume needs --run-id")
    if args.resume and not (args.styles or args.random or args.disagreements):
        args.all = True
    if not (args.styles or args.all or args.random or args.disagreements or args.dry_run):
        p.error("choose --styles, --all, --random N, --disagreements or --dry-run")
    if args.random is not None and args.seed is None:
        args.seed = random.SystemRandom().randrange(1_000_000)
    args.run_id_given = bool(args.run_id)
    args.run_id = args.run_id or now().strftime("radar-%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    model_id = model_id_from_env()
    db = mongo_db()
    tools_module = load_tools_module()
    if tools_module is None:
        print("scripts/radar_agent_tools.py is required")
        return 2
    if args.dry_run:
        return dry_run(args, db=db, model_id=model_id, tools_module=tools_module)
    if not model_id:
        print("RADAR_AGENT_MODEL is not set (for example anthropic:claude-sonnet-5-5)")
        return 2
    provider, name = split_model_id(model_id)
    ledger = Ledger(load_spend_module(), provider, name, args.run_id, resume=resume_command(args, args.run_id))
    if not ledger.ready:
        print("spend ledger (scripts/radar_spend.py) is not available; refusing paid calls")
        return 2
    model = make_chat_model(model_id)
    store = Store(db[DECISIONS_COLLECTION] if db is not None else None, DECISIONS_JSONL)
    if args.disagreements:
        dstore = Store(db[DISAGREEMENTS_COLLECTION] if db is not None else None, DISAGREEMENTS_JSONL)
        return run_disagreements(args, db=db, model=model, model_id=model_id, tools_module=tools_module,
                                 ledger=ledger, store=store, disagreement_store=dstore)
    return run_decisions(args, db=db, model=model, model_id=model_id, tools_module=tools_module,
                         ledger=ledger, store=store)


if __name__ == "__main__":
    sys.exit(main())
