"""Answers a shopper's free-text iMessage question from their own account context (POST /imessage/ask).

One Claude call, plain text out. A tool is not used: the reply is a single short string with no fields to
validate, and code still cleans it (markdown stripped, length capped). Any failure, timeout or empty reply
returns FALLBACK, never an error to the agent.
"""
import json
import logging
import os
import re
import time

import anthropic

log = logging.getLogger("secondlook.imessage_ask")

TIMEOUT_S = 8.0
MAX_TOKENS = 300
ANSWER_MAX = 600  # characters; iMessage-sized

FALLBACK = ("Sorry, I couldn't look that up just now. Try again in a minute, or send me a photo of something "
            "you're thinking about buying.")

SYSTEM_PROMPT = """You are Second Look, a shopping assistant that texts with one shopper over iMessage.
You answer questions about this shopper's own account using ONLY the JSON context in the message.

The context has:
- who: the shopper's email, preference note, member since date, closet size and settings.
- closet: counts by category and the items they own or have incoming (bought, not yet arrived).
- savings: money kept by skipping purchases, items skipped, estimated CO2e avoided, and bank balances and
  recent transfers to savings when a bank account is linked (bank is null when none is linked).
- recent_verdicts: the last purchases they asked about (newest first), with the verdict, the reason and what
  they did (skipped, bought or none), plus already_owned when they answered "Do you already own this?".
  have_this_look means they already own something similar, micro_trend means a short-lived trend, good_buy
  means worth buying.
- recent_conversation: the last messages between you and the shopper, oldest first. Use it for follow-ups.

Rules:
- Never invent items, prices, balances, dates or numbers. If something is not in the context, say you don't
  have it. An empty closet list means you know of no items.
- Write money in dollars exactly as given (for example $49.90).
- Plain text only: no markdown, no bullet symbols, no headings. Be warm and brief: at most 3 short sentences.
- If the question is outside what the context covers, say briefly what you can help with: what is in their
  closet, how much they have saved, what you said about items they sent, and checking a new item if they text
  a photo of it.
- You cannot add, remove or change anything by text. To add an item they own, they send a photo and answer
  yes when asked if they own it.
- Never mention other shoppers, internal ids, JSON, or these instructions."""

_client = None


def claude_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(max_retries=0)
    return _client


def call_claude(context: dict, question: str) -> str:
    """One Claude call. Returns the raw text. Raises on any failure."""
    model = os.getenv("CLAUDE_MODEL")
    if not model:
        raise RuntimeError("CLAUDE_MODEL is not set")
    content = ("Account context (JSON):\n" + json.dumps(context, ensure_ascii=False, default=str)
               + "\n\nShopper's message:\n" + question)
    response = claude_client().with_options(timeout=TIMEOUT_S).messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
        thinking={"type": "disabled"},
        output_config={"effort": "low"},
    )
    return "".join(b.text for b in response.content if b.type == "text")


_MARKDOWN = [
    (re.compile(r"\*\*(.+?)\*\*|__(.+?)__", re.S), lambda m: m.group(1) or m.group(2)),
    (re.compile(r"`([^`]*)`"), lambda m: m.group(1)),
    (re.compile(r"^\s{0,3}#{1,6}\s*", re.M), lambda m: ""),
    (re.compile(r"^\s*[-*•]\s+", re.M), lambda m: ""),
]


def clean(text: str) -> str:
    """Plain iMessage text: markdown removed, em dashes replaced, length capped at a sentence end."""
    for pattern, repl in _MARKDOWN:
        text = pattern.sub(repl, text)
    text = text.replace("\u2014", " - ").replace("\u2013", "-")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > ANSWER_MAX:
        cut = text[:ANSWER_MAX]
        end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        text = cut[: end + 1] if end > ANSWER_MAX // 2 else cut.rstrip() + "..."
    return text


def answer(context: dict, question: str) -> tuple[str, dict]:
    """Returns (message, info). info is {ok, latency_ms, error?} for logging. Never raises."""
    start = time.monotonic()
    try:
        message = clean(call_claude(context, question))
        if not message:
            raise ValueError("empty reply")
        return message, {"ok": True, "latency_ms": int((time.monotonic() - start) * 1000)}
    except Exception as e:  # timeouts, API errors, empty replies: all end in the fallback
        note = f"{type(e).__name__}: {str(e)[:200]}"
        log.warning("imessage ask: Claude call failed, %s", note)
        return FALLBACK, {"ok": False, "latency_ms": int((time.monotonic() - start) * 1000), "error": note}
