"""Tier 3 item reading: ask Gemini to pull the main product out of trimmed page text."""
import logging
import os
import time

from google import genai
from google.genai import types
from pydantic import BaseModel, ValidationError

from backend.services.gemini_json import BUSY_CODES, RETRY_WAIT_S

log = logging.getLogger("secondlook.read_page")

MAX_TEXT_CHARS = 8000
# The extension waits 15 seconds for the whole request. Gemini's minimum deadline is 10 seconds,
# so the retry only runs when the first attempt came back fast (worst case about 13 seconds).
ATTEMPT_TIMEOUT_MS = 10000
RETRY_IF_FIRST_UNDER_S = 3
# A busy reply (429 rate limit or 503 high demand) is retried after 2 s, then 4 s. Kept below the shared
# helper's 4 attempts because this call sits on the click path (the extension waits up to 15 s).
BUSY_ATTEMPTS = 3

PROMPT = """You read online store product pages.
From the page text below, extract the ONE main product the page is selling.
- name: the product name as the store shows it, without size, color, or store name. null if unclear.
- price: the current selling price as a plain number (use the sale price if there is one, the lower
  value of a range). null if no price is shown.
- image_url: the main product image URL if one is given below, otherwise null.
Do not invent values.

Page URL: {url}
Image URL candidate: {image_url}
Page text:
{text}"""


class PageItem(BaseModel):
    name: str | None = None
    price: float | None = None
    image_url: str | None = None


class ReadPageError(Exception):
    pass


_client = None


def _get_client():
    global _client
    if _client is None:
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise ReadPageError("GEMINI_API_KEY is not set")
        _client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=ATTEMPT_TIMEOUT_MS))
    return _client


def _generate(model: str, prompt: str, config):
    """One Gemini call, retried only while Gemini answers busy (429 or 503). Raises ReadPageError."""
    for attempt in range(1, BUSY_ATTEMPTS + 1):
        try:
            return _get_client().models.generate_content(model=model, contents=prompt, config=config)
        except Exception as e:
            code = getattr(e, "code", None)
            log.warning("read-page: Gemini call failed on attempt %s: %s", attempt, str(e)[:300])
            if code not in BUSY_CODES or attempt == BUSY_ATTEMPTS:
                raise ReadPageError(f"Gemini call failed ({code or type(e).__name__})") from e
            time.sleep(RETRY_WAIT_S * 2 ** (attempt - 1))  # 2 s, then 4 s


def read_page(url: str, text: str, image_url: str | None) -> PageItem:
    model = os.getenv("GEMINI_MODEL")
    if not model:
        raise ReadPageError("GEMINI_MODEL is not set")
    prompt = PROMPT.format(url=url, image_url=image_url or "none", text=text[:MAX_TEXT_CHARS])
    config = types.GenerateContentConfig(
        temperature=0, response_mime_type="application/json", response_schema=PageItem)

    # Retry once when the reply is malformed. Busy replies are retried inside _generate. Other network errors
    # and timeouts are not retried.
    started = time.monotonic()
    for attempt in (1, 2):
        response = _generate(model, prompt, config)
        try:
            item = response.parsed if isinstance(response.parsed, PageItem) else PageItem.model_validate_json(response.text or "")
        except (ValidationError, ValueError):
            log.warning("read-page: malformed Gemini reply on attempt %s", attempt)
            if time.monotonic() - started > RETRY_IF_FIRST_UNDER_S:
                break
            continue
        if item.price is not None and item.price < 0:
            item.price = None
        if image_url:
            item.image_url = item.image_url or image_url
        elif item.image_url and item.image_url not in text:
            item.image_url = None  # only keep URLs that came from the page
        return item
    raise ReadPageError("Gemini returned a malformed reply")
