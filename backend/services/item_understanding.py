"""Part 2 item understanding: Gemini turns one shop item into a structured attribute record.

attribute_string() is the single source of truth for the text we embed. Part 3 imports it.

Store photos often show a model in a full outfit. Gemini labels only the target item (the one the name
describes) and returns a box around it. A name-first check in code catches the case where Gemini labeled
another garment: see understand_target_item().
"""
import base64
import io
import logging
import math
import os
import re

import httpx
from google import genai
from google.genai import types
from PIL import Image, ImageOps

from backend.images import to_data_url
from backend.settings import ATTRIBUTES

log = logging.getLogger("secondlook.item_understanding")

# ---------- schema, built from attributes.json at import time ----------

ENUM_FIELDS = list(ATTRIBUTES)  # category, color_family, fabric, cut, style
FABRIC_CONFIDENCE = ["high", "medium", "low"]
FALLBACK_VALUES = ("unknown", "other")  # the "not sure" values, if a list has one
DESCRIPTION_MAX_WORDS = 25
RESULT_FIELDS = ENUM_FIELDS + ["fabric_confidence", "description", "is_apparel"]
TARGET_FIELDS = ["target_box_2d", "other_garments_visible", "category_check", "target_crop"]
CATEGORY_CHECKS = ("agreed", "fixed_by_crop", "fixed_by_text", "forced_from_name", "no_name_category")


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
        "target_box_2d": {"anyOf": [
            {"type": "array", "items": {"type": "integer"}, "minItems": 4, "maxItems": 4}, {"type": "null"}]},
        "other_garments_visible": {"type": "boolean"},
    },
    "required": ["is_apparel", *ENUM_FIELDS, "fabric_confidence", "description",
                 "target_box_2d", "other_garments_visible"],
}

