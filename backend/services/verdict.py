"""Part 5 verdict engine: one of have_this_look, micro_trend, good_buy, with a reason of at most 30 words.

Hybrid authority:
1. Code computes the signals: Part 2 attributes, closet matches (Part 3) and the category's trends (Part 4).
2. Code computes the allowed verdicts from the best closet score and its zone around t_c.
3. One Claude call (tool use) picks a verdict inside the allowed set and writes the reason.
4. Code guardrails check the reply. Anything outside the rules is replaced and logged as an override.
Every failure has a code-only path, so the shopper never sees an error.

Entry point: decide(user, item, save=False) -> (AnalyzeResponse, record). POST /analyze calls it with
save=True. scripts/eval_verdicts.py calls it with save=False, which never writes to the database.
"""
import base64
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import anthropic
from bson import ObjectId

from backend import models as m
from backend.db import closet_items, trends as trends_collection, verdicts
from backend.services import timing, verdict_deps
from backend.services.closet import NO_VECTORS, find_matches, thresholds
from backend.services.item_understanding import fetch_image
from backend.settings import ROOT
from backend.trends import NAME_COLLATION, get_trends_for_prompt

log = logging.getLogger("secondlook.verdict")

PRIORITY = ("have_this_look", "micro_trend", "good_buy")
T_C_BOUNDS = (0.70, 0.92)
DEFAULT_MARGIN = 0.04
MATCH_K = 5
SMALL_CLOSET = 10
REASON_MAX_WORDS = 30
MAX_MATCHED_ITEMS = 3
GRAY_CLOSET_PHOTOS = 2
CLAUDE_TIMEOUTS_S = (6.0, 4.0)  # attempt 1, then one retry
CLAUDE_MAX_TOKENS = 1024
# Part 2 fields kept on the verdict doc. Auto-add (services/closet.py) reads category to description from here.
STORED_ATTRIBUTE_FIELDS = ("category", "color_family", "fabric", "fabric_confidence", "cut", "style", "description",
                           "is_apparel", "target_box_2d", "other_garments_visible", "category_check")
WEARS_PATH = ROOT / "backend" / "config" / "wears.json"

NON_APPAREL_REASON = "This isn't clothing, so there's nothing to compare it with."
NOT_SEEN_PHRASE = "I don't see this in your closet"


# ---------- small helpers ----------

def now():
    return datetime.now(timezone.utc)


def elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def word_count(text: str) -> int:
    return len((text or "").split())


def color_ok(match: dict, attrs: dict) -> bool:
    """A color mismatch only counts when both colors are known and differ."""
    mine, theirs = attrs.get("color_family"), (match.get("attributes") or {}).get("color_family")
    return not (mine and theirs and mine != theirs)


def category_threshold(user: dict, category: str | None, config: dict) -> float:
    """t_c: the user's per-category threshold (Part 7 feedback) or look_similarity, clamped to the bounds."""
    per_user = (user.get("look_thresholds") or {}).get(category) if category else None
    value = per_user if isinstance(per_user, (int, float)) else config["look_similarity"]
    return round(min(max(float(value), T_C_BOUNDS[0]), T_C_BOUNDS[1]), 4)


def zone_for(best_score: float | None, t_c: float, margin: float) -> str:
    if best_score is None:
        return "none"
    if best_score >= round(t_c + margin, 4):  # round: 0.80 + 0.04 is 0.8400000000000001 in floats
        return "forced"
    if best_score >= round(t_c - margin, 4):
        return "gray"
    return "none"


def allowed_verdicts(zone: str | None, short_trends: list[dict]) -> list[str]:
    """Allowed set in priority order. zone None means matching did not run, which allows no ownership claim."""
    if zone == "forced":
        return ["have_this_look"]
    allowed = ["have_this_look"] if zone == "gray" else []
    if short_trends:
        allowed.append("micro_trend")
    allowed.append("good_buy")
    return allowed


