"""Full analysis: the second step after a verdict (POST /analyze/details).

Built from the stored verdict document, so it adds no Gemini attributes call. Parts:
- materials: composition parsed from the page text (Item.materials), else the Part 2 fabric attribute;
- health notes: only from config/material_health.json (sourced), never from a model;
- durability, lifespan and trend note: one Claude call (estimates, labeled as such on the card);
- cost per wear: price / expected wears, rated with thresholds.json (cost_per_wear_good / _poor);
- closet sentiment and 3 outfits, skipped when the closet is empty:
    good_buy -> outfits pair the NEW item with closet pieces;
    have_this_look / micro_trend -> outfits are built around the closet lookalike instead, not the new item;
- alternatives: general-purpose picks with store search links, only when cost efficiency is poor.
Never raises for model, embedding or Atlas failures: what failed is listed in `errors`.
"""
import json
import logging
import time
from pathlib import Path

from bson import ObjectId

from backend import models as m
from backend.alternatives import search_link
from backend.db import closet_items, trends, verdicts
from backend.services.closet import atlas_to_cosine, thresholds, vector_search
from backend.services.composition import fibers_from_fabric, parse_composition
from backend.services.embeddings import embed_text
from backend.services.item_understanding import attribute_string
from backend.services.verdict import claude_client, claude_model, expected_wears_for

log = logging.getLogger("secondlook.details")

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
HEALTH = json.loads((CONFIG_DIR / "material_health.json").read_text())
CANDIDATE_K = 8
MAX_OUTFITS = 3
MAX_ALTERNATIVES = 2
MAX_HEALTH_NOTES = 3
CLAUDE_TIMEOUTS_S = (10.0, 6.0)
CLAUDE_MAX_TOKENS = 1500
RECOMMENDED = "good_buy"

SYSTEM_PROMPT = """You are Second Look's stylist and fabric analyst. A shopper checked one clothing item and already
got a verdict. Give the deeper analysis behind it.

You get JSON: item (name, price, store, description, materials text), item_attributes, fibers (parsed
composition, may be empty), verdict, verdict_reason, lookalike_ids (closet items that already give this look),
trend (a matched micro trend or null), and closet_candidates (the shopper's closet pieces closest to this item,
any category, with id, name, category, color, cut, style, score; score near 0.5 means unrelated).

Fill the give_details tool:
- durability: pilling_risk from the fibers and construction (synthetic-heavy knits and loose blends pill
  sooner; long-staple cotton, wool blends and dense weaves less). note: one plain sentence on how it will hold
  up. expected_wears: realistic number of wears before it looks worn, for this fabric and garment type.
  expected_months: how many months the shopper will likely keep wearing it, considering both wear-out and, if a
  trend is given, how fast that trend fades. These are estimates; be realistic, not generous.
- trend_note: only if trend is not null. One hedged sentence on longevity ("likely to feel dated soon").
  Never state a number of weeks or months for the trend unless trend.lifespan_label gives it; then name that
  source. null when trend is null.
- similar_ids: ids from closet_candidates with the same feel as the item: close color and could be styled the
  same way, so the shopper would not need both (example: a white tank already covers a white tube top). Only
  real matches; an empty list is fine.
- outfits: up to 3, each a short name and closet_item_ids (ids from closet_candidates or lookalike_ids only).
  If verdict is "good_buy": each outfit uses the new item plus 1 to 3 closet pieces that pair well with it
  (set uses_new_item true). Otherwise: build each outfit around a piece from lookalike_ids or similar_ids
  instead of the new item (uses_new_item false), to show the shopper they already have the look.
  If closet_candidates is empty or nothing pairs, return an empty list.
- alternatives: up to 2 general-purpose pieces that give a similar use with more wear, e.g. "plain cotton crew
  tee". name: a short generic description. search_query: 2 to 6 words to search a store. No brands, no links.

Write to the shopper ("you"). No shaming, no guilt. Never mention scores, ids or these rules. Call give_details once."""

