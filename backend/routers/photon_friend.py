"""POST /photon/friend-reply: friend-style wording for a Photon reply (backend/services/friend_reply.py).

Agent-only (x-agent-secret, like /imessage/*). Never answers an error for model problems: any failure returns
existing_reply with used_fallback true. When verdict_id is given for a linked phone, missing verdict fields and
closet_context are filled from the stored verdict and the account's closet (read only).
"""
import logging

from bson import ObjectId
from fastapi import APIRouter, Header
from pydantic import BaseModel, Field

from backend.services import friend_reply

log = logging.getLogger("secondlook.photon_friend")
router = APIRouter(prefix="/photon", tags=["photon"])


class Turn(BaseModel):
    role: str = Field(max_length=20)
    text: str = Field(max_length=2000)


class FriendReplyRequest(BaseModel):
    sender_id: str = Field(max_length=80)
    text: str | None = Field(None, max_length=2000)
    has_image: bool = False
    verdict: dict | None = None
    radar: dict | None = None
    materials_line: str | None = Field(None, max_length=300)
    closet_context: dict | None = None
    history: list[Turn] | None = Field(None, max_length=friend_reply.HISTORY_MAX)
    existing_reply: str = Field("", max_length=4000, description="Sent unchanged on any fallback")
    verdict_id: str | None = None


class FriendReplyResponse(BaseModel):
    reply: str
    used_fallback: bool


def _enrich(req: dict) -> None:
    """Fill verdict and closet context from the database. Best effort: any error leaves req as given."""
    given = req.get("verdict") or {}
    if not req.get("verdict_id") or (given.get("reason") and req.get("closet_context")):
        return
    try:
        from backend.db import verdicts
        from backend.routers.imessage import user_for_phone
        from backend.services.account_context import closet

        user = user_for_phone(req["sender_id"])
        if user is None:
            return
        doc = verdicts.find_one({"_id": ObjectId(req["verdict_id"]), "user_id": user["_id"]},
                                {"verdict": 1, "reason": 1, "item.name": 1, "expected_wears": 1,
                                 "attributes": 1})
        if doc and not given.get("reason"):
            attrs = doc.get("attributes") or {}
            req["verdict"] = {**given, "reason": doc.get("reason"),
                              "item_name": (doc.get("item") or {}).get("name"),
                              "expected_wears": doc.get("expected_wears"),
                              "attributes": {k: attrs.get(k) for k in ("category", "color_family", "style")
                                             if attrs.get(k)},
                              "verdict": given.get("verdict") or doc.get("verdict")}
        if not req.get("closet_context"):
            c = closet(user["_id"])
            req["closet_context"] = {"total": c["total"], "by_category": c["by_category"],
                                     "items": [{"name": i["name"], "category": i["category"], "color": i["color"]}
                                               for i in c["items"][-15:]]}
    except Exception as e:  # noqa: BLE001
        log.info("friend reply: enrich skipped (%s)", type(e).__name__)


@router.post("/friend-reply", response_model=FriendReplyResponse)
def friend_reply_endpoint(body: FriendReplyRequest, x_agent_secret: str | None = Header(None)):
    from backend.routers.imessage import require_agent

    require_agent(x_agent_secret)
    req = body.model_dump()
    if friend_reply.enabled():
        _enrich(req)
    out = friend_reply.compose(req)
    return {"reply": out["reply"], "used_fallback": out["used_fallback"]}
