"""Trend documents: validation shared by the seed script and the trend agent, plus read helpers.

A trend document in the `trends` collection:
    name, category, features {cut, color, fabric}, first_seen, lifespan_weeks_est, lifespan_label,
    lifespan_source_url, sources [{title, url}], example_images, alternatives, origin,
    search_suggestions_html (agent trends only)

Rule: lifespan_weeks_est may be a number only when lifespan_source_url names one of the trend's
sources. Otherwise it is null and lifespan_label carries the evidence in words.
"""
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from pymongo.collation import Collation

from backend.settings import ATTRIBUTES

# Case-insensitive comparison for trend names: the unique index and every upsert use it.
NAME_COLLATION = Collation(locale="en", strength=2)


def name_key(name: str) -> str:
    """The form used to compare trend names: case-insensitive, whitespace collapsed."""
    return re.sub(r"\s+", " ", name).strip().casefold()


class TrendSource(BaseModel):
    title: str = Field(min_length=1)
    url: str

    @field_validator("url")
    @classmethod
    def http_url(cls, v: str) -> str:
        v = v.strip()
        if not re.match(r"^https?://\S+$", v):
            raise ValueError(f"source url must start with http:// or https://, got '{v}'")
        return v


class TrendFeatures(BaseModel):
    """Free text, but values from backend/config/attributes.json (cut, color_family, fabric) match best."""
    cut: str | None = None
    color: str | None = None
    fabric: str | None = None


class TrendAlternative(BaseModel):
    """A longer-lasting piece to suggest instead. url only when a source gave it."""
    type: str = Field(min_length=1, description="Type of piece, for example 'straight-leg dark wash jeans'")
    why_it_lasts: str = Field(min_length=1)
    where_to_look: str = Field(min_length=1, description="Kind of store or section, not a product link")
    url: str | None = None


class TrendDoc(BaseModel):
    """One trend as stored. Both the seed file and the agent output are checked against this."""
    name: str = Field(min_length=1)
    category: str | None = None
    features: TrendFeatures = TrendFeatures()
    first_seen: datetime | None = None
    lifespan_weeks_est: float | None = Field(None, gt=0)
    lifespan_label: str = Field(min_length=1)
    lifespan_source_url: str | None = None
    sources: list[TrendSource] = Field(min_length=1)
    example_images: list[str] = []
    alternatives: list[TrendAlternative] = []
    origin: Literal["seed", "agent"]
    search_suggestions_html: str | None = None

    @field_validator("name")
    @classmethod
    def clean_name(cls, v: str) -> str:
        return re.sub(r"\s+", " ", v).strip()

    @model_validator(mode="after")
    def check_rules(self):
        source_urls = {s.url for s in self.sources}
        if self.lifespan_weeks_est is not None and self.lifespan_source_url not in source_urls:
            raise ValueError("lifespan_weeks_est is set, so lifespan_source_url must be one of the trend's source urls")
        if self.lifespan_weeks_est is None:
            self.lifespan_source_url = None
        for alt in self.alternatives:
            if alt.url and alt.url not in source_urls:
                raise ValueError(f"alternative url '{alt.url}' is not one of the trend's sources")
        if self.origin == "seed" and self.search_suggestions_html is not None:
            raise ValueError("search_suggestions_html is for agent trends only")
        return self

    def vocabulary_warnings(self) -> list[str]:
        """Values outside attributes.json. Allowed, but matching in Part 5 works best with listed values."""
        checks = [("category", self.category, "category"), ("features.cut", self.features.cut, "cut"),
                  ("features.color", self.features.color, "color_family"),
                  ("features.fabric", self.features.fabric, "fabric")]
        return [f"{label} '{value}' is not in attributes.json {key}"
                for label, value, key in checks if value is not None and value not in ATTRIBUTES[key]]


def ensure_trend_indexes(trends):
    trends.create_index("name", unique=True, collation=NAME_COLLATION, name="name_ci_unique")


def get_trends_for_prompt() -> list[dict]:
    """Compact trend list for the verdict prompt (Part 5). No URLs, images, or HTML."""
    from backend.db import trends

    docs = trends.find({}, {"name": 1, "category": 1, "features": 1, "lifespan_label": 1, "sources.title": 1})
    return [{
        "name": d["name"],
        "category": d.get("category"),
        "features": {k: (d.get("features") or {}).get(k) for k in ("cut", "color", "fabric")},
        "lifespan_label": d.get("lifespan_label"),
        "source_titles": [s["title"] for s in d.get("sources", []) if s.get("title")],
    } for d in docs.sort("name", 1)]
