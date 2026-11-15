"""Turn Radar micro_trend decisions into trend documents (origin "agent"). Safe to run any number of times.

    .venv/bin/python scripts/radar_to_trends.py --run-id ID            # write
    .venv/bin/python scripts/radar_to_trends.py --run-id ID --dry-run  # print the table, write nothing

Rules:
- Only decisions in radar_decisions for the run with final_label "micro_trend" and decision_source "agent" or
  "rule". Manual labels never become trend documents (checked on decision_source and on manual_labels.json).
- The style term is mapped to the attributes vocabulary (category, features cut/color/fabric). A style with
  no category is skipped and recorded (the trend guard "no category means discard").
- Source: a Bluesky search URL for the term. Lifespan comes from micro_trend.measured_active_run (the same
  measurement as write_decision rail 3) and cites that URL as lifespan_source_url. No measurable run means
  no lifespan numbers, only words.
- Every document goes through backend.trends.TrendDoc validation. Upsert by name (case-insensitive). A name
  held by a non-agent trend, or a feature signature already held by another trend, is skipped, never
  overwritten.
"""
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from pydantic import ValidationError  # noqa: E402

from backend.settings import ATTRIBUTES  # noqa: E402
from backend.trends import NAME_COLLATION, TrendDoc, feature_signature, name_key  # noqa: E402

TREND_SOURCES = ("agent", "rule")
SKIPS_DIR = ROOT / ".social_runs" / "radar_runs"

# Terms the shared category keywords (item_understanding.CATEGORY_KEYWORDS) do not resolve. Judgment calls,
# listed here so they are visible: brand or slang names for a garment type.
CATEGORY_OVERRIDES = {
    "sambas": "shoes",
    "levi's 501": "jeans",
    "quarter zip": "sweater",
    "leg warmers": "accessory",
    "flats": "shoes",
    "mesh flats": "shoes",
}
# Words in a term that name a listed cut. "flare" and "bootcut" have no listed cut, so they map to none.
CUT_WORDS = {
    "barrel": "wide", "wide": "wide", "balloon": "wide", "baggy": "relaxed", "relaxed": "relaxed",
    "straight": "straight", "skinny": "slim", "slim": "slim", "oversized": "oversized",
    "cropped": "cropped", "capri": "cropped", "fitted": "fitted",
}
COLOR_WORDS = {c: c for c in ATTRIBUTES["color_family"] if c != "multi"} | {"grey": "gray", "leopard": "multi"}
FABRIC_WORDS = {"denim": "denim", "leather": "leather", "linen": "linen", "wool": "wool", "cotton": "cotton",
                "poplin": "cotton", "nylon": "nylon"}
CATEGORY_FABRIC = {"jeans": "denim"}


def _words(term: str) -> list[str]:
    return re.sub(r"[^a-z0-9']+", " ", term.lower()).split()


def map_style(term: str) -> tuple[str | None, dict]:
    """Category and features for a style term, in the attributes.json vocabulary. Category None = unmappable."""
    from backend.services.item_understanding import _head_category

    key = " ".join(_words(term))
    category = CATEGORY_OVERRIDES.get(key) or _head_category(term)
    if category not in ATTRIBUTES["category"]:
        category = None
    words = _words(term)
    cut = next((CUT_WORDS[w] for w in words if w in CUT_WORDS), None)
    color = next((COLOR_WORDS[w] for w in words if w in COLOR_WORDS), None)
    fabric = next((FABRIC_WORDS[w] for w in words if w in FABRIC_WORDS), None) or CATEGORY_FABRIC.get(category)
    return category, {"cut": cut, "color": color, "fabric": fabric}


def bluesky_search_url(term: str) -> str:
    return f"https://bsky.app/search?q={quote(term)}"


