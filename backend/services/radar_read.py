"""Read side of the Social Radar: the latest decision per style and the Bluesky sample run numbers.

Used by GET /social/trends/{id}/timeline (backend/routers/social.py) and by the verdict prompt
(backend/services/verdict.py). Decisions are written by scripts/radar_agent_tools.py and scripts/radar_agent.py
into the Mongo collection radar_decisions, with a JSONL copy (or fallback) under .social_runs/radar_runs/.
This module never writes.

Sample numbers (weeks to rise, weeks active) come from backend/services/micro_trend.py. They describe a
sampled Bluesky corpus, never the whole market, and are never given for manual (editorial) styles.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from pymongo import timeout as mongo_timeout

from backend.services import micro_trend

log = logging.getLogger("secondlook.radar")

ROOT = Path(__file__).resolve().parents[2]
RUNS_DIR = ROOT / ".social_runs" / "radar_runs"
DECISIONS = "radar_decisions"
SHORT_MAX_WEEKS = 16  # same bound as backend/trends.py bucket_for_weeks "short"
DECISION_FIELDS = ("style", "run_id", "rule_label", "agent_label", "final_label", "decision_source", "agree",
                   "confidence", "reasons", "evidence_ids", "metric_version", "prompt_version", "model_id",
                   "display", "excluded_from_agreement", "created_at")


def _norm(term) -> str:
    return " ".join(str(term or "").casefold().split())


def _iso(value) -> str:
    """Sortable UTC ISO text for created_at (datetime from Mongo, or text from JSONL)."""
    if isinstance(value, datetime):
        value = value if value.tzinfo else value.replace(tzinfo=UTC)
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(value or "")


def _from_db(style: str, db) -> list[dict]:
    if db is None:
        return []
    try:
        with mongo_timeout(2):
            rows = list(db[DECISIONS].find({"style": style}, {"_id": 0}).sort("created_at", -1).limit(1))
    except Exception as exc:  # noqa: BLE001 - the JSONL copy is the fallback
        log.warning("radar_decisions read failed: %s", type(exc).__name__)
        return []
    return rows


def _from_jsonl(style: str, runs_dir: Path) -> list[dict]:
    key, rows = _norm(style), []
    for path in sorted(Path(runs_dir).glob("*/decisions.jsonl")):
        try:
            lines = path.read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and _norm(row.get("style")) == key:
                rows.append(row)
    return rows


def latest_decision(style: str, db=None, runs_dir: Path | None = None) -> dict | None:
    """The newest decision for a style across radar_decisions and the JSONL files, or None.

    Ties on created_at go to the database copy. The result keeps DECISION_FIELDS only, created_at as ISO text.
    """
    candidates = _from_db(style, db) + _from_jsonl(style, runs_dir or RUNS_DIR)
    if not candidates:
        return None
    best = max(enumerate(candidates), key=lambda pair: (_iso(pair[1].get("created_at")), -pair[0]))[1]
    out = {k: best.get(k) for k in DECISION_FIELDS}
    out["created_at"] = _iso(best.get("created_at")) or None
    out["reasons"] = [str(r) for r in (best.get("reasons") or [])]
    out["evidence_ids"] = [str(r) for r in (best.get("evidence_ids") or [])]
    return out


def sample_run(metrics: dict) -> dict | None:
    """Measured Bluesky sample run from compute_rule_metrics output, or None.

    None for manual styles, data-short or seasonal styles, a peak censored at the weekly cap, or a run that
    touches either window edge (same conditions as micro_trend.measured_active_run). weeks_to_rise is the
    test 1 ramp, or None when the rise was not observed.
    """
    run = metrics.get("active_run")
    blockers = {"posts_below_min", "weeks_with_posts_below_min", "seasonal_category", "no_posts"}
    if (metrics.get("manual") or blockers & set(metrics.get("reason_codes") or []) or metrics.get("peak_censored")
            or not run or run.get("touches_left") or run.get("touches_right")):
        return None
    ramp = (metrics.get("test1") or {}).get("ramp_weeks")
    return {"start_week": run["start_week"], "end_week": run["end_week"], "weeks_active": run["weeks_active"],
            "weeks_to_rise": ramp if isinstance(ramp, int) else None, "peak_week": metrics.get("peak_week"),
            "source": "Bluesky sample"}


def sample_lifespan(metrics: dict, decision: dict | None) -> dict | None:
    """Numbers the verdict may cite as Bluesky sample evidence, or None.

    Given only when the Radar's final label is micro_trend (the latest agent or rule decision, else the rule
    label), the style is not manual, and the measured sample run is short (16 weeks or less).
    """
    if metrics.get("manual") or (decision or {}).get("decision_source") == "manual":
        return None
    final = (decision or {}).get("final_label") or metrics.get("rule_label")
    run = sample_run(metrics)
    if final != "micro_trend" or run is None or run["weeks_active"] > SHORT_MAX_WEEKS:
        return None
    return {"term": metrics.get("term"), "final_label": final,
            "decision_source": (decision or {}).get("decision_source") or "rule",
            "weeks_to_rise": run["weeks_to_rise"], "weeks_active": run["weeks_active"], "source": "Bluesky sample"}


def sample_lifespan_for_term(term: str, db=None, runs_dir: Path | None = None) -> dict | None:
    """sample_lifespan for a social_trends term, read from db (default backend.db.db). Any failure: None."""
    if not term:
        return None
    try:
        if db is None:
            from backend.db import db as default_db
            db = default_db
        with mongo_timeout(0.5):
            doc = db["social_trends"].find_one({"term": term}, {"_id": 0, "embedding": 0})
        if not doc or micro_trend.is_manual(term):
            return None
        metrics = micro_trend.compute_rule_metrics(doc)
        return sample_lifespan(metrics, latest_decision(term, db, runs_dir))
    except Exception as exc:  # noqa: BLE001 - optional evidence, never breaks a verdict
        log.warning("radar sample lifespan unavailable: %s", type(exc).__name__)
        return None
