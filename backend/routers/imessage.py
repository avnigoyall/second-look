"""iMessage channel (Photon). Service endpoints for the Node agent in imessage/, not for the extension.

The agent authenticates with the shared secret IMESSAGE_AGENT_SECRET (header X-Agent-Secret) and names the
shopper by phone number. A phone is linked to a users document once, with POST /imessage/link.

Flow: photo texted in -> POST /imessage/analyze runs the same verdict engine as /analyze (decide) for the
linked user -> the agent texts back `message`. When `ask_ownership` is true the agent asks "Do you already
own this?" and sends the answer to POST /imessage/own, which adds the item to the closet on yes.
Any other text goes to POST /imessage/ask, which answers from this account's context (closet, savings,
recent verdicts, recent conversation). Every turn of a linked phone is stored in `imessage_messages`.

Two endpoints are for the extension instead (JWT, the logged-in shopper): POST /imessage/connect assigns the
account's phone a Photon iMessage line, GET /imessage/status reports it (backend/services/photon_lines.py).
"""
import hmac
import logging
import os
import re
from datetime import datetime, timedelta, timezone

from bson import ObjectId
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

from backend.auth import current_user

from backend.db import closet_items, users, verdicts
from backend.phone import PhoneError
from backend.phone import normalize_phone as to_e164
from backend.services import imessage_ask, photon_lines
from backend.services.account_context import build_context, record_turn
from backend.services.closet import item_name, new_closet_doc
from backend.services.embeddings import EmbeddingError
from backend.services.verdict import decide

log = logging.getLogger("secondlook.imessage")
router = APIRouter(prefix="/imessage", tags=["imessage"])

OWNERSHIP_WINDOW = timedelta(hours=24)  # a yes/no answers the latest iMessage verdict from this window
DEFAULT_NAME = "Clothing item from a photo sent over iMessage"
NAME_MAX = 200


# ---------- auth and phone helpers ----------

def require_agent(secret: str | None) -> None:
    expected = os.getenv("IMESSAGE_AGENT_SECRET", "")
    if not expected:
        raise HTTPException(503, "IMESSAGE_AGENT_SECRET is not set on the backend")
    if not secret or not hmac.compare_digest(secret, expected):
        raise HTTPException(401, "Bad agent secret")


def normalize_phone(phone: str) -> str:
    """E.164, the same form signup stores, so a Photon handle finds the account. 422 when it is not a phone."""
    try:
        return to_e164(phone)
    except PhoneError as e:
        raise HTTPException(422, str(e))


def user_for_phone(phone: str) -> dict | None:
    return users.find_one({"phone": normalize_phone(phone)})


_indexes_ready = False


def ensure_phone_index() -> None:
    global _indexes_ready
    if not _indexes_ready:  # users.phone is indexed in backend/db.py ensure_indexes()
        verdicts.create_index([("user_id", ASCENDING), ("channel", ASCENDING), ("created_at", DESCENDING)])
        _indexes_ready = True


# ---------- request and response models ----------

class LinkRequest(BaseModel):
    phone: str
    email: str = Field(min_length=3, max_length=320)


class LinkResponse(BaseModel):
    linked: bool
    email: str | None = None


class AnalyzeImessageRequest(BaseModel):
    phone: str
    image_base64: str = Field(description="JPEG/PNG data URL or bare base64")
    text: str | None = Field(None, max_length=2000, description="Caption the shopper sent with the photo")


class AnalyzeImessageResponse(BaseModel):
    verdict_id: str
    verdict: str
    message: str = Field(description="Ready-to-send iMessage text")
    ask_ownership: bool


class OwnRequest(BaseModel):
    phone: str
    owns: bool


class OwnResponse(BaseModel):
    ok: bool
    message: str


class AskRequest(BaseModel):
    phone: str
    text: str = Field(min_length=1, max_length=10000, description="The shopper's message (stored capped at 2000)")


class AskResponse(BaseModel):
    message: str = Field(description="Ready-to-send iMessage text")


class ConnectRequest(BaseModel):
    phone: str | None = Field(None, max_length=40, description="Optional. Sets or replaces the account's phone first")


