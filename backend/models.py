"""Pydantic request and response models. FastAPI's /docs page is built from these, so they are the API contract."""
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from backend.settings import ATTRIBUTES
from backend.trends import TrendAlternative, TrendFeatures, TrendSource

VerdictType = Literal["have_this_look", "micro_trend", "good_buy"]
ProfileType = Literal["demo", "new", "real"]
ClosetSource = Literal["snap", "auto_add", "ownership_answer", "quick_add",
                       "ingest_photo", "ingest_video", "ingest_text"]
ClosetStatus = Literal["owned", "incoming"]
FabricConfidence = Literal["high", "medium", "low"]
CategoryCheck = Literal["agreed", "fixed_by_crop", "fixed_by_text", "forced_from_name", "no_name_category"]
DecidedBy = Literal["code", "claude", "fallback"]
LookZone = Literal["forced", "gray", "none"]


# ---------- shared pieces ----------

class Attributes(BaseModel):
    """Clothing attributes. Allowed values live in backend/config/attributes.json."""
    category: str | None = None
    color_family: str | None = None
    fabric: str | None = None
    # Part 0 used a 0 to 1 number. Part 2 and Part 3 store "high" | "medium" | "low". Both are accepted.
    fabric_confidence: Annotated[float, Field(ge=0, le=1)] | FabricConfidence | None = None
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
    description: str | None = Field(None, max_length=20000, description="Store description, if the page has one")
    breadcrumb: str | None = Field(None, max_length=2000, description="Category path, for example 'Men > Pants'")


class Alternative(BaseModel):
    """Something to buy instead (micro_trend verdicts). Always has a link: a product page, or a store
    search page when link_kind is "search". Build these with backend.alternatives.alternatives_for_trend."""
    name: str
    url: str = Field(min_length=1, description="Product page, or store search page when link_kind is search")
    price: float | None = None
    reason: str | None = None
    store: str | None = None
    link_kind: Literal["product", "search"] = "product"


class Outfit(BaseModel):
    name: str
    closet_item_ids: list[str] = []


class User(BaseModel):
    id: str
    email: str
    profile_type: ProfileType
    preference_note: str | None = None
    created_at: datetime
    phone: str | None = Field(None, description="E.164. Links the account to the iMessage agent (imessage/)")


class ClosetItem(BaseModel):
    id: str
    user_id: str
    source: ClosetSource
    status: ClosetStatus = Field("owned", description="A stored document with no status is owned")
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
    target_crop: str | None = Field(
        None, description="JPEG data URL of only the target item (understand_target_item). Set by Part 5")


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


class SignupRequest(Credentials):
    phone: str | None = Field(None, max_length=40,
                              description="Optional. Any common format; stored as E.164 to link iMessage")


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


class ReadPriceRequest(BaseModel):
    url: str = Field(max_length=4000)
    name: str | None = Field(None, max_length=300)
    windows: list[Annotated[str, Field(max_length=2000)]] = Field(
        min_length=1, max_length=24, description="Visible page text around each currency amount and the add-to-cart "
                                                 "area. At most 24 windows, 2000 characters each, 8000 in total")

    @field_validator("windows")
    @classmethod
    def total_size(cls, windows: list[str]) -> list[str]:
        if sum(len(w) for w in windows) > 8000:
            raise ValueError("windows have more than 8000 characters in total")
        if not any(w.strip() for w in windows):
            raise ValueError("windows are empty")
        return windows


class AttributesRequest(BaseModel):
    name: str = Field(min_length=1)
    image_url: str | None = None
    description: str | None = Field(None, max_length=20000)
    breadcrumb: str | None = Field(None, max_length=2000)
    text: str | None = Field(None, max_length=50000, description="Extra page text. Only the first 4000 characters are used")


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
    # Part 5 additions (all optional, additive only)
    matched_closet_items: list[ClosetItem] = Field(
        [], description="Up to 3 matched closet items with image_data, best first. matched_closet_item is the first")
    decided_by: DecidedBy | None = Field(
        None, description="code: rules decided (Claude may have written the reason). claude: Claude chose within "
                          "the allowed set. fallback: Claude failed, code-only verdict with a template reason")
    closet_small: bool | None = Field(None, description="True while the closet has fewer than 10 items")
    zone: LookZone | None = Field(
        None, description="have_this_look zone from the best closet score. null when matching did not run "
                          "(not apparel, attributes failed, embedding or Atlas error): say nothing about ownership")
    wears_estimated: bool = Field(False, description="True when expected_wears is a rough category estimate. "
                                                      "Show it labeled as an estimate")
    attributes: Attributes | None = Field(
        None, description="Part 2 attributes of the item being added (POST /analyze only). null when attributes "
                          "failed or the item is not apparel. The extension's ownership question reads them, so it "
                          "needs no second /attributes call")


class ReadPageResponse(BaseModel):
    name: str | None = None
    price: float | None = None
    image_url: str | None = None


class ReadPriceResponse(BaseModel):
    price: float | None = None
    price_text: str | None = Field(None, description="The price exactly as shown on the page, for example \"$39.90\"")