def downgrade(verdict: str, allowed: list[str]) -> str:
    """The next allowed verdict below `verdict` in priority order, else the highest allowed one."""
    below = PRIORITY[PRIORITY.index(verdict) + 1:] if verdict in PRIORITY else PRIORITY
    return next((v for v in below if v in allowed), allowed[0])


def needs_not_seen_phrase(closet_small: bool, matches: list[dict], zone: str | None) -> bool:
    """Small closet, matching ran, nothing matched: a good_buy reason must say "I don't see this in your closet"."""
    return closet_small and zone is not None and not matches


def wears_config() -> dict:
    return json.loads(WEARS_PATH.read_text())


def expected_wears_for(category: str | None) -> int | None:
    if not category:
        return None
    config = wears_config()
    return int(config["per_category"].get(category, config["default"]))


def trend_matches_features(trend: dict, attrs: dict) -> bool:
    """Code-only trend check for the fallback: every feature the trend names equals the item's attribute."""
    features = {k: v for k, v in (trend.get("features") or {}).items() if v}
    if not features:
        return False
    item = {"cut": attrs.get("cut"), "color": attrs.get("color_family"), "fabric": attrs.get("fabric")}
    return all(item.get(k) == v for k, v in features.items())


def data_url_block(data_url: str) -> dict | None:
    """Claude image block from a data URL, or None if it is not a base64 image data URL."""
    try:
        head, data = data_url.split(",", 1)
        media_type = head.split(":", 1)[1].split(";", 1)[0]
    except (ValueError, IndexError, AttributeError):
        return None
    if not head.endswith(";base64") or media_type not in ("image/jpeg", "image/png", "image/gif", "image/webp"):
        return None
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}


def jpeg_block(jpeg: bytes) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": base64.b64encode(jpeg).decode()}}


# ---------- template reasons (fallback and guardrail replacements) ----------

def template_reason(verdict: str, *, match_name: str | None = None, trend_name: str | None = None,
                    closet_small: bool = False, has_matches: bool = False, zone: str | None = None) -> str:
    if verdict == "have_this_look":
        if match_name:
            return f"This looks a lot like your {match_name}, so it may not add much to your closet."
        return "This looks a lot like something already in your closet."
    if verdict == "micro_trend":
        if trend_name:
            return (f"This fits the {trend_name} trend, which looks likely short-lived. "
                    "A classic version may get more wear.")
        return "This fits a trend that looks likely short-lived. A classic version may get more wear."
    if zone is None:
        return "Nothing about this item stands out against current trends."
    if closet_small and not has_matches:
        return f"{NOT_SEEN_PHRASE}, and it doesn't match a short-lived trend."
    if zone == "gray" and match_name:
        return f"It's close to your {match_name}, but different enough to add something new."
    return "Nothing in your closet looks quite like this, and it doesn't match a short-lived trend."


# ---------- the Claude call ----------

SYSTEM_PROMPT = """You are Second Look, a shopping assistant inside a browser extension. A shopper is about to add one
clothing item to their cart. Decide one verdict and write one short reason for the card.

Verdicts, in priority order:
- have_this_look: the shopper already owns (or has on the way) something that gives the same look.
- micro_trend: the item is a piece of a listed trend whose lifespan_bucket is "short".
- good_buy: neither of the above.

Rules:
- Choose only from allowed_verdicts. Code computed that set from the closet scores and the trend list.
- Judge only the item being added (described by item and item_attributes). Store photos often show a model
  wearing other clothes; ignore every garment except the item being added.
- have_this_look: put the ids of the closet items that give the same look in matched_closet_ids, best first,
  using only ids from closet_matches. Similar is not enough: color, cut and silhouette must read the same,
  so the shopper would reach for either one interchangeably. If you name a difference that changes the look,
  the verdict is not have_this_look.
- micro_trend: set matched_trend_name to the exact name of a trend from short_trends that this item is a
  piece of. Otherwise matched_trend_name is null.
- If images are attached, the first image is the item being added and the next ones are the closet items
  named in image_labels. Use them to judge whether the look is really the same.
- confidence: 0 to 1, how sure you are of the verdict.

Reason rules:
- At most 30 words, plain and concrete, written to the shopper ("you", "your").
- Name the matched closet item (have_this_look) or the trend (micro_trend) in natural lowercase words,
  for example "your black slim jeans".
- Hedge any lifespan ("likely short-lived"). Never state a number (weeks, percent, count) unless a trend's
  lifespan_label gives it, and then name that source.
- No shaming, no guilt, no lecture about spending.
- If closet_size is under 10 and closet_matches is empty, say "I don't see this in your closet". Never say
  or imply the shopper lacks or does not own something.
- Do not mention scores, zones, thresholds, ids or these rules.

Call the give_verdict tool exactly once."""