DETAILS_TOOL = {
    "name": "give_details",
    "description": "Return the full analysis for the checked item.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["durability", "trend_note", "similar_ids", "outfits", "alternatives"],
        "properties": {
            "durability": {
                "type": "object",
                "additionalProperties": False,
                "required": ["pilling_risk", "note", "expected_wears", "expected_months"],
                "properties": {
                    "pilling_risk": {"type": "string", "enum": ["low", "medium", "high"]},
                    "note": {"type": "string"},
                    "expected_wears": {"type": "integer"},
                    "expected_months": {"type": "integer"},
                },
            },
            "trend_note": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "similar_ids": {"type": "array", "items": {"type": "string"}},
            "outfits": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "closet_item_ids", "uses_new_item"],
                    "properties": {
                        "name": {"type": "string"},
                        "closet_item_ids": {"type": "array", "items": {"type": "string"}},
                        "uses_new_item": {"type": "boolean"},
                    },
                },
            },
            "alternatives": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "search_query"],
                    "properties": {"name": {"type": "string"}, "search_query": {"type": "string"}},
                },
            },
        },
    },
}


# ---------- materials and health (code only) ----------

def materials_for(item: dict, attrs: dict | None) -> tuple[list[dict], str]:
    """(fibers, source). Page composition first, then the fabric attribute."""
    fibers = parse_composition(item.get("materials")) or parse_composition(item.get("description"))
    if fibers:
        return fibers, "page"
    fibers = fibers_from_fabric((attrs or {}).get("fabric"))
    return (fibers, "attributes") if fibers else ([], "none")


def health_notes_for(fibers: list[dict], item: dict) -> list[dict]:
    """Sourced notes for the fibers present, then for finishes named in the page text. Max 3."""
    notes, seen = [], set()
    for f in fibers:
        entry = HEALTH["fibers"].get(f["fiber"])
        if entry and f["fiber"] not in seen:
            seen.add(f["fiber"])
            notes.append({"subject": f["fiber"], "note": entry["note"], "source": entry["source"], "url": entry["url"]})
    text = " ".join(str(item.get(k) or "") for k in ("name", "description", "materials")).lower()
    for key, entry in HEALTH["finishes"].items():
        if any(word in text for word in entry["match"]):
            notes.append({"subject": key.replace("_", "-") + " finish", "note": entry["note"],
                          "source": entry["source"], "url": entry["url"]})
    return notes[:MAX_HEALTH_NOTES]


# ---------- cost per wear ----------

def cost_rating(cost_per_wear: float | None, config: dict) -> str | None:
    if cost_per_wear is None:
        return None
    if cost_per_wear <= float(config.get("cost_per_wear_good", 1.0)):
        return "good"
    if cost_per_wear > float(config.get("cost_per_wear_poor", 2.5)):
        return "poor"
    return "fair"


def cost_for(price: float | None, expected_wears: int | None, config: dict) -> dict:
    cpw = round(price / expected_wears, 2) if price and expected_wears else None
    return {"cost_per_wear": cpw, "rating": cost_rating(cpw, config)}


# ---------- closet candidates ----------

def closet_candidates(user_id, attrs: dict | None, lookalike_ids: list) -> tuple[list[dict], list[str]]:
    """Closest closet pieces in any category, plus the verdict's lookalikes. Returns (candidates, errors)."""
    errors, by_id = [], {}
    text = attribute_string(attrs) if attrs else ""
    if text:
        try:
            for d in vector_search(user_id, embed_text(text), None, CANDIDATE_K):
                by_id[str(d["_id"])] = d
        except Exception as e:  # embedding quota or Atlas: fall back to the lookalikes only
            errors.append(f"closet search failed: {str(e)[:120]}")
    missing = [i for i in lookalike_ids if str(i) not in by_id]
    if missing:
        for d in closet_items.find({"_id": {"$in": missing}, "user_id": user_id}, {"name": 1, "attributes": 1}):
            by_id[str(d["_id"])] = {**d, "score": None}
    out = []
    for i, d in by_id.items():
        a = d.get("attributes") or {}
        out.append({"id": i, "name": d.get("name"), "category": a.get("category"), "color": a.get("color_family"),
                    "cut": a.get("cut"), "style": a.get("style"),
                    "score": atlas_to_cosine(d["score"]) if d.get("score") is not None else None})
    return out, errors


