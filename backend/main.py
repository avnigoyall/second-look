"""Second Look API (Part 0: foundation). Run from the project root: uvicorn backend.main:app --reload"""
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from urllib.parse import quote, urlencode
from pymongo.errors import DuplicateKeyError, PyMongoError

from backend import models as m
from backend.alternatives import stored_verdict_alternative
from backend.auth import check_password, create_token, current_user, hash_password
from backend.db import client, closet_items, ensure_indexes, users, verdicts
from backend.images import decode_base64_image, to_data_url
from backend.impact import impact_summary
from backend.services.analysis_details import details_for
from backend.page_reader import ReadPageError, read_page
from backend.price_reader import read_price
from backend.phone import PhoneError, normalize_phone
from backend.routers import attributes as attributes_router
from backend.routers import closet as closet_router
from backend.routers import goal as goal_router
from backend.routers import imessage as imessage_router
from backend.routers import trends as trends_router
from backend.routers import social as social_router
from backend.services.closet import NO_VECTORS, auto_add_purchase, new_closet_doc
from backend.services.embeddings import EmbeddingError
from backend.services import photon_lines
from backend.services.goal import action_goal
from backend.services.money import money_kept, on_buy, on_skip, savings_summary
from backend.services.verdict import decide
from backend.settings import ROOT
from backend.warmup import start_warmup

@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_indexes()
    start_warmup()  # background: the first check after a restart is as fast as the rest
    yield


app = FastAPI(title="Second Look API", version="0.1.0", lifespan=lifespan)

# Hackathon, local only: allow every origin so the extension and dashboard can call us.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
                   expose_headers=["X-Server-Ms", "Server-Timing"])
app.include_router(attributes_router.router)
app.include_router(closet_router.router)
app.include_router(goal_router.router)
app.include_router(trends_router.router)
app.include_router(imessage_router.router)
app.include_router(social_router.router)


# uvicorn configures this logger at INFO, so timing lines show in the server output.
timing_log = logging.getLogger("uvicorn.error")
TIMED_PATHS = ("/analyze", "/read-page", "/read-price", "/attributes")


@app.middleware("http")
async def server_time(request: Request, call_next):
    """X-Server-Ms on every reply: server time from request to response, for the extension's timing line."""
    started = time.monotonic()
    response = await call_next(request)
    ms = int((time.monotonic() - started) * 1000)
    response.headers["X-Server-Ms"] = str(ms)
    if request.url.path in TIMED_PATHS:
        timing_log.info("timing %s server_ms=%d status=%d", request.url.path, ms, response.status_code)
    return response


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
                  preference_note=doc.get("preference_note"), created_at=doc["created_at"], phone=doc.get("phone"),
                  imessage_line=doc.get("photon_line") if photon_lines.has_assignment(doc) else None)


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
                     verdict=doc["verdict"], reason=doc["reason"],
                     alternatives=[stored_verdict_alternative(a) for a in doc.get("alternatives") or []],
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
def signup(body: m.SignupRequest, background: BackgroundTasks):
    """phone is optional. When given it is stored as E.164 (backend/phone.py), which links the account to iMessage,
    and a Photon iMessage line is assigned in the background after the response (never slows or fails signup).
    GET /imessage/status reports "assigning" until that finishes, then the line."""
    phone = None
    if body.phone and body.phone.strip():
        try:
            phone = normalize_phone(body.phone)
        except PhoneError as e:
            raise HTTPException(422, str(e))
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
    auto_assign = bool(phone) and photon_lines.configured()
    if phone:
        doc["phone"] = phone  # left out when absent: the unique index on phone is sparse
    if auto_assign:
        doc["photon_assigning_at"] = doc["created_at"]
    try:
        doc["_id"] = users.insert_one(doc).inserted_id
    except DuplicateKeyError as e:
        if phone and "phone" in str(e.details.get("keyPattern", {})):
            raise HTTPException(409, "This phone number is already linked to another account")
        raise HTTPException(409, "An account with this email already exists")
    if auto_assign:
        background.add_task(photon_lines.assign_user_quietly, doc["_id"])
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


# ---------- verdict flow ----------

@app.post("/analyze", response_model=m.AnalyzeResponse)
def analyze(body: m.AnalyzeRequest, http: Response, request: Request, user=Depends(current_user)):
    """Part 5 verdict engine (backend/services/verdict.py). Always answers: failures end in a code-only verdict.
    Server-Timing lists the engine's stage times (attributes, signals, claude, ...) in ms."""
    response, record = decide(user, body.item, save=True)
    if response.social_context and (ROOT / "dashboard" / "trends.html").is_file():
        response.social_context.trends_ui_url = str(request.base_url).rstrip("/") + "/trends-ui?" + urlencode(
            {"term": response.social_context.term})
    if response.radar and response.radar.matched_style:
        response.radar.timeline_url = str(request.base_url).rstrip("/") + "/social/trends/" + quote(
            response.radar.matched_style, safe="") + "/timeline"
    stored = record.get("attributes")
    if stored and stored.get("category") and stored.get("is_apparel") is not False:
        try:
            response.attributes = m.Attributes(**{k: stored.get(k) for k in m.Attributes.model_fields})
        except ValueError:
            pass  # the ownership question then reads them with /attributes, as before
    timings = record["signals"].get("timings") or {}
    http.headers["Server-Timing"] = ", ".join(f"{k.removesuffix('_ms')};dur={v}" for k, v in timings.items())
    timing_log.info("timing /analyze decided_by=%s %s", response.decided_by,
                    " ".join(f"{k}={v}" for k, v in timings.items()))
    return response


