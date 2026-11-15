"""Optional Radar context next to a verdict (the `radar` field of POST /analyze).

The label comes only from the deepagent's stored decision in radar_decisions (decision_source "agent").
No rule label, no manual label, no embedding or LLM call at request time. The summary is a fixed template
over stored fields and describes Bluesky sample chatter only. This field never changes the verdict.

radar_for(social, status) returns the field as a dict, or None when it must be omitted (flag off, lookup
timeout or failure, Mongo timeout or error).
"""
from __future__ import annotations

import logging

from pymongo import timeout as mongo_timeout

from backend.services import micro_trend, radar_read

log = logging.getLogger("secondlook.radar_field")

LABELS = ("micro_trend", "established", "still_active", "unknown")
CONFIDENCES = ("low", "medium", "high")
READ_TIMEOUT_S = 0.4  # Mongo budget for both reads; the verdict's Claude call runs far longer
NO_MATCH_SUMMARY = "No matching style in our Bluesky sample."
NO_DECISION_SUMMARY = "No agent decision for this style."
SUMMARIES = {
    "micro_trend": "In our Bluesky sample, chatter about {style} rose and faded quickly.",
    "established": "In our Bluesky sample, chatter about {style} has stayed steady over a long period.",
    "still_active": "In our Bluesky sample, chatter about {style} is still active.",
    "unknown": "Our Bluesky sample is not clear enough to call a pattern for {style}.",
}


def evidence_note(post_count) -> str:
    if not isinstance(post_count, int) or isinstance(post_count, bool) or post_count < 0:
        return "Bluesky sample"
    return f"Bluesky sample, {post_count} post{'' if post_count == 1 else 's'}"


def _confidence(value) -> str:
    return value if value in CONFIDENCES else "low"


def no_match() -> dict:
    return {"matched_style": None, "similarity": None, "label": "unknown", "confidence": "low",
            "summary": NO_MATCH_SUMMARY, "weeks_to_rise": None, "weeks_active": None,
            "evidence_note": evidence_note(0), "timeline_url": None}


def _db():
    from backend.db import db
    return db


def agent_decision(style: str, db) -> dict | None:
    """Newest radar_decisions doc with decision_source "agent" for style, or None. Raises on Mongo errors."""
    rows = list(db[radar_read.DECISIONS].find({"style": style, "decision_source": "agent"}, {"_id": 0})
                .sort("created_at", -1).limit(1))
    return rows[0] if rows else None


def build(style: str, similarity, decision: dict | None, trend_doc: dict | None) -> dict:
    """The radar field for a matched style from stored documents. Pure, no I/O except rule metrics."""
    trend_doc = trend_doc or {}
    out = {"matched_style": style, "similarity": float(similarity) if similarity is not None else None,
           "weeks_to_rise": None, "weeks_active": None, "evidence_note": evidence_note(trend_doc.get("post_count")),
           "timeline_url": None}
    label = (decision or {}).get("agent_label")
    if not decision or label not in LABELS:
        return {**out, "label": "unknown", "confidence": "low", "summary": NO_DECISION_SUMMARY}
    out.update(label=label, confidence=_confidence(decision.get("confidence")),
               summary=SUMMARIES[label].format(style=style))
    if label == "micro_trend" and trend_doc:
        try:
            run = radar_read.sample_run(micro_trend.compute_rule_metrics(trend_doc))
        except Exception as exc:  # noqa: BLE001 - optional numbers, the label still stands
            log.warning("radar field sample run unavailable: %s", type(exc).__name__)
            run = None
        if run:
            out.update(weeks_to_rise=run.get("weeks_to_rise"), weeks_active=run.get("weeks_active"))
    return out


def radar_for(social: dict | None, status: dict | None, db=None) -> dict | None:
    """Radar field for one social_context result and its status, or None to omit the field."""
    state = (status or {}).get("state")
    if state == "no_match" and not social:
        return no_match()
    if state != "matched" or not social or not social.get("term"):
        return None
    style = social["term"]
    try:
        db = db if db is not None else _db()
        with mongo_timeout(READ_TIMEOUT_S):
            decision = None if micro_trend.is_manual(style) else agent_decision(style, db)
            trend_doc = db["social_trends"].find_one({"term": style}, {"_id": 0, "embedding": 0})
    except Exception as exc:  # noqa: BLE001 - timeout or Mongo error: omit the field
        log.warning("radar field omitted: %s", type(exc).__name__)
        return None
    return build(style, social.get("similarity"), decision, trend_doc)
