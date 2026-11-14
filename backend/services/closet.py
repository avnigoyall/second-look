"""Part 3 closet: building closet documents, vector matching, and the look-match rule.

Every closet item stores `embedding` = embed_text(attribute_string(attributes + description)), so items
without photos can still be matched. Items with a photo also store `image_embedding` (not indexed).

Items added by hand (quick add, ownership answer, iMessage "yes") have no description, so their embedding
text is attributes only. A shopped item always has a description, and a full-text vector scores low against
an attributes-only vector (0.64 for the same black shirt). Each closet item therefore stores
`has_description`, and find_matches() compares like with like: the full shopped text against items with a
description, the attributes-only shopped text against items without one.
"""
import base64
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from backend.db import closet_items
from backend.services.embeddings import EmbeddingError, embed_image, embed_text
from backend.services.item_understanding import attribute_string
from backend.settings import ATTRIBUTES, ROOT

log = logging.getLogger("secondlook.closet")

VECTOR_INDEX = "closet_vector"
NUM_CANDIDATES = 50
THRESHOLDS_PATH = ROOT / "backend" / "config" / "thresholds.json"
ATTRIBUTE_FIELDS = (*ATTRIBUTES, "fabric_confidence")  # what is stored under closet_items.attributes
# Projection for every read that goes back to a client: never send vectors.
NO_VECTORS = {"embedding": 0, "image_embedding": 0}


def thresholds() -> dict:
    """Read on every call so a tuning change applies without a server restart."""
    return json.loads(THRESHOLDS_PATH.read_text())


def clean_attributes(attrs: dict | None) -> dict:
    """Keep only the attribute fields. Values are already checked by the Pydantic models or the LLM schema."""
    return {k: attrs.get(k) for k in ATTRIBUTE_FIELDS} if attrs else {}


def item_name(attrs: dict) -> str:
    """"<color_family> <category>", for example "White tee". Unknown parts are left out."""
    color, category = attrs.get("color_family"), attrs.get("category")
    color = "multicolor" if color == "multi" else color
    text = " ".join(w for w in (color, category) if w) or "closet item"
    return text[:1].upper() + text[1:]


def embedding_text(attrs: dict, description: str | None = None, name: str | None = None) -> str:
    """attribute_string from Part 2. Falls back to the item name only when there are no attributes at all."""
    text = attribute_string({**(attrs or {}), "description": description or ""})
    return text or (name or "").strip()


def has_description(description: str | None) -> bool:
    """The `has_description` flag stored on closet docs. It decides which query vector the item is compared to."""
    return bool((description or "").strip())


def new_closet_doc(user_id: ObjectId, *, name: str, attributes: dict | None, source: str,
                   status: str = "owned", image_data: str | None = None, description: str = "",
                   extra: dict | None = None) -> dict:
    """Build a closet_items document with its embeddings. Raises EmbeddingError if the text embedding fails."""
    attrs = clean_attributes(attributes)
    text = embedding_text(attrs, description, name)
    doc = {
        "user_id": user_id,
        "source": source,
        "status": status,
        "name": name,
        "image_data": image_data,
        "attributes": attrs or None,
        "description": description or "",
        "has_description": has_description(description),
        "embedding": embed_text(text) if text else None,
        "image_embedding": None,
        "created_at": datetime.now(timezone.utc),
        **(extra or {}),
    }
    if image_data:
        try:
            doc["image_embedding"] = embed_image(base64.b64decode(image_data.split(",", 1)[-1]))
        except EmbeddingError as e:
            log.warning("image embedding skipped: %s", e)  # optional, so a failure does not block the save
    return doc


def vector_search(user_id: ObjectId, vector: list[float], category: str | None, k: int = 5,
                  described: bool | None = None) -> list[dict]:
    """Raw $vectorSearch over this user's closet. Returns documents with a `score` and no vectors or images.

    described: True keeps items with a description (and older items without the flag), False keeps items
    without one, None keeps all.
    """
    filt = {"user_id": user_id}
    if category:
        filt["attributes.category"] = category
    if described is True:
        filt["has_description"] = {"$ne": False}  # a doc stored before the flag existed counts as described
    elif described is False:
        filt["has_description"] = False
    pipeline = [
        {"$vectorSearch": {"index": VECTOR_INDEX, "path": "embedding", "queryVector": vector,
                           "numCandidates": max(NUM_CANDIDATES, k), "limit": k, "filter": filt}},
        {"$project": {"_id": 1, "name": 1, "attributes": 1, "status": 1,
                      "score": {"$meta": "vectorSearchScore"}}},
    ]
    return list(closet_items.aggregate(pipeline))


def query_texts(attrs: dict) -> tuple[str, str]:
    """(full text, attributes-only text) for an item being compared to the closet. Equal when there is no description."""
    return attribute_string(attrs), attribute_string({**attrs, "description": ""})


