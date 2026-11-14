"""Check that every external service key works. Prints one PASS or FAIL line per check.

Run from the project root:  python scripts/check_keys.py
Never prints a key or the MongoDB connection string.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

NESSIE_URL = "https://v2-prod-api.nessieisreal.com"
TIMEOUT_S = 30  # per call, so one stuck service cannot hang the whole check


def report(name: str, ok: bool, detail: str = ""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}{'  - ' + detail if detail else ''}", flush=True)


def short_error(e: Exception) -> str:
    """Error type plus a trimmed message, with any key values scrubbed out."""
    msg = str(e).splitlines()[0][:160] if str(e) else ""
    for var in ("MONGODB_URI", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "NESSIE_API_KEY"):
        secret = os.getenv(var)
        if secret:
            msg = msg.replace(secret, "***")
    return f"{type(e).__name__}: {msg}"


def check_mongo():
    uri = os.getenv("MONGODB_URI")
    if not uri:
        return report("MongoDB", False, "MONGODB_URI is not set")
    try:
        from pymongo import MongoClient
        MongoClient(uri, serverSelectionTimeoutMS=5000).admin.command("ping")
        report("MongoDB", True, "ping ok")
    except Exception as e:
        report("MongoDB", False, short_error(e))


def check_claude():
    key, model = os.getenv("ANTHROPIC_API_KEY"), os.getenv("CLAUDE_MODEL", "")
    if not key:
        return report("Claude", False, "ANTHROPIC_API_KEY is not set")
    if not model:
        return report("Claude", False, "CLAUDE_MODEL is not set. Set it to a small, cheap model id in .env")
    try:
        import anthropic
        anthropic.Anthropic(api_key=key, timeout=TIMEOUT_S, max_retries=0).messages.create(
            model=model, max_tokens=5, messages=[{"role": "user", "content": "Reply with OK."}])
        report("Claude", True, f"{model} replied")
    except Exception as e:
        report("Claude", False, short_error(e))


def check_gemini():
    key, model = os.getenv("GEMINI_API_KEY"), os.getenv("GEMINI_MODEL", "")
    if not key or not model:
        missing = "GEMINI_API_KEY" if not key else "GEMINI_MODEL"
        report("Gemini (a) generate", False, f"{missing} is not set")
        report("Gemini (b) search grounding", False, f"{missing} is not set")
        return
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=TIMEOUT_S * 1000))

    try:
        client.models.generate_content(model=model, contents="Reply with OK.")
        report("Gemini (a) generate", True, model)
    except Exception as e:
        report("Gemini (a) generate", False, short_error(e))

    try:
        resp = client.models.generate_content(
            model=model,
            contents="What is today's date? One short sentence.",
            config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())]),
        )
        grounded = bool(resp.candidates and resp.candidates[0].grounding_metadata)
        report("Gemini (b) search grounding", True,
               "grounding metadata returned" if grounded else "call ok, but no grounding metadata in reply")
    except Exception as e:
        report("Gemini (b) search grounding", False, short_error(e))


def check_nessie():
    key = os.getenv("NESSIE_API_KEY")
    if not key:
        return report("Nessie", False, "NESSIE_API_KEY is not set")
    try:
        import httpx
        r = httpx.get(f"{NESSIE_URL}/accounts", params={"limit": 1},
                      headers={"Authorization": f"Bearer {key}"}, timeout=TIMEOUT_S)
        report("Nessie", r.status_code == 200, f"GET /accounts -> HTTP {r.status_code}")
    except Exception as e:
        report("Nessie", False, short_error(e))


if __name__ == "__main__":
    check_mongo()
    check_claude()
    check_gemini()
    check_nessie()