class ConnectStatus(BaseModel):
    status: Literal["none", "assigning", "pending_optin", "connected", "error"]
    line: str | None = Field(None, description="E.164 of the account's iMessage line, when it has one")
    phone: str | None = Field(None, description="The account's own phone (E.164)")
    message: str


# ---------- message text ----------

def verdict_message(resp) -> str:
    """Plain text for one AnalyzeResponse. No markdown: iMessage shows it as typed."""
    lines = [resp.reason.strip()]
    if resp.verdict == "have_this_look":
        names = [c.name for c in (resp.matched_closet_items or ([resp.matched_closet_item] if resp.matched_closet_item else []))]
        if names:
            lines.append("From your closet: " + ", ".join(names[:3]) + ".")
    elif resp.verdict == "micro_trend" and resp.matched_trend:
        t = resp.matched_trend
        trend_line = f"Trend: {t.name}."
        if t.lifespan_label and t.lifespan_source_url:  # lifespans are shown only with their source (CONTRACT 6)
            trend_line += f" {t.lifespan_label} (source: {t.lifespan_source_url})"
        lines.append(trend_line)
        for alt in resp.alternatives[:2]:
            lines.append(f"Lasts longer: {alt.name} {alt.url}")
    elif resp.verdict == "good_buy" and resp.expected_wears:
        lines.append(f"Estimate: about {resp.expected_wears} wears.")
    return "\n".join(lines)


# ---------- endpoints ----------

@router.post("/link", response_model=LinkResponse)
def link(body: LinkRequest, x_agent_secret: str | None = Header(None)):
    """Link a phone to the account with this email. Moves the phone off any other account.

    Hackathon trust model: whoever texts from the phone can name any existing email. Fine for the demo,
    replace with a confirmation code before real users.
    """
    require_agent(x_agent_secret)
    ensure_phone_index()
    phone = normalize_phone(body.phone)
    email = body.email.strip()
    user = users.find_one({"email": email}) or users.find_one(
        {"email": {"$regex": f"^{re.escape(email)}$", "$options": "i"}})
    if user is None:
        return {"linked": False}
    users.update_many({"phone": phone, "_id": {"$ne": user["_id"]}}, {"$unset": {"phone": ""}})
    users.update_one({"_id": user["_id"]}, {"$set": {"phone": phone}})
    record_turn(user, phone, "in", "link", email)
    record_turn(user, phone, "out", "link", f"Linked to {user['email']}.")
    return {"linked": True, "email": user["email"]}


@router.post("/analyze", response_model=AnalyzeImessageResponse)
def analyze(body: AnalyzeImessageRequest, x_agent_secret: str | None = Header(None)):
    """Same verdict engine as POST /analyze. Answers 404 when the phone is not linked to an account."""
    require_agent(x_agent_secret)
    ensure_phone_index()
    user = user_for_phone(body.phone)
    if user is None:
        raise HTTPException(404, "phone_not_linked")

    image = body.image_base64.strip()
    image_url = image if image.startswith("data:") else "data:image/jpeg;base64," + image
    name = (body.text or "").strip()[:NAME_MAX] or DEFAULT_NAME
    response, record = decide(user, {"name": name, "image_url": image_url}, save=True)

    # Keep the stored verdict small and mark its channel: the photo itself lives on as target_crop.
    if response.verdict_id and response.verdict_id != "unsaved":
        verdicts.update_one({"_id": ObjectId(response.verdict_id)},
                            {"$set": {"item.image_url": None, "channel": "imessage"}})

    has_attrs = bool(record.get("attributes"))
    message = verdict_message(response)
    phone = normalize_phone(body.phone)
    record_turn(user, phone, "in", "photo", body.text, verdict_id=response.verdict_id)
    record_turn(user, phone, "out", "verdict", message, verdict_id=response.verdict_id)
    return {
        "verdict_id": response.verdict_id,
        "verdict": response.verdict,
        "message": message,
        "ask_ownership": has_attrs and response.verdict != "have_this_look",
    }


