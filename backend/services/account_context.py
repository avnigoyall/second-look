"""Per-account context for the iMessage agent: what one shopper owns, saved and was told, plus the chat log.

Everything here is read with this user's `_id` only, so a context can never hold another account's data.
`build_context(user)` returns a plain dict (money already in dollars) that POST /imessage/ask hands to
Claude. `record_turn` stores one iMessage turn in `imessage_messages` (never image bytes).

Every section degrades on its own: a database or Nessie failure leaves that section as
{"unavailable": true} and the rest still answers.
"""
import logging
from collections import Counter
from datetime import datetime, timezone

from backend.db import closet_items, imessage_messages, verdicts
from backend.impact import impact_summary
from backend.services.closet import item_name
from backend.services.money import nessie_active, savings_summary

log = logging.getLogger("secondlook.account_context")

TEXT_MAX = 2000  # stored per message
CLOSET_SCAN_MAX = 500  # closet docs read for counts
CLOSET_LIST_MAX = 80  # closet items listed by name in the context
VERDICTS_MAX = 10
TURNS_MAX = 8
TURN_TEXT_MAX = 300  # per turn inside the context
REASON_MAX = 200
TRANSFERS_MAX = 5
IMESSAGE_PHOTO_NAME = "Clothing item from a photo sent over iMessage"  # DEFAULT_NAME in routers/imessage.py

DIRECTIONS = ("in", "out")
KINDS = ("text", "photo", "verdict", "ownership", "link", "answer")


# ---------- helpers ----------

def utc(value) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def day(value) -> str | None:
    value = utc(value)
    return value.date().isoformat() if value else None


def dollars(amount: float | int | None) -> str | None:
    return None if amount is None else f"${amount:,.2f}"


def cents_to_dollars(cents: int | None) -> str | None:
    return None if cents is None else dollars(cents / 100)


