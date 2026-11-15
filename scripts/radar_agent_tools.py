"""Evidence tools for the Social Trend Radar agent (scripts/radar_agent.py).

Each tool is a plain Python function with typed arguments and a docstring, listed in TOOLS. The agent may wrap
them with LangChain tools or pass them to deepagents directly. Every tool:

- takes the style (the social_trends term) as its first argument,
- counts against a per-style budget of MAX_TOOL_CALLS_PER_STYLE (12) calls per run, refused past the budget,
- returns a JSON-safe dict with "ok" and "evidence_id". A refused or failed call returns ok=False and "error"
  (it does not raise, so one bad call does not crash the agent loop).

Evidence ids: "ev:<tool>:<style-slug>:<call number>:<sha256 of the payload, 10 hex>". They are issued per run
and per style. write_decision accepts only ids issued in the same run for the same style. Every evidence record
is also appended to .social_runs/radar_runs/<run_id>/evidence.jsonl (git-ignored) so a decision can be audited.

Rails in write_decision (data limits, not judgment):
1. A label other than "unknown" is refused when the style has under min_posts (100) posts or under
   min_weeks_with_posts (8) weeks with posts. Weeks with posts are counted inside metric weeks
   (backend.services.micro_trend.weeks_with_posts), never from the stored observed_weeks field.
2. A decision with no evidence ids, or with ids not issued in this run for this style, is refused.
3. lifespan_weeks_est and lifespan_bucket come only from backend.services.micro_trend.measured_active_run,
   computed here, never typed by the agent, and only when lifespan_source_url is one of the matching trend's
   sources. No measurable run means no lifespan.
4. Manual styles (config/manual_labels.json) refuse agent decisions. write_manual_decisions stores their
   editorial labels with no lifespan fields.

Rule outputs are returned as evidence, not as an instruction. Evaluation labels and press_status
(config/social_terms.json) are never returned by any tool.

Usage from the agent script:
    import radar_agent_tools as rt
    rt.start_run(run_id)          # optional db=<pymongo Database>; default is backend.db.db
    tools = rt.TOOLS
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import threading
import uuid
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services import micro_trend

log = logging.getLogger(__name__)

RUNS_DIR = ROOT / ".social_runs" / "radar_runs"
EVAL_MATERIALS_DOC = ROOT / "docs" / "eval_materials.md"
MAX_TOOL_CALLS_PER_STYLE = 12
MAX_SAMPLE_POSTS = 20
MAX_WEEKS = 60
MAX_ITEMS = 10
MAX_REASONS = 8
MAX_TEXT = 500
MAX_IMAGE_BYTES = 8 * 1024 * 1024
CONFIDENCES = ("low", "medium", "high")
LABELS = micro_trend.LABELS
DECISIONS = "radar_decisions"
EVENTS = "radar_events"


# ---------------------------------------------------------------- run context

class RunContext:
    def __init__(self, run_id: str, db, out_dir: Path, max_calls: int):
        self.run_id = run_id
        self._db = db
        self.out_dir = Path(out_dir)
        self.max_calls = max_calls
        self.calls: Counter = Counter()
        self.evidence: dict[str, set] = defaultdict(set)
        self.lock = threading.Lock()

    @property
    def db(self):
        if self._db is None:
            from backend.db import db  # lazy: needs MONGODB_URI
            self._db = db
        return self._db


_CTX: RunContext | None = None


def start_run(run_id: str | None = None, db=None, out_dir: Path | None = None,
              max_calls_per_style: int = MAX_TOOL_CALLS_PER_STYLE) -> RunContext:
    """Begin a run: fresh call counters and evidence registry. db is a pymongo Database (injectable)."""
    global _CTX
    run_id = run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    _CTX = RunContext(run_id, db, Path(out_dir or RUNS_DIR / run_id), max_calls_per_style)
    return _CTX


def current_run() -> RunContext:
    return _CTX if _CTX is not None else start_run()


def calls_used(style: str) -> int:
    return current_run().calls[_norm(style)]


# ---------------------------------------------------------------- helpers

def _norm(term) -> str:
    return " ".join(str(term or "").casefold().split())


def _slug(term) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _norm(term)).strip("-") or "style"


def _clean(value):
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items() if k != "_id"}
    if isinstance(value, (list, tuple, set)):
        return [_clean(v) for v in value]
    if isinstance(value, datetime):
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if value.tzinfo else value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return round(value, 6)
    if value is None or isinstance(value, (str, int, bool)):
        return value
    return str(value)


def _append_jsonl(name: str, record: dict) -> None:
    ctx = current_run()
    try:
        ctx.out_dir.mkdir(parents=True, exist_ok=True)
        with (ctx.out_dir / name).open("a") as fh:
            fh.write(json.dumps(_clean(record)) + "\n")
    except OSError as exc:
        log.warning("could not append %s: %s", name, exc)


def _refuse(error: str, **extra) -> dict:
    return {"ok": False, "error": error, "evidence_id": None, **extra}


def _begin(tool: str, style: str) -> str | None:
    """Count one call. Returns a refusal message past the per-style budget."""
    ctx = current_run()
    key = _norm(style)
    if not key:
        return "style is required"
    with ctx.lock:
        if ctx.calls[key] >= ctx.max_calls:
            return (f"tool budget reached: {ctx.max_calls} tool calls for style {style!r} in run {ctx.run_id}; "
                    "no more calls are allowed for this style")
        ctx.calls[key] += 1
    return None


def _issue(tool: str, style: str, payload: dict) -> dict:
    ctx = current_run()
    key = _norm(style)
    clean = _clean(payload)
    digest = hashlib.sha256(json.dumps(clean, sort_keys=True).encode()).hexdigest()[:10]
    with ctx.lock:
        n = ctx.calls[key]
        evidence_id = f"ev:{tool}:{_slug(style)}:{n:02d}:{digest}"
        ctx.evidence[key].add(evidence_id)
    _append_jsonl("evidence.jsonl", {"evidence_id": evidence_id, "run_id": ctx.run_id, "style": style,
                                     "tool": tool, "payload": clean,
                                     "at": datetime.now(UTC)})
    return {"ok": True, "evidence_id": evidence_id, "tool_calls_used": n,
            "tool_calls_left": ctx.max_calls - n, **clean}


def _trend_doc(style: str) -> dict | None:
    return current_run().db["social_trends"].find_one({"term": style}, {"_id": 0})


def _style_filter(style: str) -> dict:
    return {"$or": [{"term": style}, {"terms": style}]}


def _tokens(style: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", _norm(style))


def _matching_trends(style: str) -> list[dict]:
    """Seeded or agent trend documents whose name contains every word of the style."""
    toks = _tokens(style)
    if not toks:
        return []
    out = []
    for t in current_run().db["trends"].find({}, {"_id": 0, "example_images": 0, "alternatives": 0}):
        name_toks = set(re.findall(r"[a-z0-9']+", _norm(t.get("name"))))
        if all(tok in name_toks for tok in toks):
            out.append(t)
    return out


def _rules(style: str) -> dict:
    return micro_trend.compute_rule_metrics(style, collection=current_run().db["social_trends"])


def _active_run(style: str) -> dict | None:
    return micro_trend.measured_active_run(style, collection=current_run().db["social_trends"])


def lifespan_bucket(weeks: float | None) -> str | None:
    """CONTRACT section 6: short <= 16 weeks, medium <= 52, long above."""
    if weeks is None:
        return None
    return "short" if weeks <= 16 else "medium" if weeks <= 52 else "long"


# ---------------------------------------------------------------- materials accuracy (from the eval doc)

def materials_accuracy(path: Path = EVAL_MATERIALS_DOC) -> dict | None:
    """Measured photo-estimate accuracy, read from docs/eval_materials.md. None when the doc lacks it."""
    try:
        text = Path(path).read_text()
    except OSError:
        return None
    acc = re.search(r"Accuracy \(unknown counts as wrong\):\s*(\d+)/(\d+)\s*=\s*([\d.]+)%", text)
    base = re.search(r"Majority-class baseline[^:]*:\s*([\d.]+)%", text)
    if not acc:
        return None
    return {"correct": int(acc.group(1)), "n": int(acc.group(2)), "accuracy_pct": float(acc.group(3)),
            "baseline_pct": float(base.group(1)) if base else None,
            "test_set": "Shopify retailer" if "Shopify" in text else "retailer",
            "source": "docs/eval_materials.md"}


def materials_line(estimate: dict | None, accuracy: dict | None = None) -> str | None:
    """The single labeled line the photo estimate may produce, with the measured accuracy, or None."""
    from backend.services import materials_photo
    line = materials_photo.photo_line(estimate)
    if not line:
        return None
    accuracy = accuracy if accuracy is not None else materials_accuracy()
    if not accuracy:
        return line.replace("medium confidence", "low confidence")
    if accuracy["baseline_pct"] is None or accuracy["accuracy_pct"] <= accuracy["baseline_pct"]:
        line = line.replace("medium confidence", "low confidence")
    return (f"{line}, about {round(accuracy['accuracy_pct'])} percent accurate on a "
            f"{accuracy['n']}-item {accuracy['test_set']} test")


def _fetch_image(url: str) -> bytes:
    import requests
    if not re.match(r"^https?://", url or ""):
        raise ValueError("image_url must be http(s)")
    with requests.get(url, timeout=10, stream=True, headers={"User-Agent": "SecondLookRadar/1.0"}) as r:
        r.raise_for_status()
        if not r.headers.get("Content-Type", "").startswith("image/"):
            raise ValueError("not an image response")
        data = b""
        for chunk in r.iter_content(65536):
            data += chunk
            if len(data) > MAX_IMAGE_BYTES:
                raise ValueError("image larger than 8 MB")
        return data


# ---------------------------------------------------------------- tools

def get_style_stats(style: str) -> dict:
    """Summary counts for one style from the sampled Bluesky corpus: posts, authors, metric weeks, weeks with
    posts (inside metric weeks), first seen in the observation window, peak week, saturation and coverage
    notes, and whether the style has a manual editorial label. Counts are sampled search results, not totals."""
    if (err := _begin("get_style_stats", style)):
        return _refuse(err)
    doc = _trend_doc(style)
    if doc is None:
        return {**_issue("get_style_stats", style, {"style": style, "found": False}), "ok": False,
                "error": f"no social_trends document for {style!r}"}
    weeks = micro_trend.metric_weeks(doc)
    payload = {
        "style": style, "found": True, "post_count": micro_trend.post_count(doc), "authors": doc.get("authors"),
        "metric_weeks": len(weeks), "weeks_with_posts": micro_trend.weeks_with_posts(doc),
        "metric_window": [weeks[0]["week"], weeks[-1]["week"]] if weeks else None,
        "first_seen_in_window": doc.get("first_seen_in_window"), "peak_week": doc.get("peak_week"),
        "saturated_weeks": doc.get("saturated_weeks"), "saturated_week_share": doc.get("saturated_week_share"),
        "counts_are_lower_bound": doc.get("counts_are_lower_bound"),
        "stored_confidence": doc.get("confidence"), "stored_momentum": doc.get("momentum"),
        "coverage_note": (doc.get("coverage_note") or "")[:MAX_TEXT],
        "manual_label": micro_trend.is_manual(style),
        "limits": {"min_posts": micro_trend.rule_config()["min_posts"],
                   "min_weeks_with_posts": micro_trend.rule_config()["min_weeks_with_posts"]},
        "note": "Sampled public Bluesky search counts. first_seen_in_window is not an origin date.",
    }
    return _issue("get_style_stats", style, payload)


def get_weekly_series(style: str, last_n_weeks: int = 53) -> dict:
    """Weekly series for one style: count, corpus_count, share (count / corpus_count), S_w (share smoothed with
    the rule window, centered, computed over metric weeks only), and markers: partial_edge (outside metric
    weeks), saturated (first page was full, count is a lower bound), peak and active-run start/end from the
    rules. last_n_weeks keeps the most recent weeks (at most 60)."""
    if (err := _begin("get_weekly_series", style)):
        return _refuse(err)
    doc = _trend_doc(style)
    if doc is None:
        return _refuse(f"no social_trends document for {style!r}")
    cfg = micro_trend.rule_config()
    weekly = list(doc.get("weekly_counts") or [])
    metric = micro_trend.metric_weeks(doc)
    metric_keys = [w["week"] for w in metric]
    shares = [(w.get("count") or 0) / w["corpus_count"] if w.get("corpus_count") else 0.0 for w in metric]
    smooth = dict(zip(metric_keys, micro_trend._smooth(shares, int(cfg["smoothing_window_weeks"]))))
    try:
        rules = _rules(style)
    except LookupError:
        rules = {}
    run = rules.get("active_run") or {}
    rows = []
    for w in weekly:
        wk = w.get("week")
        markers = []
        if wk not in smooth:
            markers.append("partial_edge")
        if w.get("saturated"):
            markers.append("saturated")
        if wk == rules.get("peak_week"):
            markers.append("peak")
        if wk == run.get("start_week"):
            markers.append("active_start")
        if wk == run.get("end_week"):
            markers.append("active_end")
        rows.append({"week": wk, "count": w.get("count"), "corpus_count": w.get("corpus_count"),
                     "share": (w.get("count") or 0) / w["corpus_count"] if w.get("corpus_count") else 0.0,
                     "S_w": smooth.get(wk), "markers": markers})
    n = max(1, min(int(last_n_weeks or MAX_WEEKS), MAX_WEEKS))
    payload = {"style": style, "weeks": rows[-n:], "weeks_total": len(rows),
               "smoothing_window_weeks": cfg["smoothing_window_weeks"],
               "saturated_weeks": sum(1 for w in weekly if w.get("saturated")),
               "note": "share is count / corpus_count per UTC Monday week. Saturated counts are lower bounds."}
    return _issue("get_weekly_series", style, payload)


def get_connectivity(style: str) -> dict:
    """How the style spreads across people: breadth (communities with at least 3 posts), concentration
    (largest community post share), weeks to breadth 3, interaction share (posts that reply to, quote or
    mention someone), the number of communities its authors belong to, and the top 5 communities by posts."""
    if (err := _begin("get_connectivity", style)):
        return _refuse(err)
    db = current_run().db
    doc = _trend_doc(style) or {}
    flt = _style_filter(style)
    posts = db["social_posts"]
    total = posts.count_documents(flt)
    interacting = posts.count_documents({"$and": [flt, {"$or": [
        {"reply_parent_uri": {"$ne": None}}, {"quote_uri": {"$ne": None}},
        {"mention_author_hashes.0": {"$exists": True}}]}]})
    per_author = Counter()
    for p in posts.find(flt, {"_id": 0, "author_hash": 1}):
        if p.get("author_hash"):
            per_author[p["author_hash"]] += 1
    per_comm, names = Counter(), {}
    if per_author:
        for c in db["social_communities"].find({"author_hash": {"$in": list(per_author)}},
                                               {"_id": 0, "author_hash": 1, "community_id": 1, "name": 1}):
            per_comm[c["community_id"]] += per_author[c["author_hash"]]
            names[c["community_id"]] = c.get("name")
    payload = {
        "style": style, "stored_breadth": doc.get("breadth"), "stored_concentration": doc.get("concentration"),
        "weeks_to_breadth_3": doc.get("weeks_to_breadth_3"), "authors": len(per_author), "posts": total,
        "interaction_posts": interacting, "interaction_share": interacting / total if total else None,
        "community_count": len(per_comm), "communities_with_3plus_posts": sum(1 for v in per_comm.values() if v >= 3),
        "top_communities": [{"community_id": cid, "name": names.get(cid), "posts": v,
                             "share": v / total if total else None} for cid, v in per_comm.most_common(5)],
        "note": ("Communities are Louvain clusters of the interaction graph; names are term-based labels. posts "
                 "includes thread-enrichment posts, so it can exceed post_count from get_style_stats."),
    }
    return _issue("get_connectivity", style, payload)


def sample_posts(style: str, n: int = 8, week: str | None = None) -> dict:
    """A deterministic sample of up to n posts (at most 20) for the style, to judge whether the posts really
    are about the clothing style (precision) and in what context. Optional week (UTC Monday, YYYY-MM-DD)
    samples from that week only. Snippets are sanitized (no account names, at most 160 characters)."""
    if (err := _begin("sample_posts", style)):
        return _refuse(err)
    from backend.services.social import post_url, sanitize_snippet
    db = current_run().db
    flt = _style_filter(style)
    if week:
        try:
            start = datetime.strptime(week, "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError:
            return _refuse("week must be YYYY-MM-DD")
        from datetime import timedelta
        end = start + timedelta(days=7)
        flt = {"$and": [flt, {"created_at": {"$gte": start.strftime("%Y-%m-%dT%H:%M:%S.000000Z"),
                                             "$lt": end.strftime("%Y-%m-%dT%H:%M:%S.000000Z")}}]}
    k = max(1, min(int(n or 8), MAX_SAMPLE_POSTS))
    uris = [p["uri"] for p in db["social_posts"].find(flt, {"_id": 0, "uri": 1}).limit(20000)]
    seed = f"{current_run().run_id}:{_norm(style)}:{week or ''}"
    picked = sorted(uris, key=lambda u: hashlib.sha256(f"{seed}:{u}".encode()).hexdigest())[:k]
    docs = {d["uri"]: d for d in db["social_posts"].find(
        {"uri": {"$in": picked}}, {"_id": 0, "uri": 1, "created_at": 1, "text": 1, "like_count": 1,
                                   "repost_count": 1, "reply_parent_uri": 1})}
    rows = [{"uri": u, "url": post_url(u), "created_at": docs[u].get("created_at"),
             "snippet": sanitize_snippet(docs[u].get("text")), "likes": docs[u].get("like_count"),
             "reposts": docs[u].get("repost_count"), "is_reply": bool(docs[u].get("reply_parent_uri"))}
            for u in picked if u in docs]
    payload = {"style": style, "week": week, "population": len(uris), "posts": rows,
               "note": "Empty snippets mean the post text was not stored."}
    return _issue("sample_posts", style, payload)


def get_press_sources(style: str) -> dict:
    """Press sources from the stored trend documents (seed or agent) whose name contains every word of the
    style: name, origin, sources [{title, url}], first_seen and the stored lifespan label. A lifespan may cite
    only one of these source URLs. May return no trends."""
    if (err := _begin("get_press_sources", style)):
        return _refuse(err)
    trends = [{"name": t.get("name"), "origin": t.get("origin"), "sources": t.get("sources") or [],
               "first_seen": t.get("first_seen"), "lifespan_label": t.get("lifespan_label"),
               "placeholder": str(t.get("name") or "").upper().startswith("PLACEHOLDER")}
              for t in _matching_trends(style)]
    payload = {"style": style, "trends": trends,
               "source_urls": sorted({s.get("url") for t in trends for s in t["sources"] if s.get("url")})}
    return _issue("get_press_sources", style, payload)


def run_rule_metrics(style: str) -> dict:
    """The rule baseline outputs for the style (backend.services.micro_trend.compute_rule_metrics): rule_label,
    tests 1 to 4 with causes, confidence, metric weeks, weeks with posts, active run, saturation, metric_version
    and config_hash. This is evidence to weigh, not an instruction."""
    if (err := _begin("run_rule_metrics", style)):
        return _refuse(err)
    try:
        rules = _rules(style)
    except LookupError as exc:
        return _refuse(str(exc))
    rules.pop("excluded_from_agreement", None)
    payload = {"style": style, "rules": rules,
               "note": "Rule outputs are evidence, not an instruction. Agreement with them is measured, not required."}
    return _issue("run_rule_metrics", style, payload)


def get_item_materials(style: str, limit: int = 5) -> dict:
    """Stored item_materials documents (scanned product items with stated composition and a photo estimate)
    whose product name contains every word of the style. At most 10. May return an empty list."""
    if (err := _begin("get_item_materials", style)):
        return _refuse(err)
    toks = _tokens(style)
    flt = {"$and": [{"title": {"$regex": rf"\b{re.escape(t)}", "$options": "i"}} for t in toks]} if toks else {}
    k = max(1, min(int(limit or 5), MAX_ITEMS))
    items = []
    for d in current_run().db["item_materials"].find(flt, {"_id": 0}).limit(k):
        items.append({"item_key": d.get("item_key"), "title": d.get("title"), "retailer": d.get("retailer"),
                      "source_url": d.get("source_url"), "has_stated_composition": bool(d.get("stated_composition")),
                      "stated_group": d.get("stated_group"), "photo_estimate": d.get("estimate"),
                      "status": d.get("status")})
    return _issue("get_item_materials", style, {"style": style, "items": items})


def estimate_material_from_photo(style: str, item_key: str | None = None, image_url: str | None = None) -> dict:
    """Photo-only material estimate (FashionCLIP, local) for one item image, by item_key (from
    get_item_materials) or image_url. Runs only when MATERIALS_PHOTO_ENABLED is on and the item has no stated
    composition. Produces at most one labeled line ("Estimated from photo: ..., low confidence, about N percent
    accurate on a ...-item test"). It never changes a label, a lifespan or a verdict."""
    if (err := _begin("estimate_material_from_photo", style)):
        return _refuse(err)
    from backend.services import item_materials_store as ims
    from backend.services import materials_photo as mp
    accuracy = materials_accuracy()
    base = {"style": style, "item_key": item_key, "line": None, "estimate": None, "accuracy": accuracy,
            "affects_label": False, "note": "Never changes a label, a lifespan or a verdict."}
    if not mp.enabled():
        return _issue("estimate_material_from_photo", style, {**base, "status": "disabled"})
    item = {"materials": "", "description": None}
    doc = None
    if item_key:
        doc = current_run().db["item_materials"].find_one({"item_key": item_key}, {"_id": 0})
        if doc is None:
            return _refuse(f"no item_materials document with item_key {item_key!r}")
        item["materials"] = doc.get("stated_composition") or ""
        image_url = image_url or doc.get("image_url")
    if mp.has_stated_composition(item):
        return _issue("estimate_material_from_photo", style, {**base, "status": "skipped_stated_composition"})
    if not image_url:
        return _refuse("no image_url for this item")
    try:
        image = _fetch_image(image_url)
    except Exception as exc:  # noqa: BLE001
        return _issue("estimate_material_from_photo", style,
                      {**base, "status": "image_fetch_failed", "error": f"{type(exc).__name__}"})
    est = mp.estimate_for_item(item, image) or {}
    line = materials_line(est, accuracy)
    if os.getenv("MATERIALS_STORE_USER_ITEMS", "0").lower() in ("1", "true", "yes"):
        key = item_key or ims.item_key(url=image_url)
        status = "ok" if est.get("status") == "ok" else "model_unavailable"
        ims.ItemMaterialsStore(collection=current_run().db[ims.COLLECTION]).upsert(ims.build_doc(
            key=key, source="radar_agent", source_url=(doc or {}).get("source_url"), stated_composition=None,
            stated_group=None, estimate=est, model_id=mp.MODEL_ID, labels_hash=mp.labels_config_hash(),
            thresholds_hash=mp.thresholds_hash(), run_id=current_run().run_id, status=status,
            composition_hidden_for_eval=False, extra={"image_url": image_url}))
    payload = {**base, "status": est.get("status"), "line": line,
               "estimate": {k: est.get(k) for k in ("group", "confidence", "score", "margin")}}
    return _issue("estimate_material_from_photo", style, payload)


def record_event(style: str, event: str, detail: str = "") -> dict:
    """Record a short note about the run for this style (for example a data problem noticed in sampled posts).
    Stored in radar_events and the run's events.jsonl. Returns an evidence id."""
    if (err := _begin("record_event", style)):
        return _refuse(err)
    ctx = current_run()
    rec = {"run_id": ctx.run_id, "style": style, "event": str(event)[:100], "detail": str(detail)[:1000],
           "at": datetime.now(UTC)}
    stored = "db"
    try:
        ctx.db[EVENTS].insert_one(dict(rec))
    except Exception as exc:  # noqa: BLE001
        log.warning("radar_events write failed: %s", exc)
        stored = "jsonl_only"
    _append_jsonl("events.jsonl", rec)
    return _issue("record_event", style, {**{k: v for k, v in rec.items() if k != "at"}, "stored": stored})


def _persist_decision(doc: dict) -> str:
    ctx = current_run()
    stored = "db"
    try:
        coll = ctx.db[DECISIONS]
        coll.create_index([("style", 1), ("run_id", 1)], unique=True, name="style_run_unique")
        coll.replace_one({"style": doc["style"], "run_id": doc["run_id"]}, dict(doc), upsert=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("radar_decisions write failed, kept in jsonl: %s", exc)
        stored = "jsonl_fallback"
    _append_jsonl("decisions.jsonl", {**doc, "stored": stored})
    return stored


def write_decision(style: str, agent_label: str, confidence: str, reasons: list[str], evidence_ids: list[str],
                   lifespan_source_url: str | None = None, model_id: str | None = None,
                   prompt_version: str | None = None) -> dict:
    """Store the agent's decision for one style. agent_label is one of micro_trend, established, still_active,
    unknown. confidence is low, medium or high. reasons are short sentences. evidence_ids must be ids returned
    by tools in this run for this style. Optional lifespan_source_url must be one of the style's press source
    URLs (get_press_sources); the lifespan weeks are then measured by code, never typed. Refused for manual
    styles, without evidence, or with a non-unknown label when the data is too thin."""
    if (err := _begin("write_decision", style)):
        return _refuse(err)
    ctx = current_run()
    if micro_trend.is_manual(style):
        return _refuse(f"{style!r} has a manual editorial label; agent decisions are refused for manual styles")
    if agent_label not in LABELS:
        return _refuse(f"agent_label must be one of {list(LABELS)}")
    if confidence not in CONFIDENCES:
        return _refuse(f"confidence must be one of {list(CONFIDENCES)}")
    ids = [str(i) for i in (evidence_ids or [])]
    if not ids:
        return _refuse("a decision must cite at least one evidence_id")
    with ctx.lock:
        issued = set(ctx.evidence[_norm(style)])
    unknown_ids = [i for i in ids if i not in issued]
    if unknown_ids:
        return _refuse("evidence_ids not issued in this run for this style", unknown_ids=unknown_ids)
    try:
        rules = _rules(style)
    except LookupError as exc:
        return _refuse(str(exc))
    cfg = micro_trend.rule_config()
    if agent_label != "unknown" and (rules["posts"] < cfg["min_posts"]
                                     or rules["weeks_with_posts"] < cfg["min_weeks_with_posts"]):
        return _refuse(f"data too thin for a label other than unknown: {rules['posts']} posts "
                       f"(minimum {cfg['min_posts']}), {rules['weeks_with_posts']} weeks with posts "
                       f"(minimum {cfg['min_weeks_with_posts']})")
    lifespan = {"lifespan_weeks_est": None, "lifespan_bucket": None, "lifespan_source_url": None,
                "active_run": None, "lifespan_note": None}
    if lifespan_source_url:
        sources = {s.get("url") for t in _matching_trends(style) for s in (t.get("sources") or [])}
        if lifespan_source_url not in sources:
            return _refuse("lifespan_source_url is not one of this style's trend sources",
                           allowed_sources=sorted(u for u in sources if u))
        run = _active_run(style)
        if run is None:
            lifespan["lifespan_note"] = "no measurable active run; no lifespan set"
        else:
            lifespan.update(lifespan_weeks_est=run["weeks_active"], lifespan_bucket=lifespan_bucket(run["weeks_active"]),
                            lifespan_source_url=lifespan_source_url, active_run=run)
    doc = {
        "style": style, "run_id": ctx.run_id, "rule_label": rules["rule_label"], "agent_label": agent_label,
        "final_label": agent_label, "decision_source": "agent", "agree": agent_label == rules["rule_label"],
        "confidence": confidence,
        "reasons": [str(r)[:MAX_TEXT] for r in (reasons or [])][:MAX_REASONS],
        "evidence_ids": ids, **lifespan,
        "metric_version": rules.get("metric_version"), "config_hash": rules.get("config_hash"),
        "prompt_version": prompt_version,
        "model_id": model_id or os.getenv("RADAR_AGENT_MODEL") or os.getenv("CLAUDE_MODEL"),
        "excluded_from_agreement": False, "created_at": datetime.now(UTC),
    }
    stored = _persist_decision(doc)
    out = _issue("write_decision", style, {k: v for k, v in doc.items() if k != "created_at"})
    out["stored"] = stored
    return out


def write_manual_decisions(styles: list[str] | None = None) -> list[dict]:
    """Not an agent tool. Store the editorial label for each manual style in this run, with decision_source
    "manual", no lifespan fields and excluded_from_agreement true."""
    ctx = current_run()
    out = []
    for entry in micro_trend.load_manual_labels().values():
        style = entry["term"]
        if styles is not None and _norm(style) not in {_norm(s) for s in styles}:
            continue
        manual = micro_trend.manual_decision(style)
        try:
            rule_label = _rules(style)["rule_label"]
            metric_version = micro_trend.rule_config()["metric_version"]
        except LookupError:
            rule_label, metric_version = None, micro_trend.rule_config()["metric_version"]
        doc = {"style": style, "run_id": ctx.run_id, "rule_label": rule_label, "agent_label": None,
               "final_label": manual["final_label"], "decision_source": "manual", "agree": None,
               "confidence": manual["confidence"], "reasons": [manual["source_text"]], "evidence_ids": [],
               "lifespan_weeks_est": None, "lifespan_bucket": None, "lifespan_source_url": None,
               "active_run": None, "display": manual["display"], "metric_version": metric_version,
               "config_hash": micro_trend.config_hash(), "prompt_version": None, "model_id": None,
               "excluded_from_agreement": True, "created_at": datetime.now(UTC)}
        doc["stored"] = _persist_decision(doc)
        out.append(_clean(doc))
    return out


TOOLS = [get_style_stats, get_weekly_series, get_connectivity, sample_posts, get_press_sources, run_rule_metrics,
         get_item_materials, estimate_material_from_photo, write_decision, record_event]
TOOL_NAMES = [t.__name__ for t in TOOLS]
