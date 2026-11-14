"""Tier 3 item reading: ask Gemini to pull the main product out of trimmed page text."""
import logging
import os
import time

from google import genai
from google.genai import types
from pydantic import BaseModel, ValidationError

log = logging.getLogger("secondlook.read_page")

MAX_TEXT_CHARS = 8000
# The extension waits 15 seconds for the whole request. Gemini's minimum deadline is 10 seconds,
# so the retry only runs when the first attempt came back fast (worst case about 13 seconds).
ATTEMPT_TIMEOUT_MS = 10000
RETRY_IF_FIRST_UNDER_S = 3

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


def read_page(url: str, text: str, image_url: str | None) -> PageItem:
    model = os.getenv("GEMINI_MODEL")
    if not model:
        raise ReadPageError("GEMINI_MODEL is not set")
    prompt = PROMPT.format(url=url, image_url=image_url or "none", text=text[:MAX_TEXT_CHARS])
    config = types.GenerateContentConfig(
        temperature=0, response_mime_type="application/json", response_schema=PageItem)

    # Retry once, only when the reply is malformed. Network errors and timeouts are not retried.
    started = time.monotonic()
    for attempt in (1, 2):
        try:
            response = _get_client().models.generate_content(model=model, contents=prompt, config=config)
        except Exception as e:
            log.warning("read-page: Gemini call failed: %s", str(e)[:300])
            code = getattr(e, "code", None)
            raise ReadPageError(f"Gemini call failed ({code or type(e).__name__})") from e
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
