"""Part 2 item understanding: Gemini turns one shop item into a structured attribute record.

attribute_string() is the single source of truth for the text we embed. Part 3 imports it.
"""
import io
import logging
import os

import httpx
from google import genai
from google.genai import types
from PIL import Image, ImageOps

from backend.settings import ATTRIBUTES

log = logging.getLogger("secondlook.item_understanding")

# ---------- schema, built from attributes.json at import time ----------

ENUM_FIELDS = list(ATTRIBUTES)  # category, color_family, fabric, cut, style
FABRIC_CONFIDENCE = ["high", "medium", "low"]
FALLBACK_VALUES = ("unknown", "other")  # the "not sure" values, if a list has one
DESCRIPTION_MAX_WORDS = 25
RESULT_FIELDS = ENUM_FIELDS + ["fabric_confidence", "description", "is_apparel"]


def _enum_schema(values: list[str]) -> dict:
    """Lists with an "unknown" or "other" value use it for "not sure". Lists without one allow null."""
    if any(v in values for v in FALLBACK_VALUES):
        return {"type": "string", "enum": values}
    return {"anyOf": [{"type": "string", "enum": values}, {"type": "null"}]}


RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "is_apparel": {"type": "boolean"},
        **{field: _enum_schema(values) for field, values in ATTRIBUTES.items()},
        "fabric_confidence": {"type": "string", "enum": FABRIC_CONFIDENCE},
        "description": {"type": "string"},
    },
    "required": ["is_apparel", *ENUM_FIELDS, "fabric_confidence", "description"],
}

PROMPT = """You label one product from an online clothing store for a shopping assistant.

Rules:
- Describe the MAIN garment or item only: the one the product name and breadcrumb refer to. Store photos
  often show a model in a full outfit. Find the item the name describes (for example "pants" means label
  the pants, even if a jacket is more visible) and ignore the other clothes, props, and background.
- is_apparel: true if the item is clothing, shoes, a bag, or a wearable accessory. false otherwise
  (furniture, electronics, beauty products, and so on). If false, set every other label to null or its
  "unknown"/"other" value and keep the description short.
- fabric: read it from the text hints first. fabric_confidence is "high" when the text states the material
  (for example "100% cotton", "wool blend", "genuine leather"), "medium" when the text only implies it (for
  example "denim jacket", "fleece", "linen-look"), and "low" when you guess from the image or have no hint.
  Use "blend" when the text lists two or more fibers with none at 80% or more.
- color_family: the dominant color of the item. Use "multi" only when no single color dominates.
- When you are unsure of a label, use "unknown" or "other" if the list has one, otherwise null.
- description: at most 25 words, plain and factual, about how the item looks (color, cut, details).
  Never invent or name a brand. Do not mention price, size, or the store.

Product name: {name}
Breadcrumb: {breadcrumb}
Store description: {description}
Other page text: {store_text}
Image: {image_note}"""

MAX_TEXT_CHARS = 4000
ATTEMPT_TIMEOUT_MS = 20000

# ---------- Gemini client ----------

_client = None


def _get_client():
    global _client
    if _client is None:
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        _client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=ATTEMPT_TIMEOUT_MS))
    return _client


# ---------- images ----------

IMAGE_MAX_SIDE = 1024
IMAGE_MAX_BYTES = 15 * 1024 * 1024
FETCH_TIMEOUT_S = 10
BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"),
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}


class ImageFetchError(Exception):
    pass


def resize_image(raw: bytes) -> bytes:
    """Resize so the longest side is at most 1024px and return JPEG bytes."""
    img = Image.open(io.BytesIO(raw))
    img = ImageOps.exif_transpose(img)
    img.thumbnail((IMAGE_MAX_SIDE, IMAGE_MAX_SIDE))
    if img.mode != "RGB":
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def fetch_image(url: str) -> bytes:
    """Download an image URL and return resized JPEG bytes. Raises ImageFetchError on any failure."""
    if not url.lower().startswith(("http://", "https://")):
        raise ImageFetchError("only http and https image URLs are supported")
    try:
        with httpx.Client(headers=BROWSER_HEADERS, timeout=FETCH_TIMEOUT_S, follow_redirects=True) as http:
            r = http.get(url)
    except httpx.HTTPError as e:
        raise ImageFetchError(f"request failed ({type(e).__name__})") from e
    if r.status_code != 200:
        raise ImageFetchError(f"HTTP {r.status_code}")
    content_type = r.headers.get("content-type", "").split(";")[0].strip().lower()
    if not content_type.startswith("image/"):
        raise ImageFetchError(f"not an image (content-type '{content_type or 'missing'}')")
    if len(r.content) > IMAGE_MAX_BYTES:
        raise ImageFetchError("image is larger than 15 MB")
    try:
        return resize_image(r.content)
    except Exception as e:
        raise ImageFetchError(f"unreadable image ({content_type})") from e