@app.post("/analyze/details", response_model=m.AnalysisDetails)
def analyze_details(body: m.AnalysisDetailsRequest, user=Depends(current_user)):
    """Full analysis for a verdict: materials, lifespan, cost per wear, closet sentiment, outfits.

    Second step after /analyze so the verdict stays fast. Always answers; failed parts are listed in errors.
    """
    return details_for(user, own_verdict(body.verdict_id, user))


@app.post("/action", response_model=m.ActionResponse)
def action(body: m.ActionRequest, background: BackgroundTasks, user=Depends(current_user)):
    """Records the action.

    "skipped" (Part 6): for Nessie users, moves the item price from Checking to Savings within about 3 s and
    returns real balances plus `transfer`. A slow or failed transfer answers "pending" and retries once in the
    background. Other users keep placeholder balances and make no Nessie call.
    "bought" adds the item to the closet as incoming (Part 3), after the response, unless the user turned off
    settings.auto_add_purchases. When an earlier skip of the same item moved money to Savings, that skip is
    superseded and the money moves back to Checking in the background (`reversed_cents`). No Nessie call inline.
    "goal" (section 15): only on skipped and only when the user has a goal, computed after the transfer.
    """
    doc = own_verdict(body.verdict_id, user)
    verdicts.update_one({"_id": doc["_id"]}, {"$set": {"action": body.action}})
    if body.action == "bought" and (user.get("settings") or {}).get("auto_add_purchases") is not False:
        background.add_task(auto_add_purchase, doc["_id"])
    transfer, balances = on_skip(user, doc, background) if body.action == "skipped" else (None, None)
    out = {"ok": True, "transfer": transfer,
           "balances": m.Balances(**balances, placeholder=False) if balances else m.Balances()}
    if body.action == "bought":
        out["reversed_cents"] = on_buy(user, doc, background)
    if body.action == "skipped":  # section 15: bought stays free of Nessie calls, so it carries no goal
        try:
            goal = action_goal(user, transfer, balances)
        except Exception as e:  # the goal line is extra: it never fails the skip
            timing_log.warning("action goal unavailable: %s", str(e)[:200])
            goal = None
        if goal:
            out["goal"] = goal
    return out


@app.get("/savings", response_model=m.SavingsResponse)
def savings(user=Depends(current_user)):
    """Checking and Savings in cents. source nessie for Nessie users when Nessie answers, else local."""
    return savings_summary(user)


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


@app.post("/read-price", response_model=m.ReadPriceResponse)
def read_price_endpoint(body: m.ReadPriceRequest, user=Depends(current_user)):
    """Last price tier: Claude picks the displayed price from text windows. Answers within about 4 s.
    Any failure, or a price that is not verbatim in the windows, gives {price: null, price_text: null}."""
    price, price_text = read_price(body.url, body.name, body.windows)
    return {"price": price, "price_text": price_text}


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
    recent = verdicts.find({"user_id": user["_id"]}).sort("created_at", -1).limit(50)
    co2e = impact_summary(user["_id"])
    waste_avoided = co2e["co2e_kg"] if co2e["co2e_items_counted"] else None  # kg CO2e, estimate (GET /impact)
    return {"money_kept": money_kept(user["_id"]), "waste_avoided": waste_avoided,
            "verdicts": [verdict_out(d) for d in recent], "cost_per_wear": None}


@app.get("/impact", response_model=m.ImpactResponse)
def impact(user=Depends(current_user)):
    """Popup summary on non-product pages: money kept and estimated CO2e avoided by skipped items."""
    return impact_summary(user["_id"], user)


@app.get("/cart-summary", response_model=m.CartSummaryResponse)
def cart_summary(user=Depends(current_user)):
    return m.CartSummaryResponse()


@app.get("/dashboard-ui", include_in_schema=False)
def dashboard_ui():
    return FileResponse(ROOT / "dashboard" / "index.html")


@app.get("/closet-ui", include_in_schema=False)
def closet_ui():
    return FileResponse(ROOT / "dashboard" / "closet.html")


@app.get("/trends-ui", include_in_schema=False)
def trends_ui():
    page = ROOT / "dashboard" / "trends.html"
    if not page.is_file():
        raise HTTPException(404, "Bluesky sample page is unavailable")
    return FileResponse(page)


@app.get("/graph-ui", include_in_schema=False)
def graph_ui():
    """Social Radar atlas explorer. Data comes from GET /social-atlas."""
    return FileResponse(ROOT / "dashboard" / "graph.html")


app.mount("/dashboard-assets", StaticFiles(directory=ROOT / "dashboard"), name="dashboard-assets")
