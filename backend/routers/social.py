"""Social sample read endpoints and authenticated, grounded questions."""
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator
from pymongo import timeout as mongo_timeout

from backend.auth import current_user
from backend.services import micro_trend, radar_read, social_ask
from backend.trends import NAME_COLLATION

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


TIMELINE_NOTE = ("Bluesky sample estimate. share is the term's posts divided by that week's sampled corpus. "
                 "Saturated weeks are lower bounds. Not platform totals and not market-wide sales.")


def _series(doc: dict) -> list[dict]:
    cfg = micro_trend.rule_config()
    metric = micro_trend.metric_weeks(doc)
    shares = [(w.get("count") or 0) / w["corpus_count"] if w.get("corpus_count") else 0.0 for w in metric]
    smooth = dict(zip([w["week"] for w in metric],
                      micro_trend._smooth(shares, int(cfg["smoothing_window_weeks"]))))
    rows = []
    for w in doc.get("weekly_counts") or []:
        week = w.get("week")
        corpus = w.get("corpus_count") or 0
        rows.append({"week": week, "count": w.get("count") or 0, "corpus_count": corpus,
                     "share": (w.get("count") or 0) / corpus if corpus else 0.0,
                     "smoothed_share": smooth.get(week), "partial_edge": week not in smooth,
                     "saturated": bool(w.get("saturated"))})
    return rows


def _markers(metrics: dict, run: dict | None) -> list[dict]:
    markers = []
    if metrics.get("peak_week"):
        markers.append({"week": metrics["peak_week"], "kind": "peak", "label": "Sample peak",
                        "censored": bool(metrics.get("peak_censored"))})
    if run:
        markers.append({"week": run["start_week"], "kind": "active_start", "label": "Sample run start"})
        markers.append({"week": run["end_week"], "kind": "active_end", "label": "Sample run end"})
    return markers


@router.get("/social/trends/{style}/timeline")
def social_trend_timeline(style: str):
    """Weekly sample series, rule markers and the latest Radar decision for one social_trends term.

    agent_label is null until an agent decision exists. Manual styles carry the editorial label and source text
    and never a sample run or lifespan.
    """
    try:
        db = social_ask.database()
        with mongo_timeout(2):
            doc = db["social_trends"].find_one({"term": style}, {"_id": 0, "embedding": 0})
            if doc is None:
                doc = db["social_trends"].find_one({"term": style.strip()}, {"_id": 0, "embedding": 0},
                                                   collation=NAME_COLLATION)
    except Exception:
        raise HTTPException(503, "Bluesky sample trends are unavailable") from None
    if not doc:
        raise HTTPException(404, "No Bluesky sample trend for this style")
    term = doc["term"]
    metrics = micro_trend.compute_rule_metrics(doc)
    manual = micro_trend.manual_decision(term)
    decision = radar_read.latest_decision(term, db)
    run = None if manual else radar_read.sample_run(metrics)
    out = {
        "style": term,
        "series": _series(doc),
        "markers": _markers(metrics, run),
        "rule_label": metrics["rule_label"],
        "rule_reasons": metrics["reasons"],
        "rule_confidence": metrics["confidence"],
        "agent_label": None, "final_label": metrics["rule_label"], "decision_source": "rule",
        "reasons": [], "confidence": metrics["confidence"], "agree": None,
        "run_id": None, "decided_at": None, "model_id": None, "prompt_version": None, "evidence_count": 0,
        "metric_version": metrics["metric_version"],
        "sample_run": run,
        "sample_lifespan": None if manual else radar_read.sample_lifespan(metrics, decision),
        "editorial": None,
        "note": TIMELINE_NOTE,
    }
    if decision and decision.get("decision_source") in ("agent", "rule") and not manual:
        out.update(agent_label=decision.get("agent_label"),
                   final_label=decision.get("final_label") or out["final_label"], decision_source=decision["decision_source"], reasons=decision["reasons"],
                   confidence=decision.get("confidence") or out["confidence"], agree=decision.get("agree"),
                   run_id=decision.get("run_id"), decided_at=decision.get("created_at"),
                   model_id=decision.get("model_id"), prompt_version=decision.get("prompt_version"),
                   evidence_count=len(decision["evidence_ids"]))
    if manual:
        out.update(final_label=manual["final_label"], decision_source="manual", confidence=manual["confidence"],
                   reasons=[manual["source_text"]],
                   editorial={"display": manual["display"], "source_text": manual["source_text"]},
                   run_id=(decision or {}).get("run_id"), decided_at=(decision or {}).get("created_at"))
    return out