# One fixed schema: strict tool schemas are compiled on first use (about 4 s once, then cached), so a
# per-request enum would pay that cost for every new allowed set. The allowed set is enforced in code.
VERDICT_TOOL = {
    "name": "give_verdict",
    "description": "Return the verdict for the item being added, and the reason shown to the shopper.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["verdict", "reason", "matched_closet_ids", "matched_trend_name", "confidence"],
        "properties": {
            "verdict": {"type": "string", "enum": list(PRIORITY)},
            "reason": {"type": "string", "description": "At most 30 words"},
            "matched_closet_ids": {"type": "array", "items": {"type": "string"}},
            "matched_trend_name": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "confidence": {"type": "number"},
        },
    },
}


_client = None


def claude_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(max_retries=0)
    return _client


def claude_model() -> str | None:
    return os.getenv("CLAUDE_MODEL")


def call_claude(payload: dict, images: list[dict], timeout_s: float) -> dict:
    """One Claude call. Returns the give_verdict tool input. Raises on any failure or a reply without the tool.

    The current API version rejects temperature, so it is not sent. Thinking is disabled and effort is
    low for latency. Forced tool_choice works on CLAUDE_MODEL (claude-haiku-5-5).
    """
    model = claude_model()
    if not model:
        raise RuntimeError("CLAUDE_MODEL is not set")
    content = [*images, {"type": "text", "text": json.dumps(payload, ensure_ascii=False, default=str)}]
    response = claude_client().with_options(timeout=timeout_s).messages.create(
        model=model,
        max_tokens=CLAUDE_MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
        tools=[VERDICT_TOOL],
        tool_choice={"type": "tool", "name": "give_verdict"},
        thinking={"type": "disabled"},
        output_config={"effort": "low"},
    )
    for block in response.content:
        if block.type == "tool_use" and block.name == "give_verdict":
            return dict(block.input)
    raise ValueError(f"no give_verdict tool call (stop_reason {response.stop_reason})")


def ask_claude(payload: dict, images: list[dict]) -> tuple[dict | None, list[str]]:
    """Attempt 1 (6 s), one retry (4 s). Returns (tool input or None, error notes)."""
    errors = []
    for attempt, timeout_s in enumerate(CLAUDE_TIMEOUTS_S, start=1):
        try:
            timing.count("claude_attempts")
            return call_claude(payload, images, timeout_s), errors
        except Exception as e:  # timeouts, API errors, malformed replies: all end in the fallback
            note = f"attempt {attempt}: {type(e).__name__}: {str(e)[:200]}"
            log.warning("verdict: Claude call failed, %s", note)
            errors.append(note)
    return None, errors


# ---------- guardrails ----------