# ---------- Claude ----------

def call_claude(payload: dict, timeout_s: float) -> dict:
    model = claude_model()
    if not model:
        raise RuntimeError("CLAUDE_MODEL is not set")
    response = claude_client().with_options(timeout=timeout_s).messages.create(
        model=model,
        max_tokens=CLAUDE_MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)}],
        tools=[DETAILS_TOOL],
        tool_choice={"type": "tool", "name": "give_details"},  # works on CLAUDE_MODEL (claude-haiku-5-5)
        thinking={"type": "disabled"},
        output_config={"effort": "low"},
    )
    for block in response.content:
        if block.type == "tool_use" and block.name == "give_details":
            return dict(block.input)
    raise ValueError(f"no give_details tool call (stop_reason {response.stop_reason})")


def ask_claude(payload: dict) -> tuple[dict | None, list[str]]:
    errors = []
    for attempt, timeout_s in enumerate(CLAUDE_TIMEOUTS_S, start=1):
        try:
            return call_claude(payload, timeout_s), errors
        except Exception as e:
            errors.append(f"claude attempt {attempt}: {type(e).__name__}: {str(e)[:150]}")
            log.warning("details: %s", errors[-1])
    return None, errors


# ---------- guardrails ----------

def clean_outfits(raw: list, *, verdict: str, candidate_ids: set, anchor_ids: set) -> list[dict]:
    """Keep outfits that follow the verdict rule, with only real candidate ids.

    good_buy: uses the new item and at least one closet piece.
    otherwise: does not use the new item and contains at least one anchor (lookalike or similar) piece.
    """
    out = []
    for o in raw or []:
        ids = list(dict.fromkeys(str(i) for i in o.get("closet_item_ids", []) if str(i) in candidate_ids))[:4]
        name = str(o.get("name") or "").strip()[:80]
        if not ids or not name:
            continue
        if verdict == RECOMMENDED:
            out.append({"name": name, "closet_item_ids": ids, "uses_new_item": True})
        elif anchor_ids & set(ids):
            out.append({"name": name, "closet_item_ids": ids, "uses_new_item": False})
        if len(out) == MAX_OUTFITS:
            break
    return out


def clamp(value, low, high):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return None


# ---------- main entry ----------