class ItemAttributesResponse(BaseModel):
    """Part 2 item understanding. Every attribute is null when status is "attributes_failed"."""
    category: str | None = None
    color_family: str | None = None
    fabric: str | None = None
    fabric_confidence: Literal["high", "medium", "low"] | None = None
    cut: str | None = None
    style: str | None = None
    description: str | None = Field(None, description="25 words at most")
    is_apparel: bool | None = None
    status: Literal["ok", "attributes_failed"]
    attribute_string: str = Field(description="The text Part 3 embeds. Empty when status is attributes_failed")
    target_box_2d: list[int] | None = Field(
        None, min_length=4, max_length=4,
        description="[ymin, xmin, ymax, xmax] 0 to 1000 around the target item. Null when invalid or not trusted")
    other_garments_visible: bool | None = Field(None, description="The image shows other clothes, shoes or bags")
    category_check: CategoryCheck | None = Field(
        None, description="Name-first category check. Null when status is attributes_failed")
    target_crop: str | None = Field(None, description="JPEG data URL of the target item, 600px longest side at most")

    @model_validator(mode="after")
    def check_allowed_values(self):
        for field, allowed in ATTRIBUTES.items():
            value = getattr(self, field)
            if value is not None and value not in allowed:
                raise ValueError(f"{field} must be one of {allowed}, got '{value}'")
        return self


class Balances(BaseModel):
    """Nessie balances in integer cents, counting money still pending (Part 6). Null with placeholder true
    when the user is not on Nessie, Nessie is down, or the action was "bought"."""
    checking_cents: int | None = None
    savings_cents: int | None = None
    placeholder: bool = True


TransferStatus = Literal["done", "pending", "failed", "no_price"]


class TransferResult(BaseModel):
    """Skip it transfer from Checking to Savings. done: Nessie accepted it. pending: still being tried in the
    background. failed: gave up. no_price: the item had no price, so nothing moved."""
    status: TransferStatus
    amount_cents: int | None = None


class ActionResponse(BaseModel):
    ok: bool
    balances: Balances
    transfer: TransferResult | None = Field(None, description="Set on skipped for Nessie users only (Part 6)")


class SavingsTransfer(BaseModel):
    amount_cents: int
    description: str
    at: datetime


class SavingsResponse(BaseModel):
    """source nessie: live Nessie balances and transfers into Savings. source local: savings_cents is
    round(money_kept * 100), checking_cents is null, transfers come from skipped verdicts."""
    checking_cents: int | None = None
    savings_cents: int
    transfers: list[SavingsTransfer]
    source: Literal["nessie", "local"]


class OkResponse(BaseModel):
    ok: bool


class ClosetItemResponse(BaseModel):
    item: ClosetItem


class ClosetListResponse(BaseModel):
    items: list[ClosetItem]


# ---------- Part 3: closet intake, matching, ownership, settings ----------

class DuplicateRef(BaseModel):
    id: str
    name: str
    score: float = Field(description="Cosine similarity to the existing closet item")


class CandidateItem(BaseModel):
    """A garment found by closet intake, waiting for the user to review and save it."""
    id: str
    origin: Literal["photo", "video", "text"]
    name: str
    attributes: Attributes
    description: str = ""
    confidence: float = Field(ge=0, le=1)
    low_confidence: bool = False
    image_index: int | None = None
    frame: int | None = None
    box_2d: list[int] | None = Field(None, description="[ymin, xmin, ymax, xmax] normalized 0 to 1000")
    brand_visible: str | None = None
    seen_count: int = Field(1, description="How many photos or frames showed this item after merging")
    thumbnail: str | None = Field(None, description="JPEG data URL, 600px longest side")
    likely_duplicate_of: DuplicateRef | None = None


class IngestProgress(BaseModel):
    step: str
    done: int
    total: int


class IngestStartResponse(BaseModel):
    job_id: str


class IngestJobResponse(BaseModel):
    job_id: str
    status: Literal["queued", "running", "done", "failed", "saved"]
    progress: IngestProgress
    items: list[CandidateItem] = Field([], description="Filled when status is done or saved")
    warnings: list[str] = []
    error: str | None = None
    saved_count: int | None = None


class SaveItem(BaseModel):
    candidate_id: str | None = Field(None, description="Links the item to its crop. Omit for items added by text")
    name: str = Field(min_length=1, max_length=120)
    attributes: Attributes
    description: str = Field("", max_length=500)


class IngestSaveRequest(BaseModel):
    items: list[SaveItem] = Field(max_length=100)


class IngestSaveResponse(BaseModel):
    saved: int
    ids: list[str]


class ParseTextRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class CandidateListResponse(BaseModel):
    items: list[CandidateItem]


class OwnershipOptionsRequest(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    attributes: Attributes


class OwnershipOption(BaseModel):
    label: str
    attributes: Attributes


class OwnershipOptionsResponse(BaseModel):
    show: bool = Field(description="True only while the closet has fewer than 10 items")
    options: list[OwnershipOption] = []
    staple_idea: str | None = Field(None, description='One sentence, starts with "Idea: "')


class OwnershipAnswerRequest(BaseModel):
    attributes: Attributes
    name: str | None = Field(None, max_length=120, description='Defaults to "<color_family> <category>"')


class Settings(BaseModel):
    auto_add_purchases: bool = True


class SettingsPatch(BaseModel):
    auto_add_purchases: bool | None = None


class DashboardResponse(BaseModel):
    money_kept: float
    waste_avoided: float | None = None
    verdicts: list[Verdict]
    cost_per_wear: float | None = None


class ImpactSource(BaseModel):
    category: str
    kg_co2e: float
    source: str
    url: str


class ImpactResponse(BaseModel):
    """GET /impact. Totals over verdicts the shopper skipped. CO2e is an estimate from config/impact.json."""
    money_kept: float
    items_skipped: int
    co2e_kg: float = Field(description="Lifecycle kg CO2e of skipped items in categories with a sourced figure")
    co2e_items_counted: int = Field(description="How many skipped items had a sourced figure")
    co2e_sources: list[ImpactSource] = []


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