def apply_guardrails(reply: dict, *, allowed: list[str], matches: list[dict], best: dict | None,
                     short_trends: list[dict], zone: str | None, closet_small: bool) -> dict:
    """Check Claude's reply against the rules. Returns {verdict, reason, matched_ids, trend_name, confidence,
    overrides}. overrides is a list of {rule, code_said, claude_said}, empty when Claude's reply stood."""
    claude_verdict = reply.get("verdict")
    verdict = claude_verdict
    reason = (reply.get("reason") or "").strip()
    match_by_id = {mt["id"]: mt for mt in matches}
    ids = [i for i in dict.fromkeys(reply.get("matched_closet_ids") or []) if i in match_by_id]
    short_names = {t["name"].casefold(): t["name"] for t in short_trends}
    raw_trend = reply.get("matched_trend_name")
    trend_name = short_names.get(raw_trend.casefold()) if isinstance(raw_trend, str) else None
    overrides = []
    template = False

    def override(rule: str, new_verdict: str):
        nonlocal verdict, template
        overrides.append({"rule": rule, "code_said": new_verdict, "claude_said": claude_verdict})
        verdict, template = new_verdict, True

    if verdict not in allowed:
        override("verdict_not_allowed", downgrade(verdict, allowed))
    if verdict == "have_this_look" and not ids:
        if zone == "forced" and best:
            ids = [best["id"]]  # the only allowed verdict: code names the best match itself
            override("no_valid_closet_ids", "have_this_look")
        else:
            override("no_valid_closet_ids", downgrade("have_this_look", allowed))
    if verdict == "micro_trend" and not trend_name:
        override("trend_not_in_list", downgrade("micro_trend", allowed))
    if not template and (not reason or word_count(reason) > REASON_MAX_WORDS):
        overrides.append({"rule": "reason_too_long" if reason else "reason_empty",
                          "code_said": verdict, "claude_said": claude_verdict})
        template = True
    if (not template and verdict == "good_buy" and needs_not_seen_phrase(closet_small, matches, zone)
            and NOT_SEEN_PHRASE.casefold() not in reason.replace("\u2019", "'").casefold()):
        overrides.append({"rule": "missing_not_seen_phrase", "code_said": verdict, "claude_said": claude_verdict})
        template = True

    if verdict != "have_this_look":
        ids = []
    if verdict != "micro_trend":
        trend_name = None
    if template:
        first = match_by_id[ids[0]]["name"] if ids else (best["name"] if best else None)
        reason = template_reason(verdict, match_name=first, trend_name=trend_name, closet_small=closet_small,
                                 has_matches=bool(matches), zone=zone)
    confidence = reply.get("confidence")
    confidence = min(max(float(confidence), 0.0), 1.0) if isinstance(confidence, (int, float)) else None
    return {"verdict": verdict, "reason": reason, "matched_ids": ids[:MAX_MATCHED_ITEMS], "trend_name": trend_name,
            "confidence": confidence, "overrides": overrides}


def code_only_verdict(*, allowed: list[str], zone: str | None, best: dict | None, t_c: float,
                      short_trends: list[dict], attrs: dict) -> tuple[str, list[str], str | None]:
    """Verdict without Claude: (verdict, matched ids, trend name). Conservative in the gray band."""
    if "have_this_look" in allowed and best and (zone == "forced" or best["score"] >= t_c):
        return "have_this_look", [best["id"]], None
    if "micro_trend" in allowed:
        trend = next((t for t in short_trends if trend_matches_features(t, attrs)), None)
        if trend:
            return "micro_trend", [], trend["name"]
    return "good_buy", [], None


# ---------- signals ----------

def closet_size(user_id) -> int | None:
    try:
        return closet_items.count_documents({"user_id": user_id})
    except Exception as e:
        log.warning("verdict: closet count failed: %s", str(e)[:200])
        return None