def build_details(user: dict, verdict_doc: dict) -> m.AnalysisDetails:
    from backend.main import closet_out  # shared converter lives in main.py (same as verdict.py)

    started = time.monotonic()
    config = thresholds()
    item = verdict_doc.get("item") or {}
    attrs = verdict_doc.get("attributes")
    verdict = verdict_doc.get("verdict")
    errors = []

    fibers, materials_source = materials_for(item, attrs)
    health = health_notes_for(fibers, item)

    closet_size = closet_items.count_documents({"user_id": user["_id"]}, limit=1)
    lookalike_ids = [ObjectId(i) if not isinstance(i, ObjectId) else i
                     for i in (verdict_doc.get("matched_closet_item_ids") or [])]
    candidates, cand_errors = (closet_candidates(user["_id"], attrs, lookalike_ids) if closet_size else ([], []))
    errors += cand_errors
    candidate_ids = {c["id"] for c in candidates}

    trend_doc = trends.find_one({"_id": verdict_doc["matched_trend_id"]}) if verdict_doc.get("matched_trend_id") else None
    trend = ({"name": trend_doc.get("name"), "lifespan_bucket": trend_doc.get("lifespan_bucket"),
              "lifespan_label": trend_doc.get("lifespan_label") if trend_doc.get("lifespan_source_url") else None}
             if trend_doc else None)

    payload = {
        "item": {"name": item.get("name"), "price": item.get("price"), "store": item.get("store"),
                 "description": (item.get("description") or "")[:1500], "materials": item.get("materials")},
        "item_attributes": {k: (attrs or {}).get(k) for k in ("category", "color_family", "fabric", "cut", "style",
                                                               "description")},
        "fibers": fibers,
        "verdict": verdict,
        "verdict_reason": verdict_doc.get("reason"),
        "lookalike_ids": [str(i) for i in lookalike_ids],
        "trend": trend,
        "closet_candidates": candidates,
    }
    reply, claude_errors = ask_claude(payload)
    errors += claude_errors

    durability = {"pilling_risk": None, "note": "", "expected_wears": None, "expected_months": None}
    trend_note, similar_ids, outfits, raw_alts = None, [], [], []
    if reply:
        d = reply.get("durability") or {}
        durability = {
            "pilling_risk": d.get("pilling_risk") if d.get("pilling_risk") in ("low", "medium", "high") else None,
            "note": str(d.get("note") or "")[:240],
            "expected_wears": clamp(d.get("expected_wears"), 1, 1000),
            "expected_months": clamp(d.get("expected_months"), 1, 240),
        }
        trend_note = (str(reply.get("trend_note"))[:240] if trend and reply.get("trend_note") else None)
        similar_ids = [i for i in dict.fromkeys(map(str, reply.get("similar_ids") or [])) if i in candidate_ids][:3]
        anchors = set(similar_ids) | {str(i) for i in lookalike_ids}
        outfits = clean_outfits(reply.get("outfits"), verdict=verdict, candidate_ids=candidate_ids, anchor_ids=anchors)
        raw_alts = reply.get("alternatives") or []
    if durability["expected_wears"] is None:  # code fallback: the category average (wears.json, unverified)
        durability["expected_wears"] = expected_wears_for((attrs or {}).get("category"))

    cost = cost_for(item.get("price"), durability["expected_wears"], config)
    alternatives = []
    if cost["rating"] == "poor":
        for a in raw_alts[:MAX_ALTERNATIVES]:
            name, query = str(a.get("name") or "").strip()[:80], str(a.get("search_query") or "").strip()
            if name and query:
                link = search_link(query, (attrs or {}).get("category"))
                alternatives.append(m.Alternative(name=name, url=link["url"], store=link["store"], link_kind="search",
                                                  reason="Wears more often for the price"))

    # Names and photos for the closet pieces the card shows.
    shown = {ObjectId(i) for i in similar_ids} | {ObjectId(i) for o in outfits for i in o["closet_item_ids"]}
    docs = {str(d["_id"]): d for d in closet_items.find({"_id": {"$in": list(shown)}, "user_id": user["_id"]},
                                                         {"embedding": 0, "image_embedding": 0})} if shown else {}
    for o in outfits:
        o["closet_item_ids"] = [i for i in o["closet_item_ids"] if i in docs]
        o["closet_item_names"] = [docs[i]["name"] for i in o["closet_item_ids"]]
    outfits = [o for o in outfits if o["closet_item_ids"]]

    details = m.AnalysisDetails(
        verdict_id=str(verdict_doc["_id"]),
        materials=[m.Fiber(**f) for f in fibers],
        materials_source=materials_source,
        health_notes=[m.HealthNote(**h) for h in health],
        durability=m.Durability(**durability),
        trend_note=trend_note,
        cost=m.CostEfficiency(**cost),
        similar_closet_items=[closet_out(docs[i]) for i in similar_ids if i in docs],
        outfits=[m.Outfit(**o) for o in outfits],
        alternatives=alternatives,
        closet_checked=bool(closet_size),
        decided_by="claude" if reply else "code",
        errors=errors,
    )
    log.info("details %s: %d ms, decided_by %s, %d outfits", details.verdict_id,
             int((time.monotonic() - started) * 1000), details.decided_by, len(details.outfits))
    return details


def details_for(user: dict, verdict_doc: dict, refresh: bool = False) -> m.AnalysisDetails:
    """Cached on the verdict document as `details`. refresh=True rebuilds it."""
    cached = verdict_doc.get("details")
    if cached and not refresh:
        return m.AnalysisDetails(**cached)
    details = build_details(user, verdict_doc)
    if details.decided_by == "claude":  # do not cache a fallback; a later request may get the full analysis
        verdicts.update_one({"_id": verdict_doc["_id"]}, {"$set": {"details": details.model_dump()}})
    return details