@router.post("/own", response_model=OwnResponse)
def own(body: OwnRequest, x_agent_secret: str | None = Header(None)):
    """Answer "do you already own this?" for the latest iMessage verdict. Yes adds it to the closet as owned."""
    require_agent(x_agent_secret)
    ensure_phone_index()
    user = user_for_phone(body.phone)
    if user is None:
        raise HTTPException(404, "phone_not_linked")
    phone = normalize_phone(body.phone)
    record_turn(user, phone, "in", "ownership", "yes" if body.owns else "no")
    result = _own(user, body.owns)
    record_turn(user, phone, "out", "ownership", result["message"])
    return result


def _own(user: dict, owns: bool) -> dict:
    since = datetime.now(timezone.utc) - OWNERSHIP_WINDOW
    doc = verdicts.find_one(
        {"user_id": user["_id"], "channel": "imessage", "ownership": {"$exists": False}, "created_at": {"$gte": since}},
        sort=[("created_at", DESCENDING)],
    )
    if doc is None:
        return {"ok": False, "message": "Send me a photo of something you're thinking about buying first."}

    verdicts.update_one({"_id": doc["_id"]}, {"$set": {"ownership": "yes" if owns else "no"}})
    if not owns:
        return {"ok": True, "message": "Got it, not in your closet. Send another photo whenever you're deciding on something."}

    attrs = doc.get("attributes")
    if not attrs:
        return {"ok": False, "message": "I couldn't tell what that item was, so I didn't add it. Try another photo."}
    try:
        closet_doc = new_closet_doc(user["_id"], name=item_name(attrs), attributes=attrs, source="ownership_answer",
                                    image_data=doc.get("target_crop"))
    except EmbeddingError as e:
        log.warning("imessage own: embedding failed: %s", e)
        verdicts.update_one({"_id": doc["_id"]}, {"$unset": {"ownership": ""}})  # let them answer again
        return {"ok": False, "message": "I couldn't save that just now. Reply yes again in a minute."}
    closet_items.insert_one(closet_doc)
    return {"ok": True, "message": f"Added the {closet_doc['name']} to your closet. I'll flag lookalikes next time."}


@router.post("/ask", response_model=AskResponse)
def ask(body: AskRequest, x_agent_secret: str | None = Header(None)):
    """Answer any other text from this account's own context. Claude failures answer a fallback message, never 5xx."""
    require_agent(x_agent_secret)
    ensure_phone_index()
    user = user_for_phone(body.phone)
    if user is None:
        raise HTTPException(404, "phone_not_linked")
    phone = normalize_phone(body.phone)
    question = body.text.strip()[:2000]
    context = build_context(user)  # read before this turn is stored, so the question is not in its own history
    message, info = imessage_ask.answer(context, question)
    log.info("imessage ask: ok=%s %d ms", info["ok"], info["latency_ms"])
    record_turn(user, phone, "in", "text", question)
    record_turn(user, phone, "out", "answer", message)
    return {"message": message}


# ---------- extension endpoints (JWT): the shopper's own iMessage line ----------

@router.post("/connect", response_model=ConnectStatus)
def connect(body: ConnectRequest | None = None, user=Depends(current_user)):
    """Assign this account's phone a Photon iMessage line (idempotent). Photon failures answer 200 with
    status "error" and a user-safe message. 400 when the account has no phone and none is given."""
    if body and body.phone and body.phone.strip():
        phone = normalize_phone(body.phone)
        if phone != user.get("phone"):
            if users.find_one({"phone": phone, "_id": {"$ne": user["_id"]}}):
                raise HTTPException(409, "This phone number is already linked to another account")
            try:
                users.update_one({"_id": user["_id"]}, {"$set": {"phone": phone}, "$unset": {"photon_error": ""}})
            except DuplicateKeyError:
                raise HTTPException(409, "This phone number is already linked to another account")
    elif not user.get("phone"):
        raise HTTPException(400, "Add your phone number first")
    return photon_lines.assign_user(user["_id"])


@router.get("/status", response_model=ConnectStatus)
def status(user=Depends(current_user)):
    """This account's iMessage line. Refreshes opt-in from Photon at most once a minute."""
    return photon_lines.status_for(photon_lines.refresh_opt_in(user))