def gather_signals(user: dict, item: dict, attrs: dict, config: dict) -> dict:
    """Closet matches, trends, closet size and the item photo, fetched in parallel."""
    errors = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        f_matches = timing.submit(pool, find_matches, user["_id"], attrs, MATCH_K)
        f_trends = timing.submit(pool, timed, "trends_ms", get_trends_for_prompt)
        f_size = timing.submit(pool, timed, "closet_size_ms", closet_size, user["_id"])
        # The item photo is only sent to Claude in the gray band, and only when there is no target crop.
        f_photo = (timing.submit(pool, timed, "photo_fetch_ms", fetch_image, item["image_url"])
                   if item.get("image_url") and not item.get("target_crop") else None)
        try:
            matches = f_matches.result()
        except Exception as e:  # EmbeddingError, Atlas (PyMongoError) and anything else: no match
            log.warning("verdict: find_matches failed: %s", str(e)[:200])
            errors.append(f"matches: {type(e).__name__}")
            matches = None
        try:
            all_trends = f_trends.result()
        except Exception as e:
            log.warning("verdict: trends lookup failed: %s", str(e)[:200])
            errors.append(f"trends: {type(e).__name__}")
            all_trends = []
        size = f_size.result()
        photo = None
        if f_photo is not None:
            try:
                photo = f_photo.result()
            except Exception as e:
                log.info("verdict: item photo not available for Claude: %s", str(e)[:200])
    category = attrs.get("category")
    trends = [t for t in all_trends if category and t.get("category") == category]
    return {"matches": matches, "trends": trends, "closet_size": size, "photo": photo, "errors": errors}


def timed(name: str, fn, *args):
    with timing.stage(name):
        return fn(*args)


def closet_docs(ids: list[str]) -> dict:
    """Full closet docs (with image_data, no vectors) by id. {} on any failure."""
    if not ids:
        return {}
    try:
        docs = closet_items.find({"_id": {"$in": [ObjectId(i) for i in ids]}}, NO_VECTORS)
        return {str(d["_id"]): d for d in docs}
    except Exception as e:
        log.warning("verdict: closet photo lookup failed: %s", str(e)[:200])
        return {}


def find_trend_doc(name: str) -> dict | None:
    try:
        return trends_collection.find_one({"name": name}, collation=NAME_COLLATION)
    except Exception as e:
        log.warning("verdict: trend lookup failed: %s", str(e)[:200])
        return None


# ---------- decide ----------

