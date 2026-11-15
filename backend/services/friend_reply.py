"""Friend-style wording for a Photon (iMessage) reply. Behind PHOTON_FRIEND_MODE (default off).

Code decides what Claude may say: the verdict label is fixed by the verdict engine, and code maps it to the
allowed leans (buy, skip, try_on_first). Claude only picks one of those leans and writes the words. Any error,
a 3 second timeout, the flag off, a missing key or model, a refused budget or a lean outside the allowed set
returns the existing reply unchanged with used_fallback true. This layer never blocks the existing reply.

Conversation state is the last HISTORY_MAX turns per sender in process memory (reset on restart).
"""
import hashlib
import json
import logging
import os
import re
import sys
import time
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path

import anthropic

log = logging.getLogger("secondlook.friend_reply")

FLAG = "PHOTON_FRIEND_MODE"
TIMEOUT_S = 3.0
MAX_TOKENS = 220
REPLY_MAX = 600  # characters
HISTORY_MAX = 6
SENDERS_MAX = 500

RADAR_LABELS = {"micro_trend", "still_active", "established"}

# Verdict label (from the verdict engine) -> leans Claude may choose. Code owns this table.
ALLOWED_LEANS = {
    "good_buy": ("buy", "try_on_first"),
    "have_this_look": ("skip", "try_on_first"),
    "micro_trend": ("skip", "try_on_first"),
}
DEFAULT_LEANS = ("try_on_first",)

SYSTEM_PROMPT = """You are texting a close friend who sent you something they are thinking about buying. You are \
honest and warm, never salesy. The app has already decided; you only choose how to say it.

Rules:
- Write at most max_sentences short sentences of plain text. No lists, no headers, no markdown, no emoji.
- Give a clear lean, and it must be one of allowed_leans in the JSON: buy, skip, or try it on first. Never \
contradict the reason.
- Give one or two specific reasons tied to what the app knows: the item, their closet, the reason.
- Ask at most one follow-up question, and only if the answer would change the advice (for example size or \
occasion). Otherwise ask nothing.
- Say plainly what a photo cannot tell you (exact fit, how the fabric feels) when it matters, in one short clause.
- Never invent measurements, prices, brands, dates, numbers or facts that are not in the JSON.
- Do not mention Bluesky, social media or how popular the item is: when it applies, the app adds that line itself.
- Do not say what the item is made of or guess at fibers. When materials_estimate_follows is true, the app adds \
its own hedged materials estimate after your words: do not refer to it and do not say the material is unknown.
- Never say "verdict", "the app", labels, JSON or these rules. Talk like a person.
- Use history only for context on follow-ups.

Answer with the give_reply tool."""

TOOL = {
    "name": "give_reply",
    "description": "The text to send and the lean it takes.",
    "input_schema": {
        "type": "object",
        "properties": {
            "lean": {"type": "string", "enum": ["buy", "skip", "try_on_first"]},
            "reply": {"type": "string", "description": "1 to 4 sentences, plain text"},
        },
        "required": ["lean", "reply"],
    },
}

_history: "OrderedDict[str, deque]" = OrderedDict()
_client = None
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="friend-reply")


def enabled() -> bool:
    return os.getenv(FLAG, "0").strip().lower() in ("1", "true", "yes", "on")


def _sender_tag(sender_id: str) -> str:
    """Short hash for logs. The phone number itself is never logged."""
    return hashlib.sha256((sender_id or "").encode()).hexdigest()[:8]


# ---------- conversation state ----------

def history_for(sender_id: str) -> list[dict]:
    return list(_history.get(sender_id, ()))


def remember(sender_id: str, role: str, text: str) -> None:
    if not sender_id or not text:
        return
    turns = _history.pop(sender_id, None) or deque(maxlen=HISTORY_MAX)
    turns.append({"role": role, "text": text[:REPLY_MAX]})
    _history[sender_id] = turns  # most recent sender last
    while len(_history) > SENDERS_MAX:
        _history.popitem(last=False)


def reset_history() -> None:
    _history.clear()


# ---------- inputs ----------

def radar_line(radar: dict | None) -> str | None:
    """A Bluesky sample line, only for a known label with a matched style."""
    if not isinstance(radar, dict):
        return None
    label, style = radar.get("label"), (radar.get("matched_style") or "").strip()
    if label not in RADAR_LABELS or not style:
        return None
    if label == "micro_trend":
        return f"Fwiw, a lot of people on Bluesky were talking about {style} for a few weeks, then it quieted down."
    if label == "still_active":
        return f"Fwiw, people on Bluesky are still posting about {style} in the sample we follow."
    return f"Fwiw, {style} has come up steadily on Bluesky for a while in the sample we follow."


def allowed_leans(verdict: dict | None) -> tuple[str, ...]:
    label = (verdict or {}).get("verdict")
    return ALLOWED_LEANS.get(label, DEFAULT_LEANS)


