"""Pydantic request and response models. FastAPI's /docs page is built from these, so they are the API contract."""
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from backend.settings import ATTRIBUTES

VerdictType = Literal["have_this_look", "micro_trend", "good_buy"]
ProfileType = Literal["demo", "new", "real"]
ClosetSource = Literal["snap", "auto_add", "ownership_answer", "quick_add",
                       "ingest_photo", "ingest_video", "ingest_text"]
ClosetStatus = Literal["owned", "incoming"]
FabricConfidence = Literal["high", "medium", "low"]


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
    status: ClosetStatus = Field("owned", description="A stored document with no status is owned")
    name: str
    image_data: str | None = None
    attributes: Attributes | None = None
    description: str = ""
    created_at: datetime


class Trend(BaseModel):
    id: str
    name: str
    features: dict
    first_seen: datetime | None = None
    lifespan_weeks_est: int | None = None
    lifespan_label: str | None = None
    sources: list[str] = []
    example_images: list[str] = []
    alternatives: list[Alternative] = []
    origin: Literal["seed", "agent"]


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


class ReadPageResponse(BaseModel):
    name: str | None = None
    price: float | None = None
    image_url: str | None = None


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

    @model_validator(mode="after")
    def check_allowed_values(self):
        for field, allowed in ATTRIBUTES.items():
            value = getattr(self, field)
            if value is not None and value not in allowed:
                raise ValueError(f"{field} must be one of {allowed}, got '{value}'")
        return self


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


class CartSummaryResponse(BaseModel):
    """Placeholder counts. Real cart tracking comes in a later part."""
    items_checked: int = 0
    have_this_look: int = 0
    micro_trend: int = 0
    good_buy: int = 0
    skipped: int = 0
    bought: int = 0
    placeholder: bool = True
