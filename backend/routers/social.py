"""Social sample read endpoints and authenticated, grounded questions."""
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator
from pymongo import timeout as mongo_timeout

from backend.auth import current_user
from backend.services import social_ask

router = APIRouter(tags=["social"])


class TrendAskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    term: str | None = Field(default=None, max_length=100)
    since: datetime | None = None
    until: datetime | None = None

    @field_validator("question")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("question must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def valid_dates(self):
        for value in (self.since, self.until):
            if value is not None and value.utcoffset() is None:
                raise ValueError("date filters require a UTC offset")
        if self.since and self.until and self.since >= self.until:
            raise ValueError("since must precede until")
        return self


class SocialSource(BaseModel):
    uri: str
    url: str
    snippet: str = Field(max_length=160)
    community_name: str | None = None


class TrendAskResponse(BaseModel):
    answer: str
    sources: list[SocialSource]
    status: Literal["ok", "no_match", "unavailable"]


@router.post("/trend-ask", response_model=TrendAskResponse)
def trend_ask(body: TrendAskRequest, user=Depends(current_user)):
    return social_ask.ask(body.question, term=body.term, since=body.since, until=body.until)


@router.get("/social-trends")
def social_trends():
    try:
        with mongo_timeout(2):
            rows = list(social_ask.database()["social_trends"].find({}, {"_id": 0, "embedding": 0}))
        return {"trends": sorted(rows, key=lambda r: r.get("term", ""))}
    except Exception:
        raise HTTPException(503, "Bluesky sample trends are unavailable") from None


@router.get("/social-graph")
def social_graph():
    try:
        with mongo_timeout(2):
            row = social_ask.database()["social_graph_meta"].find_one({"_id": "graph"}, {"_id": 0, "graph": 1})
        if not row or not row.get("graph"):
            raise HTTPException(404, "Bluesky community graph has not been built")
        return row["graph"]
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "Bluesky community graph is unavailable") from None
