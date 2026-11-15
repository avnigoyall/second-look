"""Social sample read endpoints and authenticated, grounded questions."""
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
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


ATLAS_PATH = Path(__file__).resolve().parents[2] / ".social_runs" / "social_atlas.json"
ATLAS_AUTHORS_PATH = ATLAS_PATH.with_name("social_atlas_authors.json")
_ATLAS_AUTHORS: dict = {"mtime": None, "rows": {}}


@router.get("/social-atlas")
def social_atlas():
    """Precomputed public aggregate atlas (scripts/social_atlas.py). Hash prefixes only, sanitized snippets."""
    if not ATLAS_PATH.is_file():
        raise HTTPException(404, "Run scripts/social_atlas.py to build the atlas")
    return FileResponse(ATLAS_PATH, media_type="application/json")


@router.get("/social-atlas/author/{prefix}")
def social_atlas_author(prefix: str):
    """One author's sampled styles and up to three sanitized posts, loaded on click."""
    if not re.fullmatch(r"[0-9a-f]{10}", prefix):
        raise HTTPException(422, "Author id must be a 10-character hash prefix")
    if not ATLAS_AUTHORS_PATH.is_file():
        raise HTTPException(404, "Run scripts/social_atlas.py to build the atlas")
    mtime = ATLAS_AUTHORS_PATH.stat().st_mtime
    if _ATLAS_AUTHORS["mtime"] != mtime:
        _ATLAS_AUTHORS.update(mtime=mtime, rows=json.loads(ATLAS_AUTHORS_PATH.read_text()))
    row = _ATLAS_AUTHORS["rows"].get(prefix)
    if row is None:
        raise HTTPException(404, "No sampled posts for this author")
    return row


ATLAS_CONNECTIONS_PATH = ATLAS_PATH.with_name("social_atlas_connections.json")
_ATLAS_CONNECTIONS: dict = {"mtime": None, "rows": {}}
_NODE = re.compile(r"([sca])(\d{1,6})")


@router.get("/social-atlas/connection/{first}/{second}")
def social_atlas_connection(first: str, second: str):
    """What links two atlas nodes (s=style, c=community, a=author index), with sanitized example posts.

    Returns an empty connection when the sample holds no shared posts or interactions.
    """
    nodes = [_NODE.fullmatch(first), _NODE.fullmatch(second)]
    if not all(nodes) or first == second:
        raise HTTPException(422, "Nodes look like s3, c12 or a4051, and must differ")
    order = {"s": 0, "c": 1, "a": 2}
    a, b = sorted(((m.group(1), int(m.group(2))) for m in nodes), key=lambda v: (order[v[0]], v[1]))
    if not ATLAS_CONNECTIONS_PATH.is_file():
        raise HTTPException(404, "Run scripts/social_atlas.py to build the atlas")
    mtime = ATLAS_CONNECTIONS_PATH.stat().st_mtime
    if _ATLAS_CONNECTIONS["mtime"] != mtime:
        _ATLAS_CONNECTIONS.update(mtime=mtime, rows=json.loads(ATLAS_CONNECTIONS_PATH.read_text()))
    return _ATLAS_CONNECTIONS["rows"].get(f"{a[0]}{a[1]}|{b[0]}{b[1]}", {})