PROMPT = """You label one product from an online clothing store for a shopping assistant.

The target item is the exact product the shopper is adding to the cart. Its product name, breadcrumb and
store description are at the end of this message.

Rules:
- Label ONLY the target item. Store photos often show a model in a full outfit. Use the product name,
  breadcrumb and description to find the target garment in the image (for example "pants" means label the
  pants, even if a jacket or top is more visible). Ignore everything else in the image: the other clothes
  the model wears, shoes, bags, jewelry, props and the background. Never take a color, cut, fabric, style
  or description detail from any garment other than the target item.
- target_box_2d: [ymin, xmin, ymax, xmax] around the target item only, as integers normalized 0 to 1000.
  null when there is no image or the target item is not visible in it.
- other_garments_visible: true when the image shows any clothing, shoes or bags besides the target item.
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
    """Download an image URL and return resized JPEG bytes. Raises ImageFetchError on any failure.

    Also accepts a data:image/... URL (photos texted to the iMessage agent arrive as bytes, not links).
    """
    if url.lower().startswith("data:image/"):
        try:
            raw = base64.b64decode(url.split(",", 1)[1])
        except Exception as e:
            raise ImageFetchError("data URL is not valid base64") from e
        if len(raw) > IMAGE_MAX_BYTES:
            raise ImageFetchError("image is larger than 15 MB")
        try:
            return resize_image(raw)
        except Exception as e:
            raise ImageFetchError("unreadable image in data URL") from e
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


# ---------- target box and crop ----------

BOX_MAX = 1000
MIN_BOX_SIDE = 30  # 3% of the image side. A smaller box is noise, not a garment
CROP_PAD = 0.05  # widen the crop by 5% of the box on each side, so the edges of the garment are kept


def valid_box(box) -> list[int] | None:
    """Return the box as 4 ints if it is [ymin, xmin, ymax, xmax] in 0..1000, ordered and not tiny. Else None."""
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in box):
        return None
    ymin, xmin, ymax, xmax = (round(v) for v in box)
    if not all(0 <= v <= BOX_MAX for v in (ymin, xmin, ymax, xmax)):
        return None
    if ymax - ymin < MIN_BOX_SIDE or xmax - xmin < MIN_BOX_SIDE:
        return None  # also rejects reversed boxes
    return [ymin, xmin, ymax, xmax]


def box_to_pixels(box: list[int], width: int, height: int, pad: float = CROP_PAD) -> tuple[int, int, int, int]:
    """Map a valid 0..1000 box to a padded Pillow crop area (left, top, right, bottom), clamped to the image."""
    ymin, xmin, ymax, xmax = box
    top, bottom = ymin / BOX_MAX * height, ymax / BOX_MAX * height
    left, right = xmin / BOX_MAX * width, xmax / BOX_MAX * width
    pad_y, pad_x = (bottom - top) * pad, (right - left) * pad
    return (max(0, math.floor(left - pad_x)), max(0, math.floor(top - pad_y)),
            min(width, math.ceil(right + pad_x)), min(height, math.ceil(bottom + pad_y)))


def crop_to_box(jpeg: bytes, box) -> bytes | None:
    """Crop JPEG bytes to the box and return JPEG bytes, or None when the box is invalid."""
    box = valid_box(box)
    if box is None:
        return None
    img = Image.open(io.BytesIO(jpeg))
    img = img.crop(box_to_pixels(box, *img.size))
    if img.mode != "RGB":
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


# ---------- name-first category check ----------

# Keywords per category, singular and lower case. Multi-word phrases match whole words in order. The table
# must cover every category in attributes.json (checked at import time). Only unambiguous words are listed:
# "top", "pullover", "sweatshirt" or "crop top" do not say which category a garment is.
CATEGORY_KEYWORDS = {
    "tee": ["tee", "t shirt", "tshirt", "tee shirt", "tank", "tank top", "muscle tee"],
    "shirt": ["shirt", "blouse", "button up", "button down", "buttondown", "polo", "flannel", "dress shirt"],
    "sweater": ["sweater", "cardigan", "sweater vest"],
    "hoodie": ["hoodie", "hoody", "hooded sweatshirt"],
    "jacket": ["jacket", "bomber", "blazer", "windbreaker", "anorak", "shacket", "jean jacket", "denim jacket"],
    "coat": ["coat", "parka", "trench", "overcoat", "peacoat", "topcoat"],
    "jeans": ["jean", "denim", "denim pant"],
    "pants": ["pant", "trouser", "cargo", "jogger", "sweatpant", "chino", "slack", "legging", "track pant"],
    "shorts": ["short", "boardshort", "bermuda"],
    "skirt": ["skirt", "miniskirt"],
    "dress": ["dress", "gown", "sundress", "minidress", "maxi dress", "midi dress", "mini dress"],
    "shoes": ["shoe", "sneaker", "boot", "loafer", "sandal", "heel", "mule", "clog", "trainer", "slipper",
              "ballet flat"],
    "bag": ["bag", "tote", "backpack", "purse", "handbag", "clutch", "crossbody"],
    "accessory": ["accessory", "hat", "cap", "beanie", "belt", "scarf", "sunglass", "necklace", "bracelet",
                  "earring", "watch", "glove", "sock", "wallet"],
}
# Phrases that use a category word as a modifier. They are matched like keywords and point to no category.
NOT_A_GARMENT = ["short sleeve", "short sleeved", "cap sleeve", "cap sleeved", "boot cut", "boot leg",
                 "tee length"]

if set(CATEGORY_KEYWORDS) != set(ATTRIBUTES["category"]):
    raise RuntimeError("CATEGORY_KEYWORDS must have exactly the categories in attributes.json")

_PHRASES = sorted(
    [(tuple(k.split()), cat) for cat, words in CATEGORY_KEYWORDS.items() for k in words]
    + [(tuple(k.split()), None) for k in NOT_A_GARMENT],
    key=lambda p: -len(p[0]))  # longest phrase first, so "t shirt" wins over "shirt"
_TRAILING_FEATURE = re.compile(r"\s(?:with|w/)\s.*$", re.IGNORECASE)  # "Shorts with Belt": belt is a detail
_SEGMENT_SPLIT = re.compile(r"\band\b|&|\+|,|;", re.IGNORECASE)  # "Tee and Shorts Set": two garments
_CRUMB_SPLIT = re.compile(r"[>/|\\›»]")


def _forms(token: str) -> set[str]:
    """The token and its singular guesses: "jeans" -> {jeans, jean}, "dresses" -> {dresses, dress}."""
    forms = {token}
    if token.endswith("ies") and len(token) > 4:
        forms.add(token[:-3] + "y")
    if token.endswith("es") and len(token) > 3:
        forms.add(token[:-2])
    if token.endswith("s") and len(token) > 2:
        forms.add(token[:-1])
    return forms


def _tokens(text: str) -> list[set[str]]:
    return [_forms(t) for t in re.sub(r"[^a-z0-9]+", " ", text.lower()).split()]


def _matches_at(tokens: list[set[str]], i: int, words: tuple[str, ...]) -> bool:
    return i + len(words) <= len(tokens) and all(w in tokens[i + k] for k, w in enumerate(words))


def _head_category(text: str) -> str | None:
    """Category of the head noun: the last keyword match in the text. "Denim Jacket" -> jacket."""
    tokens, i, head = _tokens(text), 0, None
    while i < len(tokens):
        for words, category in _PHRASES:
            if _matches_at(tokens, i, words):
                head = category or head
                i += len(words)
                break
        else:
            i += 1
    return head


def text_category(text: str | None) -> str | None:
    """Map a product name to one category, or None when it names no category or several garments."""
    if not text:
        return None
    text = _TRAILING_FEATURE.sub("", text)
    found = {c for c in (_head_category(seg) for seg in _SEGMENT_SPLIT.split(text)) if c}
    return found.pop() if len(found) == 1 else None


def name_category(name: str | None, breadcrumb: str | None = None) -> str | None:
    """The category the store text gives: the name first, else the most specific breadcrumb level."""
    category = text_category(name)
    if category or not breadcrumb:
        return category
    for crumb in reversed(_CRUMB_SPLIT.split(breadcrumb)):
        if category := text_category(crumb):
            return category
    return None


# ---------- attributes the store text supports ----------

# Words that state a value in store text. Each value also matches its own name.
TEXT_SYNONYMS = {
    "color_family": {
        "gray": ["grey", "charcoal", "heather"], "beige": ["tan", "khaki", "camel", "sand", "taupe", "oatmeal"],
        "brown": ["chocolate", "mocha", "espresso"], "red": ["burgundy", "maroon", "wine"],
        "blue": ["navy", "indigo"], "green": ["olive", "sage"], "purple": ["lavender", "lilac", "violet", "plum"],
        "white": ["ivory", "off white"], "pink": ["blush", "fuchsia"], "multi": ["multicolor", "multicolour"]},
    "cut": {"cropped": ["crop"], "fitted": ["bodycon"], "oversized": ["oversize"], "slim": ["skinny"]},
    "fabric": {"wool": ["merino", "cashmere"], "leather": ["suede"]},
    "style": {},
}


def text_values(field: str, text: str) -> set[str]:
    """Values of one field that the text states, for example {"black"} for "Washed Black Sweatpants"."""
    tokens = _tokens(text)
    synonyms = TEXT_SYNONYMS.get(field, {})
    return {value for value in ATTRIBUTES[field] if value not in FALLBACK_VALUES
            and any(_matches_at(tokens, i, tuple(p.split()))
                    for p in [value, *synonyms.get(value, [])] for i in range(len(tokens)))}


def _fallback(field: str) -> str | None:
    return next((v for v in ATTRIBUTES[field] if v in FALLBACK_VALUES), None)


def force_from_name(result: dict, category: str, text: str) -> dict:
    """Keep the category from the name and only what the store text supports.

    Gemini labeled another garment, so its image-based labels describe the wrong item. color_family, cut,
    style and fabric keep Gemini's value only when the text states it, else take the one value the text
    states, else become null (or "unknown"/"other"). The description is dropped: it describes the wrong
    garment.
    """
    out = {**result, "category": category, "is_apparel": True, "description": None}
    for field in ("color_family", "cut", "style", "fabric"):
        stated = text_values(field, text)
        if result.get(field) in stated:
            out[field] = result[field]
        elif len(stated) == 1:
            out[field] = next(iter(stated))
        else:
            out[field] = _fallback(field)
    if out["fabric"] in (None, "unknown"):
        out["fabric_confidence"] = "low"
    elif out["fabric"] != result.get("fabric") or result.get("fabric_confidence") == "low":
        out["fabric_confidence"] = "medium"  # stated by the text, which may be the name only
    return out


# ---------- extraction ----------

def failed_result() -> dict:
    return {**{field: None for field in RESULT_FIELDS + TARGET_FIELDS}, "status": "attributes_failed"}


def _clean(raw: dict) -> dict:
    """Check the reply against attributes.json. Raises ValueError if the reply is malformed.

    A bad target_box_2d or other_garments_visible becomes None instead of failing the reply.
    """
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
    box = raw.get("target_box_2d")
    result["target_box_2d"] = valid_box(box)
    if box is not None and result["target_box_2d"] is None:
        log.info("attributes: invalid target_box_2d %s set to null", str(box)[:80])
    others = raw.get("other_garments_visible")
    result["other_garments_visible"] = others if isinstance(others, bool) else None
    result["status"] = "ok"
    return result


def extract_attributes(image_bytes: bytes | None, name: str, description: str | None = None,
                       breadcrumb: str | None = None, store_text: str | None = None,
                       cropped: bool = False) -> dict:
    """One Gemini read: {category, color_family, fabric, fabric_confidence, cut, style, description, is_apparel,
    target_box_2d, other_garments_visible, status}. No name check: understand_target_item() adds it.

    image_bytes must be JPEG (fetch_image and resize_image return JPEG). Pass None for text only.
    cropped=True tells Gemini the image is already a crop around the target item.
    On two failed attempts every field is None and status is "attributes_failed".
    """
    model = os.getenv("GEMINI_MODEL")
    if not model:
        log.error("GEMINI_MODEL is not set")
        return failed_result()
    if not image_bytes:
        image_note = "not available, use the text only"
    else:
        image_note = "attached, already cropped around the target item" if cropped else "attached"
    prompt = PROMPT.format(
        name=name or "none",
        breadcrumb=breadcrumb or "none",
        description=(description or "none")[:MAX_TEXT_CHARS],
        store_text=(store_text or "none")[:MAX_TEXT_CHARS],
        image_note=image_note,
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


def analyze_target(image_bytes: bytes | None, name: str, description: str | None = None,
                   breadcrumb: str | None = None, store_text: str | None = None, extract=None) -> dict:
    """Attributes of the target item only, with the name-first category check.

    1. Gemini labels the target item and returns target_box_2d.
    2. If the name (or breadcrumb) gives exactly one category and Gemini disagrees, ask once more on the crop
       of target_box_2d (text only when there is no box; no retry when the first read was already text only).
    3. If Gemini still disagrees, the name wins: force_from_name() keeps only what the store text supports.
    category_check says which path ran. target_crop is a JPEG data URL (600px longest side at most) of the
    target item, or None when there is no valid box or the box is not trusted (forced_from_name).
    extract is the Gemini call (extract_attributes). Tests pass a fake.
    """
    extract = extract or extract_attributes
    first = extract(image_bytes, name, description, breadcrumb, store_text)
    if first.get("status") != "ok":
        return failed_result()
    crop = crop_to_box(image_bytes, first.get("target_box_2d")) if image_bytes else None
    expected = name_category(name, breadcrumb)
    result, check = first, "agreed"
    if expected is None:
        check = "no_name_category"
    elif first.get("category") != expected:
        log.warning("attributes: category conflict for %r: name says %s, Gemini says %s",
                    name[:80], expected, first.get("category"))
        retry = None
        if crop:
            retry = extract(crop, name, description, breadcrumb, store_text, cropped=True)
        elif image_bytes:
            retry = extract(None, name, description, breadcrumb, store_text)
        if retry and retry.get("status") == "ok" and retry.get("category") == expected:
            check = "fixed_by_crop" if crop else "fixed_by_text"
            # The box and other_garments_visible describe the full image, so they come from the first read.
            result = {**retry, "target_box_2d": first.get("target_box_2d"),
                      "other_garments_visible": first.get("other_garments_visible")}
        else:
            check = "forced_from_name"
            text = " ".join(t for t in (name, breadcrumb, description) if t)
            result = {**force_from_name(first, expected, text), "target_box_2d": None}
            crop = None  # the box framed the wrong garment
            log.warning("attributes: category forced from name for %r: %s (Gemini said %s, then %s)",
                        name[:80], expected, first.get("category"), (retry or {}).get("category"))
    return {**result, "category_check": check, "target_crop": to_data_url(crop) if crop else None}


def understand_target_item(name: str, image_url: str | None = None, description: str | None = None,
                           breadcrumb: str | None = None, store_text: str | None = None) -> dict:
    """Part 5 entry point. Fetch the image if there is one, then analyze_target(). A failed fetch runs text only.

    Returns the attributes plus target_box_2d, other_garments_visible, category_check and target_crop.
    Store target_crop on the verdict doc, so the verdict step can show Claude only the target garment.
    """
    image_bytes = None
    if image_url:
        try:
            image_bytes = fetch_image(image_url)
        except ImageFetchError as e:
            log.warning("attributes: image fetch failed, using text only: %s", e)
    return analyze_target(image_bytes, name, description, breadcrumb, store_text)


# The earlier name, kept for existing callers (the Buy anyway auto-add in services/closet.py).
understand_item = understand_target_item


def verdict_doc_fields(understood: dict) -> dict:
    """The Part 2 fields for a stored verdict doc: attributes (null on failure) and target_crop."""
    if understood.get("status") != "ok":
        return {"attributes": None, "target_crop": None}
    return {"attributes": {f: understood.get(f) for f in ENUM_FIELDS + ["fabric_confidence"]},
            "target_crop": understood.get("target_crop")}


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