def search_closet(user_id: ObjectId, full_vector: list[float], attr_vector: list[float], category: str | None,
                  k: int = 5) -> list[dict]:
    """Two $vectorSearch calls in parallel, each comparing like with like, merged best first.

    full_vector is compared to items with a description, attr_vector to items without one. The two filters
    are disjoint, so the merge only sorts. Returns raw docs with the Atlas `score`.
    """
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_full = pool.submit(vector_search, user_id, full_vector, category, k, True)
        f_attr = pool.submit(vector_search, user_id, attr_vector, category, k, False)
        docs = f_full.result() + f_attr.result()
    return sorted(docs, key=lambda d: -d["score"])[:k]


def embed_query(attrs: dict) -> tuple[list[float], list[float]] | None:
    """(full vector, attributes-only vector), embedded in parallel. One call when the texts are equal. None if empty."""
    full, attr_only = query_texts(attrs)
    if not full:
        return None
    if not attr_only or attr_only == full:
        vector = embed_text(full)
        return vector, vector
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_full, f_attr = pool.submit(embed_text, full), pool.submit(embed_text, attr_only)
        return f_full.result(), f_attr.result()


def atlas_to_cosine(score: float) -> float:
    """Atlas reports cosine as (1 + cos) / 2. Convert back to plain cosine."""
    return round(2 * score - 1, 4)


def find_matches(user_id: ObjectId, attrs: dict, k: int = 5) -> list[dict]:
    """Closet items closest to `attrs` (same user, same category). Never returns image_data or embedding.

    Returns [{score, id, name, attributes, status}], best first. score is cosine similarity (-1 to 1), the
    same number scripts/tune_matching.py prints, so thresholds.json means one thing everywhere. Items without
    a description are scored against the attributes-only text of `attrs` (see the module docstring).
    """
    vectors = embed_query(attrs)
    if vectors is None:
        return []
    docs = search_closet(user_id, *vectors, attrs.get("category"), k)
    return [{"score": atlas_to_cosine(d["score"]), "id": str(d["_id"]), "name": d["name"],
             "attributes": d.get("attributes"), "status": d.get("status", "owned")} for d in docs]


def is_look_match(match: dict, attrs: dict) -> bool:
    """True when a find_matches() result counts as "you already have this look"."""
    t = thresholds()
    if match.get("score", 0) < t["look_similarity"]:
        return False
    if t.get("require_color_match"):
        mine, theirs = attrs.get("color_family"), (match.get("attributes") or {}).get("color_family")
        if mine and theirs and mine != theirs:
            return False
    return True


# ---------- Buy anyway auto-add ----------

def auto_add_purchase(verdict_id: ObjectId) -> None:
    """Background task after POST /action "bought": add the item as an incoming closet item. Silent by design.

    Name and attributes come from the stored verdict. If the verdict has no attributes yet (the Part 0 stub
    stores none), Part 2 reads them so the item can still be matched. The product image, if any, becomes the
    thumbnail. Runs once per verdict.
    """
    from backend.db import users, verdicts  # local import: db is the only shared state
    from backend.images import to_data_url
    from backend.services.item_understanding import ImageFetchError, fetch_image, understand_item

    verdict = verdicts.find_one({"_id": verdict_id})
    if verdict is None or closet_items.find_one({"source_verdict_id": verdict_id}, {"_id": 1}):
        return
    user = users.find_one({"_id": verdict["user_id"]}, {"settings": 1})
    if user is None or (user.get("settings") or {}).get("auto_add_purchases") is False:
        return
    item = verdict.get("item") or {}
    attrs = clean_attributes(verdict.get("attributes"))
    description = (verdict.get("attributes") or {}).get("description") or ""
    if not attrs.get("category"):
        understood = understand_item(item.get("name") or "", item.get("image_url"))
        if understood.get("status") == "ok" and understood.get("is_apparel") is not False:
            attrs, description = clean_attributes(understood), understood.get("description") or ""
    image_data = verdict.get("thumbnail")
    if not image_data and item.get("image_url"):
        try:
            image_data = to_data_url(fetch_image(item["image_url"]))
        except (ImageFetchError, OSError) as e:
            log.info("auto-add: no thumbnail (%s)", e)
    try:
        doc = new_closet_doc(verdict["user_id"], name=item.get("name") or item_name(attrs), attributes=attrs,
                             source="auto_add", status="incoming", image_data=image_data,
                             description=description, extra={"source_verdict_id": verdict_id})
        closet_items.insert_one(doc)
    except DuplicateKeyError:
        pass  # a second "bought" click raced this one; the unique index kept one item
    except Exception as e:
        log.warning("auto-add failed for verdict %s: %s", verdict_id, str(e)[:300])
