"""Pydantic request and response models. FastAPI's /docs page is built from these, so they are the API contract."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from backend.settings import ATTRIBUTES
from backend.trends import TrendAlternative, TrendFeatures, TrendSource

VerdictType = Literal["have_this_look", "micro_trend", "good_buy"]
ProfileType = Literal["demo", "new", "real"]
ClosetSource = Literal["snap", "auto_add", "ownership_answer", "quick_add"]


# ---------- shared pieces ----------

class Attributes(BaseModel):
    """Clothing attributes. Allowed values live in backend/config/attributes.json."""
    category: str | None = None
    color_family: str | None = None
    fabric: str | None = None
    fabric_confidence: float | None = Field(None, ge=0, le=1)
    cut: str | None = None
    style: str | None = None

    @model_validator(mode="after")
    def check_allowed_values(self):
        for field, allowed in ATTRIBUTES.items():
            value = getattr(self, field)
            if value is not None and value not in allowed:
                raise ValueError(f"{field} must be one of {allowed}, got '{value}'")
        return self


class Item(BaseModel):
    """A product the shopper is looking at in an online store."""
    name: str
    image_url: str | None = None
    price: float | None = Field(None, ge=0, description="Price in dollars")
    store: str | None = None
    url: str | None = None


class Alternative(BaseModel):
    name: str
    url: str | None = None
    price: float | None = None
    reason: str | None = None


class Outfit(BaseModel):
    name: str
    closet_item_ids: list[str] = []


class User(BaseModel):
    id: str
    email: str
    profile_type: ProfileType
    preference_note: str | None = None
    created_at: datetime


class ClosetItem(BaseModel):
    id: str
    user_id: str
    source: ClosetSource
    name: str
    image_data: str | None = None
    attributes: Attributes | None = None
    description: str = ""
    created_at: datetime


class TrendBase(BaseModel):
    """A trend from the trends collection. See backend/trends.py for the stored document and its rules."""
    id: str
    name: str
    category: str | None = None
    features: TrendFeatures = TrendFeatures()
    first_seen: datetime | None = None
    lifespan_weeks_est: float | None = Field(None, description="Set only when lifespan_source_url cites it")
    lifespan_bucket: Literal["short", "medium", "long"] | None = Field(
        None, description="short: up to 16 weeks, medium: up to 52, long: longer. Set only when lifespan_source_url cites it")
    lifespan_label: str | None = None
    lifespan_source_url: str | None = None
    sources: list[TrendSource] = []
    alternatives: list[TrendAlternative] = []
    origin: Literal["seed", "agent"]
    search_suggestions_html: str | None = Field(None, description="Google Search suggestions chip HTML. Agent trends only")


class Trend(TrendBase):
    example_images: list[str] = Field([], description="Image URLs or data URLs")


class TrendSummary(TrendBase):
    """GET /trends item: example images can be large data URLs, so only their count is sent."""
    example_image_count: int = 0


class Verdict(BaseModel):
    id: str
    item: Item
    attributes: Attributes | None = None
    verdict: VerdictType
    reason: str
    matched_closet_item_id: str | None = None
    matched_trend_id: str | None = None
    alternatives: list[Alternative] = []
    expected_wears: int | None = None
    action: Literal["none", "skipped", "bought"]
    feedback: Literal["none", "up", "down"]
    created_at: datetime


# ---------- requests ----------

class Credentials(BaseModel):
    email: str
    password: str = Field(min_length=6)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("not a valid email address")
        return v


class AnalyzeRequest(BaseModel):
    item: Item


class ActionRequest(BaseModel):
    verdict_id: str
    action: Literal["skipped", "bought"]


class FeedbackRequest(BaseModel):
    verdict_id: str
    value: Literal["up", "down"]


class ReadPageRequest(BaseModel):
    url: str
    text: str = Field(max_length=50000, description="Trimmed visible page text. Only the first 8000 characters are used")
    image_url: str | None = None


class ClosetCreateRequest(BaseModel):
    name: str | None = None
    image_base64: str | None = Field(None, description="Bare base64 or a data URL")
    attributes: Attributes | None = None
    source: ClosetSource


# ---------- responses ----------

class HealthResponse(BaseModel):
    ok: bool
    db: bool


class AuthResponse(BaseModel):
    token: str
    user: User


class MeResponse(BaseModel):
    user: User


class AnalyzeResponse(BaseModel):
    status: Literal["ok"]
    verdict_id: str
    verdict: VerdictType
    reason: str
    matched_closet_item: ClosetItem | None = None
    matched_trend: Trend | None = None
    alternatives: list[Alternative] = []
    outfits: list[Outfit] = []
    expected_wears: int | None = None
    cost_per_wear: float | None = None


class ReadPageResponse(BaseModel):
    name: str | None = None
    price: float | None = None
    image_url: str | None = None


class Balances(BaseModel):
    """Placeholder until Nessie is wired in. Nessie v2 reports integer cents."""
    checking_cents: int | None = None
    savings_cents: int | None = None
    placeholder: bool = True


class ActionResponse(BaseModel):
    ok: bool
    balances: Balances


class OkResponse(BaseModel):
    ok: bool


class ClosetItemResponse(BaseModel):
    item: ClosetItem


class ClosetListResponse(BaseModel):
    items: list[ClosetItem]


class DashboardResponse(BaseModel):
    money_kept: float
    waste_avoided: float | None = None
    verdicts: list[Verdict]
    cost_per_wear: float | None = None


class TrendsResponse(BaseModel):
    trends: list[TrendSummary]


class CartSummaryResponse(BaseModel):
    """Placeholder counts. Real cart tracking comes in a later part."""
    items_checked: int = 0
    have_this_look: int = 0
    micro_trend: int = 0
    good_buy: int = 0
    skipped: int = 0
    bought: int = 0
    placeholder: bool = True