def decide(user: dict, item: dict | m.Item, save: bool = False) -> tuple[m.AnalyzeResponse, dict]:
    """Run the verdict engine for one item. Never raises for model, embedding or Atlas failures.

    user: the users document (needs _id; reads look_thresholds and preference_note if present).
    item: an Item (or its dict): name, image_url, price, store, url, description, breadcrumb.
    save: True inserts the verdict document and returns its id. False writes nothing (verdict_id "unsaved").
    Returns (AnalyzeResponse, record). record is the verdict document (stored or not) and holds the signals.
    """
    from backend.main import closet_out, trend_out  # shared converters live in main.py

    started = time.monotonic()
    timings = timing.start()
    item = item.model_dump() if isinstance(item, m.Item) else m.Item(**item).model_dump()
    config = thresholds()
    margin = float(config.get("gray_margin", DEFAULT_MARGIN))

    try:
        understood = verdict_deps.understand_for_verdict(item)
        timings["attributes_ms"] = elapsed_ms(started)
    except Exception as e:
        log.warning("verdict: attributes failed: %s", str(e)[:200])
        understood = {"status": "attributes_failed"}
    target_crop = understood.get("target_crop")
    attrs = {k: v for k, v in understood.items() if k not in ("target_crop",)}

    signals = {"t_c": None, "margin": margin, "zone": None, "allowed": ["good_buy"], "matches": [],
               "best": None, "trends": [], "short_trends": [], "closet_size": None, "closet_small": False,
               "errors": [], "path": None}
    matches, best, zone, t_c, short_trends = [], None, None, None, []
    reply, claude_errors, overrides, confidence = None, [], [], None
    matched_ids, trend_name, docs = [], None, {}

    if understood.get("status") != "ok":
        signals["path"] = "attributes_failed"
        verdict, decided_by = "good_buy", "code"
        reason = template_reason("good_buy", zone=None)
    elif understood.get("is_apparel") is False:
        signals["path"] = "non_apparel"
        verdict, decided_by, reason = "good_buy", "code", NON_APPAREL_REASON
    else:
        mark = time.monotonic()
        gathered = gather_signals(user, {**item, "target_crop": target_crop}, attrs, config)
        timings["signals_ms"] = elapsed_ms(mark)
        signals["errors"] = gathered["errors"]
        size = gathered["closet_size"]
        closet_small = size is not None and size < SMALL_CLOSET
        trends = gathered["trends"]
        short_trends = [t for t in trends if t.get("lifespan_bucket") == "short"]
        t_c = category_threshold(user, attrs.get("category"), config)
        if gathered["matches"] is not None:
            matches = gathered["matches"]
            for mt in matches:
                mt["color_ok"] = color_ok(mt, attrs)
            eligible = [mt for mt in matches if mt["color_ok"]]
            best = max(eligible, key=lambda mt: mt["score"]) if eligible else None
            zone = zone_for(best["score"] if best else None, t_c, margin)
        allowed = allowed_verdicts(zone, short_trends)
        signals.update(t_c=t_c, zone=zone, allowed=allowed, matches=matches, best=best, trends=trends,
                       short_trends=[t["name"] for t in short_trends], closet_size=size,
                       closet_small=closet_small, path="matched" if zone is not None else "no_matching")

        # Closet docs for the top matches: photos for the gray band, and the response objects.
        top_ids = [mt["id"] for mt in sorted(matches, key=lambda mt: -mt["score"])][:MAX_MATCHED_ITEMS]
        with timing.stage("closet_docs_ms"):
            docs = closet_docs(top_ids) if zone in ("forced", "gray") else {}
        images, labels = [], []
        if zone == "gray":
            item_image = data_url_block(target_crop) if target_crop else (
                jpeg_block(gathered["photo"]) if gathered["photo"] else None)
            if item_image:
                images.append(item_image)
                labels.append("item being added")
                for mt in [mt for mt in matches if mt["color_ok"]][:GRAY_CLOSET_PHOTOS]:
                    block = data_url_block((docs.get(mt["id"]) or {}).get("image_data") or "")
                    if block:
                        images.append(block)
                        labels.append(f"closet item {mt['id']} ({mt['name']})")

        payload = {
            "item": {k: item.get(k) for k in ("name", "price", "store", "description", "breadcrumb")},
            "item_attributes": {k: attrs.get(k) for k in ("category", "color_family", "fabric", "cut", "style",
                                                          "description")},
            "closet_matches": [{"id": mt["id"], "name": mt["name"], "status": mt["status"],
                                "score": mt["score"], "same_color_family": mt["color_ok"],
                                "attributes": mt.get("attributes")} for mt in matches],
            "closet_size": size,
            "trends": trends,
            "short_trends": [t["name"] for t in short_trends],
            "preference_note": user.get("preference_note"),
            "allowed_verdicts": allowed,
            "image_labels": labels,
        }
        if zone is None:
            payload["note"] = "Closet matching was unavailable. Say nothing about what the shopper owns."
        elif needs_not_seen_phrase(closet_small, matches, zone):
            payload["note"] = f"The closet is small and nothing matched. The reason must include: {NOT_SEEN_PHRASE}."
        mark = time.monotonic()
        reply, claude_errors = ask_claude(payload, images)
        timings["claude_ms"] = elapsed_ms(mark)
        if reply is not None:
            checked = apply_guardrails(reply, allowed=allowed, matches=matches, best=best,
                                       short_trends=short_trends, zone=zone, closet_small=closet_small)
            verdict, reason = checked["verdict"], checked["reason"]
            matched_ids, trend_name = checked["matched_ids"], checked["trend_name"]
            overrides, confidence = checked["overrides"], checked["confidence"]
            verdict_overridden = any(o["code_said"] != o["claude_said"] for o in overrides)
            decided_by = "claude" if len(allowed) > 1 and not verdict_overridden else "code"
        else:
            verdict, matched_ids, trend_name = code_only_verdict(
                allowed=allowed, zone=zone, best=best, t_c=t_c, short_trends=short_trends, attrs=attrs)
            decided_by = "fallback"
            match_name = next((mt["name"] for mt in matches if mt["id"] in matched_ids), None)
            reason = template_reason(verdict, match_name=match_name or (best["name"] if best else None),
                                     trend_name=trend_name, closet_small=closet_small,
                                     has_matches=bool(matches), zone=zone)

        for o in overrides:
            log.info("verdict override: %s (code said %s, Claude said %s)", o["rule"], o["code_said"],
                     o["claude_said"])
        if matched_ids:
            missing = [i for i in matched_ids if i not in docs]
            docs.update(closet_docs(missing))

    # ---------- build the response ----------
    matched_docs = [docs[i] for i in matched_ids if i in docs]
    matched_items = [closet_out(d) for d in matched_docs]
    trend_doc = find_trend_doc(trend_name) if verdict == "micro_trend" and trend_name else None
    alternatives = []
    if trend_doc:
        try:
            alternatives = [a for a in verdict_deps.alternatives_for_trend(trend_doc) if a.url]
        except Exception as e:
            log.warning("verdict: alternatives failed: %s", str(e)[:200])
    expected_wears = expected_wears_for(attrs.get("category")) if verdict == "good_buy" and \
        understood.get("is_apparel") is not False and understood.get("status") == "ok" else None
    price = item.get("price")
    cost_per_wear = round(price / expected_wears, 2) if price and expected_wears else None
    latency_ms = int((time.monotonic() - started) * 1000)

    signals["claude_errors"] = claude_errors
    signals["timings"] = timings
    signals["claude_reply"] = reply
    signals["confidence"] = confidence
    record = {
        "user_id": user["_id"],
        "item": item,
        "attributes": ({k: attrs.get(k) for k in STORED_ATTRIBUTE_FIELDS}
                       if understood.get("status") == "ok" else None),
        "verdict": verdict,
        "reason": reason,
        "matched_closet_item_id": matched_docs[0]["_id"] if matched_docs else None,
        "matched_closet_item_ids": [d["_id"] for d in matched_docs],
        "matched_trend_id": trend_doc["_id"] if trend_doc else None,
        "alternatives": [a.model_dump() for a in alternatives],
        "expected_wears": expected_wears,
        "cost_per_wear": cost_per_wear,
        "signals": signals,
        "decided_by": decided_by,
        "override": overrides[0] if overrides else None,
        "overrides": overrides,
        "model": claude_model() if (reply is not None or claude_errors) else None,
        "latency_ms": latency_ms,
        "target_crop": target_crop,
        "action": "none",
        "feedback": "none",
        "created_at": now(),
    }
    verdict_id = "unsaved"
    if save:
        mark = time.monotonic()
        verdict_id = str(verdicts.insert_one(record).inserted_id)
        timings["save_ms"] = elapsed_ms(mark)
    timings["total_ms"] = elapsed_ms(started)
    response = m.AnalyzeResponse(
        status="ok", verdict_id=verdict_id, verdict=verdict, reason=reason,
        matched_closet_item=matched_items[0] if matched_items else None,
        matched_trend=trend_out(trend_doc) if trend_doc else None,
        alternatives=alternatives, outfits=[], expected_wears=expected_wears, cost_per_wear=cost_per_wear,
        matched_closet_items=matched_items, decided_by=decided_by, closet_small=signals["closet_small"],
        zone=signals["zone"], wears_estimated=expected_wears is not None,
    )
    log.info("verdict %s by %s in %d ms (zone %s, allowed %s)", verdict, decided_by, latency_ms,
             signals["zone"], signals["allowed"])
    log.info("timing analyze %s", " ".join(f"{k}={v}" for k, v in timings.items()))
    return response, record
