"""One structured-output Gemini call, shared by the Part 3 services. Uses Part 2's client and GEMINI_MODEL."""
import logging
import os
import time

from google.genai import types

from backend.services.item_understanding import _get_client

log = logging.getLogger("secondlook.gemini")


RETRY_WAIT_S = 2
BUSY_CODES = (429, 503)  # "high demand" and rate limits clear in seconds, so they get extra attempts
BUSY_ATTEMPTS = 4


class GeminiError(Exception):
    pass


def generate_json(contents: list, schema: dict, timeout_ms: int, label: str, attempts: int = 2) -> dict:
    """Return the parsed JSON object, or raise GeminiError.

    A malformed reply or a failed call is retried (attempts in total). A busy reply (429 or 503) gets up to
    4 attempts with 2, 4 and 8 second waits.
    """
    model = os.getenv("GEMINI_MODEL")
    if not model:
        raise GeminiError("GEMINI_MODEL is not set")
    config = types.GenerateContentConfig(
        temperature=0, response_mime_type="application/json", response_json_schema=schema,
        http_options=types.HttpOptions(timeout=timeout_ms))
    last, attempt, limit = "no attempt made", 0, attempts
    while attempt < limit:
        attempt += 1
        if attempt > 1:
            time.sleep(RETRY_WAIT_S * 2 ** (attempt - 2))  # 2 s, 4 s, 8 s
        try:
            response = _get_client().models.generate_content(model=model, contents=contents, config=config)
        except Exception as e:
            code = getattr(e, "code", None)
            last = f"call failed ({code or type(e).__name__})"
            log.warning("%s: Gemini %s on attempt %s: %s", label, last, attempt, str(e)[:300])
            if code in BUSY_CODES:
                limit = max(limit, BUSY_ATTEMPTS)
                last = "is busy right now (high demand). Try again in a minute"
            continue
        if isinstance(response.parsed, dict):
            return response.parsed
        last = "malformed reply"
        log.warning("%s: malformed Gemini reply on attempt %s", label, attempt)
    raise GeminiError(f"{label}: Gemini {last}")