def _week_dt(week: str | None):
    if not week:
        return None
    return datetime.strptime(week, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def build_trend(decision: dict, active_run: dict | None) -> TrendDoc:
    """Raises ValueError (unmappable) or ValidationError (TrendDoc rules)."""
    style = decision["style"]
    category, features = map_style(style)
    if category is None:
        raise ValueError("unmappable: no category in attributes.json for this term")
    url = bluesky_search_url(style)
    if active_run:
        weeks = active_run["weeks_active"]
        label = (f"About {weeks} weeks of active Bluesky chatter ({active_run['start_week']} to "
                 f"{active_run['end_week']}), measured on a 12-month sample. Sampled counts, not platform totals")
    else:
        weeks = None
        label = "No fully observed active run in the 12-month Bluesky sample, so the lifespan is not measured"
    return TrendDoc(
        name=style, category=category, features=features,
        first_seen=_week_dt(active_run["start_week"]) if active_run else None,
        lifespan_weeks_est=weeks, lifespan_label=label,
        lifespan_source_url=url if weeks is not None else None,
        sources=[{"title": f"Bluesky search: {style}", "url": url}],
        origin="agent")


def candidate_decisions(decisions, run_id: str) -> list[dict]:
    return list(decisions.find({"run_id": run_id, "final_label": "micro_trend"}, {"_id": 0}).sort("style", 1))


def write_trends(*, decisions, trends, run_id: str, active_run_fn, is_manual_fn, dry_run: bool = False) -> list[dict]:
    """One row per candidate decision: {style, decision_source, action, detail, category, lifespan_weeks}."""
    rows = []
    existing = list(trends.find({}, {"name": 1, "category": 1, "features": 1, "origin": 1}))
    for dec in candidate_decisions(decisions, run_id):
        style = dec["style"]
        row = {"style": style, "decision_source": dec.get("decision_source"), "category": None,
               "lifespan_weeks": None, "action": "skipped", "detail": ""}
        rows.append(row)
        if dec.get("decision_source") == "manual" or is_manual_fn(style):
            row["detail"] = "manual label: never a trend document"
            continue
        if dec.get("decision_source") not in TREND_SOURCES:
            row["detail"] = f"decision_source {dec.get('decision_source')!r} not in {TREND_SOURCES}"
            continue
        try:
            doc = build_trend(dec, active_run_fn(style))
        except ValidationError as exc:
            row["detail"] = f"TrendDoc validation failed: {exc.errors()[0]['msg']}"
            continue
        except ValueError as exc:
            row["detail"] = str(exc)
            continue
        row["category"], row["lifespan_weeks"] = doc.category, doc.lifespan_weeks_est
        same_name = next((e for e in existing if name_key(e["name"]) == name_key(doc.name)), None)
        if same_name and same_name.get("origin") != "agent":
            row["detail"] = f"name held by a {same_name.get('origin')} trend, not overwritten"
            continue
        sig = feature_signature(doc.category, doc.features)
        clash = next((e for e in existing if name_key(e["name"]) != name_key(doc.name)
                      and feature_signature(e.get("category"), e.get("features") or {}) == sig), None)
        if clash:
            row["detail"] = f"same category and features as trend '{clash['name']}'"
            continue
        fields = doc.model_dump(exclude={"search_suggestions_html"})
        fields.update(radar_style=style, radar_run_id=run_id, radar_decision_source=dec["decision_source"])
        if dry_run:
            row["action"] = "would write"
            continue
        res = trends.update_one({"name": doc.name}, {"$set": fields}, upsert=True, collation=NAME_COLLATION)
        row["action"] = "inserted" if res.upserted_id else ("updated" if res.modified_count else "unchanged")
        if res.upserted_id:
            existing.append({"name": doc.name, "category": doc.category, "features": fields["features"],
                             "origin": "agent"})
    return rows


def record_skips(run_id: str, rows: list[dict]) -> Path | None:
    skips = [r for r in rows if r["action"] == "skipped"]
    if not skips:
        return None
    path = SKIPS_DIR / run_id / "trend_skips.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        for r in skips:
            fh.write(json.dumps({**r, "at": datetime.now(timezone.utc).isoformat()}) + "\n")
    return path


def print_table(rows: list[dict]) -> None:
    head = f"{'style':<22} {'source':<7} {'category':<9} {'weeks':>5}  {'action':<12} detail"
    print(head)
    print("-" * len(head))
    for r in rows:
        weeks = f"{r['lifespan_weeks']:g}" if r["lifespan_weeks"] is not None else "-"
        print(f"{r['style']:<22} {str(r['decision_source']):<7} {str(r['category'] or '-'):<9} {weeks:>5}  "
              f"{r['action']:<12} {r['detail']}")
    if not rows:
        print("(no decision with final_label micro_trend in this run)")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--run-id", required=True, help="radar_decisions run_id to read")
    p.add_argument("--dry-run", action="store_true", help="print the table, write nothing")
    args = p.parse_args(argv)

    from backend.db import db, ensure_indexes
    from backend.services import micro_trend as mt

    if not args.dry_run:
        ensure_indexes()
    rows = write_trends(decisions=db["radar_decisions"], trends=db["trends"], run_id=args.run_id,
                        active_run_fn=mt.measured_active_run, is_manual_fn=mt.is_manual, dry_run=args.dry_run)
    print_table(rows)
    if not args.dry_run and (path := record_skips(args.run_id, rows)):
        print(f"skips recorded in {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