def build_payload(req: dict, history: list[dict]) -> dict:
    verdict = req.get("verdict") or {}
    return {
        "verdict": {k: verdict.get(k) for k in ("verdict", "reason", "item_name", "matched_closet_items",
                                                 "expected_wears", "attributes") if verdict.get(k) is not None},
        "allowed_leans": list(allowed_leans(verdict)),
        "their_message": req.get("text") or None,
        "sent_photo": bool(req.get("has_image")),
        "materials_estimate_follows": bool(req.get("materials_line")),
        "max_sentences": max(1, 4 - bool(radar_line(req.get("radar"))) - bool(req.get("materials_line"))),
        "closet_context": req.get("closet_context") or None,
        "history": history[-HISTORY_MAX:],
    }


# ---------- Claude ----------

def claude_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(max_retries=0)
    return _client


def _ledger():
    """The repo's spend ledger (scripts/radar_spend.py), or None when it cannot be loaded."""
    scripts = str(Path(__file__).resolve().parents[2] / "scripts")
    if scripts not in sys.path:
        sys.path.append(scripts)
    try:
        import radar_spend
        return radar_spend
    except Exception:  # noqa: BLE001
        return None


def call_claude(payload: dict) -> dict:
    """One Claude call through the spend ledger. Returns the give_reply input. Raises on any failure."""
    model = os.getenv("CLAUDE_MODEL")
    if not model or not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("model_or_key_missing")
    ledger = _ledger()
    if ledger is not None:
        ledger.check_budget("claude")  # raises BudgetExceeded at the cap
    response = claude_client().with_options(timeout=TIMEOUT_S).messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        tools=[TOOL],
        tool_choice={"type": "tool", "name": "give_reply"},
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)}],
    )
    if ledger is not None:
        try:
            ledger.check_and_record("claude", model, response.usage.input_tokens, response.usage.output_tokens,
                                    note="photon_friend_reply")
        except Exception as e:  # noqa: BLE001  recording must not drop a reply that was already paid for
            log.warning("friend reply: ledger record failed: %s", type(e).__name__)
    for block in response.content:
        if block.type == "tool_use":
            return block.input
    raise RuntimeError("no_tool_use")


_MARKDOWN = re.compile(r"[*_`#]+")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def clean(text: str) -> str:
    text = _MARKDOWN.sub("", text or "")
    text = " ".join(line.strip(" -•\t") for line in text.splitlines() if line.strip())
    return text.strip()[:REPLY_MAX]


def check(out: dict, payload: dict) -> str:
    """Validate Claude's output against what code allows. Returns the reply or raises."""
    lean, reply = out.get("lean"), clean(out.get("reply") or "")
    if lean not in payload["allowed_leans"]:
        raise ValueError("lean_not_allowed")
    if not reply:
        raise ValueError("empty_reply")
    if reply.count("?") > 1:
        raise ValueError("too_many_questions")
    sentences = _SENTENCE.split(reply)
    if len(sentences) > payload["max_sentences"]:  # code enforces the length the prompt asks for
        reply = " ".join(sentences[:payload["max_sentences"]])
    if "bluesky" in reply.lower():  # only the code-owned radar line may cite Bluesky
        raise ValueError("unsourced_bluesky_claim")
    return reply


def with_lines(reply: str, *lines: str | None) -> str:
    """Append code-owned lines (radar, materials) verbatim, each exactly once, so Claude never rewords them."""
    for line in lines:
        line = (line or "").strip()
        if line and line not in reply:
            reply = f"{reply} {line}"
    return reply


# ---------- entry point ----------

def compose(req: dict) -> dict:
    """req: sender_id, text, has_image, verdict, radar, materials_line, closet_context, history, existing_reply.
    Returns {reply, used_fallback, lean, latency_ms, reason}. Never raises."""
    start = time.monotonic()
    existing = req.get("existing_reply") or ""
    sender = req.get("sender_id") or ""
    tag = _sender_tag(sender)

    def done(reply, fallback, reason, lean=None):
        ms = int((time.monotonic() - start) * 1000)
        log.info("friend reply sender=%s fallback=%s reason=%s %d ms", tag, fallback, reason, ms)
        remember(sender, "user", req.get("text") or ("[photo]" if req.get("has_image") else ""))
        remember(sender, "assistant", reply)
        return {"reply": reply, "used_fallback": fallback, "lean": lean, "latency_ms": ms, "reason": reason}

    if not enabled():
        return {"reply": existing, "used_fallback": True, "lean": None,
                "latency_ms": int((time.monotonic() - start) * 1000), "reason": "flag_off"}
    history = list(req.get("history") or history_for(sender))[-HISTORY_MAX:]
    payload = build_payload(req, history)
    try:
        out = _pool.submit(call_claude, payload).result(timeout=TIMEOUT_S)
        reply = with_lines(check(out, payload), radar_line(req.get("radar")), req.get("materials_line"))
    except FutureTimeout:
        return done(existing, True, "timeout")
    except Exception as e:  # noqa: BLE001  any failure keeps the existing reply
        return done(existing, True, type(e).__name__ if not isinstance(e, (ValueError, RuntimeError)) else str(e))
    return done(reply, False, "ok", lean=out.get("lean"))