# ---------- extraction ----------

def failed_result() -> dict:
    return {**{field: None for field in RESULT_FIELDS}, "status": "attributes_failed"}


def _clean(raw: dict) -> dict:
    """Check the reply against attributes.json. Raises ValueError if the reply is malformed."""
    if not isinstance(raw, dict) or not isinstance(raw.get("is_apparel"), bool):
        raise ValueError("reply is not an object with a boolean is_apparel")
    result = {"is_apparel": raw["is_apparel"]}
    for field, allowed in ATTRIBUTES.items():
        value = raw.get(field)
        if value is not None and value not in allowed:
            raise ValueError(f"{field} '{value}' is not in attributes.json")
        result[field] = value
    confidence = raw.get("fabric_confidence")
    if confidence not in FABRIC_CONFIDENCE:
        raise ValueError(f"fabric_confidence '{confidence}' is not allowed")
    # A fabric of "unknown" or null is never more than a guess.
    result["fabric_confidence"] = "low" if result["fabric"] in (None, "unknown") else confidence
    description = raw.get("description")
    if not isinstance(description, str):
        raise ValueError("description is not a string")
    words = description.split()
    result["description"] = " ".join(words[:DESCRIPTION_MAX_WORDS]) or None
    result["status"] = "ok"
    return result


def extract_attributes(image_bytes: bytes | None, name: str, description: str | None = None,
                       breadcrumb: str | None = None, store_text: str | None = None) -> dict:
    """Return {category, color_family, fabric, fabric_confidence, cut, style, description, is_apparel, status}.

    image_bytes must be JPEG (fetch_image and resize_image return JPEG). Pass None for text only.
    On two failed attempts every field is None and status is "attributes_failed".
    """
    model = os.getenv("GEMINI_MODEL")
    if not model:
        log.error("GEMINI_MODEL is not set")
        return failed_result()
    prompt = PROMPT.format(
        name=name or "none",
        breadcrumb=breadcrumb or "none",
        description=(description or "none")[:MAX_TEXT_CHARS],
        store_text=(store_text or "none")[:MAX_TEXT_CHARS],
        image_note="attached" if image_bytes else "not available, use the text only",
    )
    contents = [prompt]
    if image_bytes:
        contents.insert(0, types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"))
    config = types.GenerateContentConfig(
        temperature=0, response_mime_type="application/json", response_json_schema=RESPONSE_SCHEMA)

    for attempt in (1, 2):
        try:
            response = _get_client().models.generate_content(model=model, contents=contents, config=config)
            return _clean(response.parsed)
        except ValueError as e:
            log.warning("attributes: malformed Gemini reply on attempt %s: %s", attempt, e)
        except Exception as e:
            log.warning("attributes: Gemini call failed on attempt %s: %s", attempt, str(e)[:300])
    return failed_result()


def understand_item(name: str, image_url: str | None = None, description: str | None = None,
                    breadcrumb: str | None = None, store_text: str | None = None) -> dict:
    """Fetch the image if there is one, then extract. A failed fetch falls back to text only."""
    image_bytes = None
    if image_url:
        try:
            image_bytes = fetch_image(image_url)
        except ImageFetchError as e:
            log.warning("attributes: image fetch failed, using text only: %s", e)
    return extract_attributes(image_bytes, name, description, breadcrumb, store_text)


# ---------- embedding text ----------

def attribute_string(attrs: dict) -> str:
    """The one template for embedding text: "{color_family} {cut} {fabric} {category}, {style} style. {description}".

    Null, "unknown" and "other" values are skipped with their words, because they carry no meaning
    to embed. Part 3 imports this, so change it only on purpose.
    """
    def known(key):
        value = attrs.get(key)
        return value if value and value not in FALLBACK_VALUES else None

    head = " ".join(known(k) for k in ("color_family", "cut", "fabric", "category") if known(k))
    style = f"{known('style')} style" if known("style") else ""
    first = ", ".join(p for p in (head, style) if p)
    parts = [first + "." if first else "", attrs.get("description") or ""]
    return " ".join(p for p in parts if p).strip()