def clip(text: str | None, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


# ---------- conversation log ----------

def record_turn(user: dict, phone: str, direction: str, kind: str, text: str | None,
                verdict_id: str | None = None) -> None:
    """Store one iMessage turn for this account. Never raises: logging must not break a reply."""
    if direction not in DIRECTIONS or kind not in KINDS:
        raise ValueError(f"bad turn {direction}/{kind}")
    doc = {"user_id": user["_id"], "phone": phone, "direction": direction, "kind": kind,
           "text": (text or "")[:TEXT_MAX], "created_at": datetime.now(timezone.utc)}
    if verdict_id and verdict_id != "unsaved":
        doc["verdict_id"] = verdict_id
    try:
        imessage_messages.insert_one(doc)
    except Exception as e:
        log.warning("imessage turn not stored: %s", str(e)[:200])


def recent_turns(user_id, limit: int = TURNS_MAX) -> list[dict]:
    """The last `limit` turns, oldest first, as {from: shopper|second_look, kind, text, at}."""
    docs = list(imessage_messages.find({"user_id": user_id}, {"direction": 1, "kind": 1, "text": 1, "created_at": 1})
                .sort("created_at", -1).limit(limit))
    docs.reverse()
    return [{"from": "shopper" if d.get("direction") == "in" else "second_look", "kind": d.get("kind"),
             "text": clip(d.get("text"), TURN_TEXT_MAX) or ("(photo)" if d.get("kind") == "photo" else ""),
             "at": utc(d.get("created_at")).isoformat(timespec="minutes") if utc(d.get("created_at")) else None}
            for d in docs]


# ---------- sections ----------

def who(user: dict, closet_count: int | None) -> dict:
    settings = user.get("settings") or {}
    return {
        "email": user.get("email"),
        "preference_note": user.get("preference_note") or None,
        "member_since": day(user.get("created_at")),
        "closet_size": closet_count,
        "auto_add_purchases": settings.get("auto_add_purchases") is not False,
        "bank_savings_linked": nessie_active(user),
    }


def closet(user_id) -> dict:
    docs = list(closet_items.find({"user_id": user_id},
                                  {"name": 1, "status": 1, "attributes.category": 1, "attributes.color_family": 1,
                                   "created_at": 1})
                .sort("created_at", 1).limit(CLOSET_SCAN_MAX))
    items = []
    for d in docs:
        attrs = d.get("attributes") or {}
        items.append({"name": clip(d.get("name") or item_name(attrs), 80), "category": attrs.get("category"),
                      "color": attrs.get("color_family"),
                      "status": "incoming" if d.get("status") == "incoming" else "owned",
                      "added": day(d.get("created_at"))})
    by_category = Counter(i["category"] or "unknown" for i in items)
    return {
        "total": len(items),
        "owned": sum(1 for i in items if i["status"] == "owned"),
        "incoming": sum(1 for i in items if i["status"] == "incoming"),
        "by_category": dict(by_category.most_common()),
        "items": items[-CLOSET_LIST_MAX:],  # newest kept when the list is capped
        "items_listed": min(len(items), CLOSET_LIST_MAX),
        "note": None if len(items) <= CLOSET_LIST_MAX else f"Only the newest {CLOSET_LIST_MAX} items are listed.",
    }


def savings(user: dict) -> dict:
    impact = impact_summary(user["_id"])
    out = {
        "money_kept": dollars(impact["money_kept"]),
        "items_skipped": impact["items_skipped"],
        "co2e_avoided_kg": impact["co2e_kg"] if impact["co2e_items_counted"] else None,
        "co2e_note": "Estimate from sourced per-category lifecycle figures; "
                     "categories without a figure are not counted."
                     if impact["co2e_items_counted"] else None,
        "bank": None,
    }
    if nessie_active(user):
        try:
            summary = savings_summary(user)
        except Exception as e:  # savings_summary already falls back to local; this is a last guard
            log.info("savings summary unavailable: %s", str(e)[:200])
            summary = None
        if summary and summary.get("source") == "nessie":
            out["bank"] = {
                "checking_balance": cents_to_dollars(summary.get("checking_cents")),
                "savings_balance": cents_to_dollars(summary.get("savings_cents")),
                "recent_transfers_to_savings": [
                    {"amount": cents_to_dollars(t.get("amount_cents")), "date": str(t.get("at") or "")[:10] or None}
                    for t in (summary.get("transfers") or [])[:TRANSFERS_MAX]],
            }
        else:
            out["bank"] = {"unavailable": True}
    return out


def verdict_name(doc: dict) -> str:
    item, attrs = doc.get("item") or {}, doc.get("attributes") or {}
    name = (item.get("name") or "").strip()
    if (not name or name == IMESSAGE_PHOTO_NAME) and attrs.get("category"):
        name = item_name(attrs) + " (photo sent over iMessage)"
    return clip(name or "unnamed item", 120)


def recent_verdicts(user_id) -> list[dict]:
    docs = verdicts.find({"user_id": user_id},
                         {"item.name": 1, "item.price": 1, "item.store": 1, "attributes.category": 1,
                          "attributes.color_family": 1, "verdict": 1, "reason": 1, "action": 1, "channel": 1,
                          "ownership": 1, "created_at": 1}).sort("created_at", -1).limit(VERDICTS_MAX)
    out = []
    for d in docs:
        item = d.get("item") or {}
        action = d.get("action")
        row = {"item": verdict_name(d), "price": dollars(item.get("price")) if item.get("price") else None,
               "store": item.get("store") or None, "verdict": d.get("verdict"),
               "reason": clip(d.get("reason"), REASON_MAX),
               "action": action if action in ("skipped", "bought") else "none",
               "channel": d.get("channel") or "extension", "date": day(d.get("created_at"))}
        if d.get("ownership") in ("yes", "no"):  # their answer to "Do you already own this?" over iMessage
            row["already_owned"] = d["ownership"] == "yes"
        out.append(row)
    return out


def section(name: str, fn, *args):
    try:
        return fn(*args)
    except Exception as e:  # one broken section must not sink the answer
        log.warning("context section %s unavailable: %s", name, str(e)[:200])
        return {"unavailable": True}


def build_context(user: dict) -> dict:
    """Everything the agent may say about this account. Reads only documents with this user's _id."""
    user_id = user["_id"]
    closet_part = section("closet", closet, user_id)
    closet_count = closet_part.get("total") if isinstance(closet_part, dict) else None
    return {
        "today": datetime.now(timezone.utc).date().isoformat(),
        "who": who(user, closet_count),
        "closet": closet_part,
        "savings": section("savings", savings, user),
        "recent_verdicts": section("recent_verdicts", recent_verdicts, user_id),
        "recent_conversation": section("recent_conversation", recent_turns, user_id),
    }
