"""Startup warm-up, in a background thread, so the first check after a restart is not slower than the rest.

- Claude: one small call per strict tool schema (the verdict tool and the /read-price tool). Anthropic compiles
  a strict schema on first use (about 4 s once, then cached), and the first call also opens the connection.
- Gemini: opens the connections of both Gemini clients with a model lookup. No generate call, so no quota is used.
- MongoDB: opens the pool and reads the trend list once.

Every step is best effort: a failure is logged and the server works as before. SECONDLOOK_WARMUP=0 turns it off.
"""
import logging
import os
import threading
import time

log = logging.getLogger("uvicorn.error")


def _claude_tool(client, model: str, tool: dict, text: str) -> None:
    client.with_options(timeout=15.0).messages.create(
        model=model, max_tokens=256, messages=[{"role": "user", "content": text}],
        tools=[tool], tool_choice={"type": "tool", "name": tool["name"]},
        thinking={"type": "disabled"}, output_config={"effort": "low"})


def _steps() -> list[tuple[str, callable]]:
    from backend import page_reader, price_reader
    from backend.db import client as mongo
    from backend.services import item_understanding, verdict
    from backend.trends import get_trends_for_prompt

    claude_model = os.getenv("CLAUDE_MODEL")
    gemini_model = os.getenv("GEMINI_MODEL")
    steps = [("mongo", lambda: (mongo.admin.command("ping"), get_trends_for_prompt()))]
    if gemini_model:
        steps += [
            ("gemini", lambda: item_understanding._get_client().models.get(model=gemini_model)),
            ("gemini_read_page", lambda: page_reader._get_client().models.get(model=gemini_model)),
        ]
    if claude_model:
        steps += [
            ("claude_verdict_tool", lambda: _claude_tool(
                verdict.claude_client(), claude_model, verdict.VERDICT_TOOL,
                "Warm-up call. Return good_buy with the reason 'ok', no ids, no trend, confidence 1.")),
            ("claude_price_tool", lambda: _claude_tool(
                price_reader.claude_client(), claude_model, price_reader.PRICE_TOOL,
                "Warm-up call. Report price_text null and the reason 'ok'.")),
        ]
    return steps


def _run() -> None:
    started = time.monotonic()
    done = []
    for name, step in _steps():
        mark = time.monotonic()
        try:
            step()
            done.append(f"{name}={int((time.monotonic() - mark) * 1000)}")
        except Exception as e:
            done.append(f"{name}=failed")
            log.warning("warmup: %s failed: %s: %s", name, type(e).__name__, str(e)[:200])
    log.info("warmup done in %d ms: %s", int((time.monotonic() - started) * 1000), " ".join(done))


def start_warmup() -> None:
    if os.getenv("SECONDLOOK_WARMUP", "1") == "0":
        return
    threading.Thread(target=_run, name="warmup", daemon=True).start()
