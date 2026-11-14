"""Second Look API (Part 0: foundation). Run from the project root: uvicorn backend.main:app --reload"""
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pymongo.errors import DuplicateKeyError, PyMongoError

from backend import models as m
from backend.auth import check_password, create_token, current_user, hash_password
from backend.db import client, closet_items, ensure_indexes, trends, users, verdicts
from backend.images import decode_base64_image, to_data_url
from backend.page_reader import ReadPageError, read_page
from backend.routers import attributes as attributes_router
from backend.routers import closet as closet_router
from backend.routers import trends as trends_router
from backend.services.closet import NO_VECTORS, auto_add_purchase, new_closet_doc
from backend.services.embeddings import EmbeddingError
from backend.settings import ROOT

@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_indexes()
    yield


app = FastAPI(title="Second Look API", version="0.1.0", lifespan=lifespan)

# Hackathon, local only: allow every origin so the extension and dashboard can call us.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(attributes_router.router)
app.include_router(closet_router.router)
app.include_router(trends_router.router)


def now():
    return datetime.now(timezone.utc)


def oid(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise HTTPException(404, "Not found")


# ---------- document -> response converters ----------

def user_out(doc) -> m.User:
    return m.User(id=str(doc["_id"]), email=doc["email"], profile_type=doc["profile_type"],
                  preference_note=doc.get("preference_note"), created_at=doc["created_at"])


def closet_out(doc, include_image=True) -> m.ClosetItem:
    return m.ClosetItem(id=str(doc["_id"]), user_id=str(doc["user_id"]), source=doc["source"],
                        status=doc.get("status") or "owned", name=doc["name"], image_data=doc.get("image_data") if include_image else None,
                        attributes=doc.get("attributes"), description=doc.get("description", ""),
                        created_at=doc["created_at"])


def trend_out(doc) -> m.Trend:
    return m.Trend(id=str(doc["_id"]), **{k: v for k, v in doc.items() if k != "_id"})


def verdict_out(doc) -> m.Verdict:
    ids = {k: str(doc[k]) if doc.get(k) else None for k in ("matched_closet_item_id", "matched_trend_id")}
    return m.Verdict(id=str(doc["_id"]), item=doc["item"], attributes=doc.get("attributes"),
                     verdict=doc["verdict"], reason=doc["reason"], alternatives=doc.get("alternatives", []),
                     expected_wears=doc.get("expected_wears"), action=doc["action"],
                     feedback=doc["feedback"], created_at=doc["created_at"], **ids)


def own_verdict(verdict_id: str, user) -> dict:
    doc = verdicts.find_one({"_id": oid(verdict_id), "user_id": user["_id"]})
    if doc is None:
        raise HTTPException(404, "Verdict not found")
    return doc


# ---------- health and auth ----------

@app.get("/health", response_model=m.HealthResponse)
def health():
    try:
        client.admin.command("ping")
        db_ok = True
    except PyMongoError:
        db_ok = False
    return {"ok": True, "db": db_ok}


@app.post("/auth/signup", response_model=m.AuthResponse)
def signup(body: m.Credentials):
    doc = {
        "email": body.email,
        "password_hash": hash_password(body.password),
        "profile_type": "real",
        "nessie_customer_id": None,
        "nessie_checking_id": None,
        "nessie_savings_id": None,
        "preference_note": None,
        "created_at": now(),
    }
    try:
        doc["_id"] = users.insert_one(doc).inserted_id
    except DuplicateKeyError:
        raise HTTPException(409, "An account with this email already exists")
    return {"token": create_token(str(doc["_id"])), "user": user_out(doc)}


@app.post("/auth/login", response_model=m.AuthResponse)
def login(body: m.Credentials):
    doc = users.find_one({"email": body.email})
    if doc is None or not check_password(body.password, doc["password_hash"]):
        raise HTTPException(401, "Wrong email or password")
    return {"token": create_token(str(doc["_id"])), "user": user_out(doc)}


@app.get("/auth/me", response_model=m.MeResponse)
def me(user=Depends(current_user)):
    return {"user": user_out(user)}


# ---------- verdict flow (stubs) ----------

VERDICT_CYCLE = ["have_this_look", "micro_trend", "good_buy"]


@app.post("/analyze", response_model=m.AnalyzeResponse)
def analyze(body: m.AnalyzeRequest, user=Depends(current_user)):
    """STUB: cycles through the three verdicts using this user's verdict count. No AI yet."""
    verdict = VERDICT_CYCLE[verdicts.count_documents({"user_id": user["_id"]}) % 3]
    item = body.item
    matched_closet = matched_trend = None
    alternatives, outfits, expected_wears = [], [], None

    if verdict == "have_this_look":
        matched_closet = closet_items.find_one({"user_id": user["_id"]}, sort=[("created_at", 1)])
        reason = "You already own something very close to this. (Stub reason.)"
    elif verdict == "micro_trend":
        matched_trend = trends.find_one({}, sort=[("_id", 1)])
        reason = "This look is peaking now and will likely fade within a few months. (Stub reason.)"
        alternatives = [m.Alternative(name="Classic straight-leg version", price=48.0,
                                      reason="Same feel, lasts longer. (Stub alternative.)")]
    else:
        reason = "This fills a real gap in your closet and pairs with what you own. (Stub reason.)"
        expected_wears = 30
        outfits = [m.Outfit(name="Everyday outfit (stub)")]

    cost_per_wear = round(item.price / expected_wears, 2) if item.price and expected_wears else None
    doc = {
        "user_id": user["_id"],
        "item": item.model_dump(),
        "attributes": None,
        "verdict": verdict,
        "reason": reason,
        "matched_closet_item_id": matched_closet["_id"] if matched_closet else None,
        "matched_trend_id": matched_trend["_id"] if matched_trend else None,
        "alternatives": [a.model_dump() for a in alternatives],
        "expected_wears": expected_wears,
        "action": "none",
        "feedback": "none",
        "created_at": now(),
    }
    verdict_id = verdicts.insert_one(doc).inserted_id
    return m.AnalyzeResponse(
        status="ok", verdict_id=str(verdict_id), verdict=verdict, reason=reason,
        matched_closet_item=closet_out(matched_closet, include_image=False) if matched_closet else None,
        matched_trend=trend_out(matched_trend) if matched_trend else None,
        alternatives=alternatives, outfits=outfits, expected_wears=expected_wears,
        cost_per_wear=cost_per_wear,
    )


@app.post("/action", response_model=m.ActionResponse)
def action(body: m.ActionRequest, background: BackgroundTasks, user=Depends(current_user)):
    """STUB: records the action. Balances are a placeholder until Nessie is wired in.

    "bought" also adds the item to the closet as incoming (Part 3), after the response, unless the user
    turned off settings.auto_add_purchases. The response does not change.
    """
    doc = own_verdict(body.verdict_id, user)
    verdicts.update_one({"_id": doc["_id"]}, {"$set": {"action": body.action}})
    if body.action == "bought" and (user.get("settings") or {}).get("auto_add_purchases") is not False:
        background.add_task(auto_add_purchase, doc["_id"])
    return {"ok": True, "balances": m.Balances()}


@app.post("/feedback", response_model=m.OkResponse)
def feedback(body: m.FeedbackRequest, user=Depends(current_user)):
    doc = own_verdict(body.verdict_id, user)
    verdicts.update_one({"_id": doc["_id"]}, {"$set": {"feedback": body.value}})
    return {"ok": True}


# ---------- item reading ----------

@app.post("/read-page", response_model=m.ReadPageResponse)
def read_page_endpoint(body: m.ReadPageRequest, user=Depends(current_user)):
    """Tier 3 reader: Gemini extracts name, price and image from trimmed page text."""
    if not body.text.strip():
        raise HTTPException(422, "text is empty")
    try:
        item = read_page(body.url, body.text, body.image_url)
    except ReadPageError as e:
        raise HTTPException(502, str(e))
    return item.model_dump()


# ---------- closet ----------

@app.post("/closet", response_model=m.ClosetItemResponse)
def add_closet_item(body: m.ClosetCreateRequest, user=Depends(current_user)):
    if not body.name and not body.image_base64:
        raise HTTPException(422, "Provide a name, an image, or both")
    image_data = None
    if body.image_base64:
        try:
            image_data = to_data_url(decode_base64_image(body.image_base64))
        except Exception:
            raise HTTPException(422, "image_base64 is not a readable image")
    try:
        doc = new_closet_doc(user["_id"], name=body.name or "Untitled item", source=body.source,
                             attributes=body.attributes.model_dump() if body.attributes else None,
                             image_data=image_data)
    except EmbeddingError as e:
        raise HTTPException(502, f"Could not save: {e}")
    doc["_id"] = closet_items.insert_one(doc).inserted_id
    return {"item": closet_out(doc)}


@app.get("/closet", response_model=m.ClosetListResponse)
def list_closet(include_images: int = 0, status: m.ClosetStatus | None = None, user=Depends(current_user)):
    """status filters to owned or incoming items (a stored item with no status counts as owned)."""
    projection = NO_VECTORS if include_images else {**NO_VECTORS, "image_data": 0}
    query = {"user_id": user["_id"]}
    if status == "owned":
        query["status"] = {"$in": ["owned", None]}  # None also matches a missing field
    elif status == "incoming":
        query["status"] = "incoming"
    docs = closet_items.find(query, projection).sort("created_at", 1)
    return {"items": [closet_out(d, include_image=bool(include_images)) for d in docs]}


# ---------- dashboard ----------

@app.get("/dashboard", response_model=m.DashboardResponse)
def dashboard(user=Depends(current_user)):
    pipeline = [
        {"$match": {"user_id": user["_id"], "action": "skipped"}},
        {"$group": {"_id": None, "total": {"$sum": "$item.price"}}},
    ]
    result = list(verdicts.aggregate(pipeline))
    money_kept = round(result[0]["total"], 2) if result else 0.0
    recent = verdicts.find({"user_id": user["_id"]}).sort("created_at", -1).limit(50)
    return {"money_kept": money_kept, "waste_avoided": None,
            "verdicts": [verdict_out(d) for d in recent], "cost_per_wear": None}


@app.get("/cart-summary", response_model=m.CartSummaryResponse)
def cart_summary(user=Depends(current_user)):
    return m.CartSummaryResponse()


@app.get("/dashboard-ui", include_in_schema=False)
def dashboard_ui():
    return FileResponse(ROOT / "dashboard" / "index.html")


@app.get("/closet-ui", include_in_schema=False)
def closet_ui():
    return FileResponse(ROOT / "dashboard" / "closet.html")


app.mount("/dashboard-assets", StaticFiles(directory=ROOT / "dashboard"), name="dashboard-assets")
