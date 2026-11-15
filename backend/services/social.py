"""Sampled Bluesky trend metrics and optional, bounded runtime context.

Counts describe the collected search index sample, never Bluesky platform totals.
Thresholds use only numeric metrics. Evaluation labels never enter classify_momentum.
"""
from __future__ import annotations

import copy
import json
import math
import os
import re
import threading
import time
from collections import Counter, OrderedDict, defaultdict
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from pymongo import timeout as mongo_timeout

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "social_momentum.json"
_HANDLE = re.compile(r"(?<!\w)@[\w.-]+|\b(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}\b", re.I)
_URL = re.compile(r"(?:https?://|at://)\S+", re.I)
_RUNTIME_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="social-context")
_RUNTIME_SLOTS = threading.BoundedSemaphore(4)
_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, dict | None]] = {}
_INFLIGHT: dict[str, object] = {}
MAX_CACHE_ITEMS = 512
MAX_VECTOR_CACHE_ITEMS = 2048
_VECTOR_CACHE: "OrderedDict[tuple, tuple]" = OrderedDict()


def momentum_config() -> dict:
    return json.loads(CONFIG_PATH.read_text())


def utc(value) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        raise ValueError("a UTC timestamp is required")
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def iso(value: datetime) -> str:
    # Fixed-width strings preserve chronological order in Atlas string range filters.
    return utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def monday(value: datetime) -> datetime:
    day = utc(value).replace(hour=0, minute=0, second=0, microsecond=0)
    return day - timedelta(days=day.weekday())


def terms_for(post: dict) -> list[str]:
    terms = [post.get("term"), *(post.get("terms") or [])]
    return list(dict.fromkeys(t.strip() for t in terms if isinstance(t, str) and t.strip()))


def sanitize_snippet(text: str | None, limit: int = 160) -> str:
    """Public snippets contain no account mentions and fit the response cap."""
    clean = _HANDLE.sub("[account]", _URL.sub("[link]", str(text or ""))).replace("\u2014", ", ")
    return " ".join(clean.split())[:limit]


def post_url(uri: str) -> str | None:
    """DID-based links avoid fetching or exposing an author's handle."""
    parts = str(uri).split("/")
    if len(parts) != 5 or parts[0] != "at:" or not parts[2].startswith("did:") or parts[3] != "app.bsky.feed.post":
        return None
    return f"https://bsky.app/profile/{quote(parts[2], safe=':')}/post/{quote(parts[4], safe='')}"


def classify_momentum(metrics: dict, config: dict | None = None) -> str:
    """Numeric rules only. Labels, term names and stored evaluation metadata are ignored."""
    cfg = config or momentum_config()
    if metrics.get("post_count", 0) < cfg["min_posts"] or \
            metrics.get("observed_weeks", 0) < cfg["minimum_observed_weeks"]:
        return "unknown"
    ratio = metrics.get("decay_ratio")
    if ratio is None or not math.isfinite(ratio):
        return "unknown"
    if ratio < cfg["fading_decay_ratio"]:
        return "fading"
    growth = metrics.get("recent_growth")
    if ratio >= cfg["rising_peak_ratio"] and growth is not None and \
            growth >= cfg["rising_recent_growth"]:
        return "rising"
    if ratio >= cfg["peaking_peak_ratio"] and \
            metrics.get("peak_weeks_ago", 10**6) <= cfg["peaking_max_weeks_ago"] and \
            growth is not None and growth < cfg["rising_recent_growth"]:
        return "peaking"
    return "steady"


def query_window_coverage(tasks, since, until) -> str:
    """Only exhausted no-cursor searches establish an observed index-count window."""
    intersecting = []
    for task in tasks:
        try:
            left, right = utc(task["since"]), utc(task["until"])
        except (KeyError, ValueError, TypeError):
            continue
        if left < until and right > since:
            intersecting.append((max(left, since), min(right, until), task))
    if not intersecting:
        return "unknown"
    if not any(t.get("pages", 0) > 0 for _, _, t in intersecting):
        return "unvisited"
    covered_until = since
    for left, right, task in sorted(intersecting, key=lambda row: row[0]):
        if left > covered_until:
            break
        exhausted = task.get("stop_reason") in ("no_cursor", "empty_keyset_page", "confirmed_empty", "confirmed_no_new") \
            and not task.get("equal_timestamp_boundary_risk") and not task.get("partial") \
            and not task.get("unresolved")
        if task.get("pages", 0) > 0 and task.get("done") and exhausted:
            covered_until = max(covered_until, right)
    return "complete" if covered_until >= until else "partial"


def query_window_visited(tasks, since, until) -> bool:
    """A successful first page proves a sampled window was queried, not exhausted."""
    intervals = []
    for task in tasks:
        try:
            left, right = utc(task["since"]), utc(task["until"])
        except (KeyError, ValueError, TypeError):
            continue
        if task.get("pages", 0) > 0 and left < until and right > since:
            intervals.append((max(left, since), min(right, until)))
    covered_until = since
    for left, right in sorted(intervals):
        if left > covered_until:
            break
        covered_until = max(covered_until, right)
    return covered_until >= until


def task_saturated(task) -> bool:
    return bool(task.get("saturated")) or task.get("first_page_count") == 20


def task_saturation_known(task) -> bool:
    return isinstance(task.get("first_page_count"), int) or isinstance(task.get("saturated"), bool)


def preview_recent_momentum(metrics, config=None) -> dict:
    """Read-only proposal: recent sample movement, never a published replacement rule.

    Require eight fully visited term/corpus windows and a nonempty corpus each week.
    A zero from an unexhausted term search is not treated as observed absence. Compare
    four-week normalized sample sums. Partial or saturated counts keep confidence low.
    Historical peak and decay remain unchanged, including nulls for incomplete history.
    """
    cfg = config or momentum_config()
    rows = []
    for metric in metrics:
        weekly = [w for w in metric.get("weekly_counts", []) if w.get("complete")][-8:]
        eligible = len(weekly) == 8 and all(w.get("query_visited") and w.get("corpus_query_visited")
                  and w.get("corpus_count", 0) > 0 and
                  (w.get("count", 0) > 0 or w.get("query_coverage") == "complete") for w in weekly)
        sample_posts = sum(w.get("count", 0) for w in weekly)
        eligible = eligible and sample_posts >= cfg["min_posts"]
        ratio, proposed, confidence = None, "unknown", "low"
        if eligible:
            prior = sum(w["normalized"] for w in weekly[:4])
            recent = sum(w["normalized"] for w in weekly[4:])
            if prior > 0:
                ratio = recent / prior
                proposed = "rising" if ratio >= cfg["rising_recent_growth"] else (
                    "fading" if ratio <= 1 / cfg["rising_recent_growth"] else "steady")
            elif recent > 0:
                # A zero denominator is eligible only after exhausted zero-result searches.
                proposed = "rising"
            if sample_posts >= cfg["medium_confidence_posts"] and \
                    all(not w.get("counts_are_lower_bound", True) for w in weekly):
                confidence = "medium"
        rows.append({"term": metric["term"], "published_momentum": metric.get("momentum", "unknown"),
                     "proposed_recent_momentum": proposed, "proposed_confidence": confidence,
                     "recent_eight_weeks_eligible": bool(eligible), "recent_growth_ratio": ratio,
                     "recent_sample_posts": sample_posts,
                     "historical_decay_ratio_unchanged": metric.get("decay_ratio"),
                     "note": "Preview only. Movement in sampled lower-bound counts is sensitive to search truncation "
                             "and is not platform growth, a historical peak, or lifespan evidence."})
    return {"policy": "proposal_recent_eight_visited_weeks", "writes": False,
            "published_policy_changed": False, "terms": rows,
            "published_non_unknown": sum(r["published_momentum"] != "unknown" for r in rows),
            "proposed_non_unknown": sum(r["proposed_recent_momentum"] != "unknown" for r in rows)}


def compute_trends(posts, communities=(), *, start=None, end=None, terms=(), config=None, coverage=None) -> list[dict]:
    """Deduplicate URIs, retain empty weeks and normalize by each week's entire corpus.

    Boundary weeks are retained and marked complete=false. Peak, decay and momentum
    use only complete weeks so a partial current week cannot create artificial fading.
    Community breadth uses only authors assigned a community. Unassigned authors are
    excluded from concentration's numerator but remain in its denominator.
    coverage is an ingest-state object. When supplied, unvisited/truncated searches
    are never evidence of absence. None means trusted complete fixture coverage;
    live callers must supply state (an empty dict correctly means unknown coverage).
    """
    cfg = config or momentum_config()
    end = utc(end or datetime.now(timezone.utc))
    start = utc(start or end - timedelta(days=365))
    if start >= end:
        raise ValueError("start must be before end")
    first_week = monday(start)
    weeks = []
    week = first_week
    while week < end:
        weeks.append(week)
        week += timedelta(days=7)
    author_communities = {d["author_hash"]: str(d["community_id"]) for d in communities
                          if d.get("author_hash") and d.get("community_id") is not None}
    unique: dict[str, dict] = {}
    for original in posts:
        # Thread enrichment selects unusually active posts and must not inflate search frequency.
        if original.get("source") == "thread":
            continue
        uri = original.get("uri")
        if not uri:
            continue
        try:
            created = utc(original.get("created_at"))
        except (ValueError, TypeError):
            continue
        if not start <= created < end:
            continue
        post = {**original, "created_at": created}
        if uri in unique:
            post["terms"] = list(dict.fromkeys(terms_for(unique[uri]) + terms_for(post)))
        unique[uri] = post
    corpus = Counter(monday(p["created_at"]) for p in unique.values())
    per_term = defaultdict(list)
    for post in unique.values():
        for term in terms_for(post):
            per_term[term].append(post)
    all_terms = sorted(set(terms) | set(per_term))
    tasks_by_term = defaultdict(list)
    if coverage is not None:
        for task in coverage.get("tasks", []):
            if task.get("term"):
                tasks_by_term[task["term"]].append(task)
    term_week_coverage = {
        (term, w): query_window_coverage(tasks_by_term[term], max(start, w), min(end, w + timedelta(days=7)))
        if coverage is not None else "complete" for term in all_terms for w in weeks}
    term_week_visited = {
        (term, w): query_window_visited(tasks_by_term[term], max(start, w), min(end, w + timedelta(days=7)))
        if coverage is not None else True for term in all_terms for w in weeks}
    term_week_saturated = {}
    for term in all_terms:
        for w in weeks:
            term_week_saturated[(term, w)] = any(task_saturated(t) for t in tasks_by_term[term]
                if utc(t["since"]) < min(end, w + timedelta(days=7)) and utc(t["until"]) > max(start, w))
    corpus_week_coverage = {
        w: ("complete" if all(term_week_coverage[(term, w)] == "complete" for term in all_terms)
            else "incomplete") for w in weeks}
    output = []
    for term in all_terms:
        samples = per_term[term]
        saturation_metadata_windows = sum(task_saturation_known(t) for t in tasks_by_term[term])
        saturation_unknown_windows = len(tasks_by_term[term]) - saturation_metadata_windows
        counts = Counter(monday(p["created_at"]) for p in samples)
        weekly = [{"week": w.date().isoformat(), "count": counts[w], "corpus_count": corpus[w],
                   "normalized": counts[w] / corpus[w] if corpus[w] else 0.0,
                   "query_coverage": term_week_coverage[(term, w)],
                   "corpus_query_coverage": corpus_week_coverage[w],
                   "query_visited": term_week_visited[(term, w)],
                   "corpus_query_visited": all(term_week_visited[(other, w)] for other in all_terms),
                   "saturated": term_week_saturated[(term, w)],
                   "counts_are_lower_bound": (term_week_saturated[(term, w)]
                       or term_week_coverage[(term, w)] != "complete" or corpus_week_coverage[w] != "complete"
                       or any(term_week_saturated[(other, w)] for other in all_terms)),
                   "complete": w >= start and w + timedelta(days=7) <= end} for w in weeks]
        complete = [w for w in weekly if w["complete"]]
        values = [w["normalized"] for w in complete]
        # A zero-corpus week is unobserved, not an observed absence of the term.
        fully_queried = [w for w in complete if w["query_coverage"] == "complete"
                         and w["corpus_query_coverage"] == "complete"]
        observed_weeks = sum(w["corpus_count"] > 0 for w in fully_queried)
        query_coverage_complete = len(complete) > 0 and len(fully_queried) == len(complete)
        # The user's saturation cap counts all observation windows, including boundary weeks.
        # Momentum calculations above and below still use only complete calendar weeks.
        saturated_weeks = sum(w["saturated"] for w in weekly)
        saturated_share = saturated_weeks / len(weekly) if weekly else 0.0
        rolling = [sum(values[i:i + 4]) for i in range(len(values) - 3)]
        peak_sum = max(rolling, default=0.0)
        # Decay is unavailable if any of the latest four weeks has no corpus.
        recent_observed = len(complete) >= 4 and all(w["corpus_count"] > 0 for w in complete[-4:])
        decay = sum(values[-4:]) / peak_sum if peak_sum and recent_observed and query_coverage_complete else None
        prior = sum(values[-8:-4]) if len(values) >= 8 else 0.0
        recent_growth = (sum(values[-4:]) / prior if prior else
                         (2.0 if sum(values[-4:]) else None)) if len(values) >= 8 else None
        peak_index = max(range(len(complete)), key=lambda i: values[i]) if values and max(values) > 0 else None
        peak_week = complete[peak_index]["week"] if peak_index is not None else None
        peak_weeks_ago = len(complete) - 1 - peak_index if peak_index is not None else None
        grouped = Counter()
        chronological = defaultdict(Counter)
        authors = {p["author_hash"] for p in samples if p.get("author_hash")}
        for post in samples:
            community = author_communities.get(post.get("author_hash"))
            if community is not None:
                grouped[community] += 1
                chronological[monday(post["created_at"])][community] += 1
        breadth = sum(n >= 3 for n in grouped.values())
        cumulative = Counter()
        breadth_week = None
        for w in weeks:
            cumulative.update(chronological[w])
            if sum(n >= 3 for n in cumulative.values()) >= 3:
                breadth_week = w
                break
        first = min((p["created_at"] for p in samples), default=None)
        spread_delay = ((breadth_week - monday(first)).days // 7
                        if breadth_week is not None and first is not None else None)
        count = len(samples)
        coverage_ok = observed_weeks >= cfg["minimum_observed_weeks"] and recent_observed and query_coverage_complete
        confidence = "low"
        if count >= cfg["medium_confidence_posts"] and coverage_ok:
            confidence = "medium"
        if count >= cfg["high_confidence_posts"] and observed_weeks >= cfg["high_confidence_weeks"] \
                and recent_observed and query_coverage_complete:
            confidence = "high"
        saturation_capped = saturated_share > 0.25 or (coverage is not None and saturation_unknown_windows > 0)
        if saturation_capped and confidence == "high":
            confidence = "medium"
        coverage_note = ("All complete calendar weeks exhausted the sampled term searches."
                         if query_coverage_complete else
                         "Unvisited, truncated or unknown term/corpus searches are not observed absence. "
                         "Decay is unavailable; momentum is unknown and confidence stays low.")
        if saturated_weeks:
            coverage_note += (f" {saturated_weeks}/{len(weekly)} observation weekly windows, including partial boundary "
                              "weeks, saturated the 20-post "
                              "first page. Those counts remain labeled lower bounds even after confirmed exhaustion.")
        if saturated_share > 0.25:
            coverage_note += " Saturation exceeds 25 percent, so confidence is capped at medium."
        if saturation_unknown_windows:
            coverage_note += (f" First-page saturation metadata is missing for {saturation_unknown_windows} windows; "
                              "reported saturated-window counts exclude those unknown windows. "
                              "Confidence is capped at medium until saturation metadata is verified.")
        metrics = {"term": term, "weekly_counts": weekly,
                   "first_seen_in_window": iso(first) if first else None,
                   "peak_week": peak_week, "decay_ratio": decay, "breadth": breadth,
                   "concentration": max(grouped.values()) / count if grouped and count else None,
                   "weeks_to_breadth_3": spread_delay, "authors": len(authors), "post_count": count,
                   "confidence": confidence, "observed_weeks": observed_weeks,
                   "peak_weeks_ago": peak_weeks_ago, "recent_growth": recent_growth,
                   "query_coverage_complete": query_coverage_complete,
                   "query_coverage_status": ("complete" if query_coverage_complete else "incomplete_or_unknown"),
                   "query_complete_weeks": len(fully_queried),
                   "calendar_complete_weeks": len(complete),
                   "completed_windows": sum(query_window_coverage([t], utc(t["since"]), utc(t["until"])) == "complete"
                                            for t in tasks_by_term[term]),
                   "total_windows": len(tasks_by_term[term]),
                   "saturated_windows": sum(task_saturated(t) for t in tasks_by_term[term]),
                   "saturation_metadata_windows": saturation_metadata_windows,
                   "saturation_unknown_windows": saturation_unknown_windows,
                   "saturated_weeks": saturated_weeks, "saturated_week_share": saturated_share,
                   "saturation_week_denominator": len(weekly),
                   "saturation_week_basis": "all_observation_weekly_windows_including_partial_boundaries",
                   "saturation_confidence_cap": "medium" if saturation_capped else None,
                   "counts_are_lower_bound": any(w["counts_are_lower_bound"] for w in weekly),
                   "query_incomplete_weeks": [w["week"] for w in complete if w not in fully_queried],
                   "coverage_note": coverage_note,
                   "window_start": iso(start), "window_end": iso(end), "computed_at": iso(datetime.now(timezone.utc)),
                   "estimate": True, "counts_are_sampled": True}
        metrics["momentum"] = classify_momentum(metrics, cfg)
        output.append(metrics)
    return output


@lru_cache(maxsize=1)
def collections():
    # Lazy import keeps pure metrics and offline tests independent of Mongo credentials.
    from backend.db import db
    return db["social_posts"], db["social_trends"]


def vector_search_posts(vector, *, limit=30, term=None, since=None, until=None, collection=None,
                        timeout_ms=700) -> list[dict]:
    """Top-k Atlas retrieval shared with Ask and evaluation. Scores are plain cosine.

    Field, index and dimensions come from the same SOCIAL_EMBED_PROVIDER spec as embed_query.
    """
    from backend.services.social_embeddings import spec as embedding_spec
    spec = embedding_spec()
    if len(vector) != spec.dims or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in vector):
        raise ValueError(f"a finite {spec.dims}-dimensional embedding is required")
    limit = max(1, min(int(limit), 100))
    filters = {}
    if term:
        filters["term"] = str(term)
    if since is not None or until is not None:
        filters["created_at"] = {}
        if since is not None:
            filters["created_at"]["$gte"] = iso(utc(since))
        if until is not None:
            filters["created_at"]["$lt"] = iso(utc(until))
    stage = {"index": spec.index, "path": spec.field, "queryVector": list(vector),
             "numCandidates": max(100, 20 * limit), "limit": limit}
    if filters:
        stage["filter"] = filters
    pipeline = [{"$vectorSearch": stage}, {"$project": {"_id": 0, "uri": 1, "term": 1, "terms": 1,
                "text": 1, "author_hash": 1, "created_at": 1,
                "atlas_score": {"$meta": "vectorSearchScore"}}}]
    posts = collection if collection is not None else collections()[0]
    with mongo_timeout(timeout_ms / 1000):
        rows = list(posts.aggregate(pipeline, maxTimeMS=timeout_ms))
    return [{**row, "similarity": max(-1.0, min(1.0, 2 * float(row.get("atlas_score", 0)) - 1))}
            for row in rows]


def embed_query(text: str, timeout: float = 3.0):
    """Query vector from the Social provider (Vultr by default), never the closet embedder.

    The runtime caller enforces its own sub-0.5 s deadline. The HTTP timeout here is longer
    so a slow first lookup can still finish in the background and fill the caches.
    """
    from backend.services.social_embeddings import embed_query as provider_embed_query, spec
    s = spec()
    key = (s.provider, s.model, s.dims, text)
    with _CACHE_LOCK:
        vector = _VECTOR_CACHE.get(key)
        if vector is not None:
            _VECTOR_CACHE.move_to_end(key)
            return list(vector)
    vector = provider_embed_query(text, timeout=timeout)
    with _CACHE_LOCK:
        # A query vector never goes stale for the same provider, model and dims, so this
        # cache outlives the ten-minute trend cache. Measured Vultr p50 is about 1.3 s;
        # a repeat lookup then costs only Atlas retrieval. Failures are never cached.
        _VECTOR_CACHE[key] = tuple(vector)
        while len(_VECTOR_CACHE) > MAX_VECTOR_CACHE_ITEMS:
            _VECTOR_CACHE.popitem(last=False)
    return vector


def item_query(item) -> str:
    if hasattr(item, "model_dump"):
        item = item.model_dump()
    attrs = item.get("attributes") or {}
    return " ".join(str(v).strip() for v in (item.get("name"), item.get("category") or attrs.get("category"),
                     item.get("color_family") or item.get("color") or attrs.get("color_family")) if v).strip()[:600]


def context_from_hits(hits: list[dict], config=None, trends=None) -> dict | None:
    """Unweighted top-30 term voting followed by cosine and vote-share gates."""
    cfg = config or momentum_config()
    hits = hits[:30]
    if not hits:
        return None
    votes = Counter(row.get("term") for row in hits if row.get("term"))
    if not votes:
        return None
    term, count = sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))[0]
    matched = [p for p in hits if p.get("term") == term]
    similarity = max(float(p.get("similarity", -1)) for p in matched)
    if count / len(hits) < cfg["minimum_vote_share"] or similarity < cfg["minimum_similarity"]:
        return None
    trend_collection = trends if trends is not None else collections()[1]
    with mongo_timeout(0.3):
        trend = trend_collection.find_one({"term": term}, {"_id": 0}, max_time_ms=300)
    if not trend:
        return None
    keys = ("term", "first_seen_in_window", "peak_week", "decay_ratio", "breadth", "concentration",
            "momentum", "confidence", "query_coverage_complete", "coverage_note", "saturated_week_share",
            "saturation_confidence_cap", "counts_are_lower_bound")
    result = {k: trend[k] for k in keys if k in trend}
    result.update(similarity=similarity, weekly_counts=list(trend.get("weekly_counts") or [])[-26:], estimate=True)
    result["examples"] = [{"uri": p["uri"], "url": post_url(p["uri"]), "snippet": sanitize_snippet(p.get("text"))}
                          for p in sorted(matched, key=lambda p: -p.get("similarity", -1)) if post_url(p.get("uri"))][:2]
    return result


def _lookup(query: str, cfg: dict):
    return context_from_hits(vector_search_posts(embed_query(query)), cfg)


def _run_lookup(query: str, cfg: dict):
    try:
        return _lookup(query, cfg)
    except Exception:
        # Exception strings from SDKs can include URLs or supplied text. Never log them.
        return None
    finally:
        _RUNTIME_SLOTS.release()


def clear_cache():
    with _CACHE_LOCK:
        _CACHE.clear()
        _INFLIGHT.clear()
        _VECTOR_CACHE.clear()


def _finish_lookup(query, cfg, future):
    try:
        result = future.result()
    except Exception:
        result = None
    with _CACHE_LOCK:
        if _INFLIGHT.get(query) is not future:
            return
        _INFLIGHT.pop(query, None)
        if len(_CACHE) >= MAX_CACHE_ITEMS:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[query] = (time.monotonic() + float(cfg["cache_seconds"]), copy.deepcopy(result))


def social_context(item) -> dict | None:
    """Optional context. No-match, errors and deadline expiry all return None.

    A bounded shared pool prevents a stalled embedding provider from creating a
    fresh thread per request. In-flight calls are reused. Cache hits copy results.
    SOCIAL_RADAR_ENABLED=1 activates retrieval after the offline pipeline is ready.
    """
    if os.getenv("SOCIAL_RADAR_ENABLED", "0").lower() not in ("1", "true", "yes"):
        return None
    query = item_query(item)
    if not query:
        return None
    cfg = momentum_config()
    now = time.monotonic()
    created = False
    with _CACHE_LOCK:
        cached = _CACHE.get(query)
        if cached and cached[0] > now:
            return copy.deepcopy(cached[1])
        future = _INFLIGHT.get(query)
        if future is None:
            if not _RUNTIME_SLOTS.acquire(blocking=False):
                return None
            try:
                future = _RUNTIME_POOL.submit(_run_lookup, query, cfg)
            except Exception:
                _RUNTIME_SLOTS.release()
                return None
            _INFLIGHT[query] = future
            created = True
    if created:
        # Register outside the lock: an already-complete future calls this inline.
        future.add_done_callback(lambda done: _finish_lookup(query, cfg, done))
    try:
        result = future.result(timeout=float(cfg["lookup_timeout_seconds"]))
    except FutureTimeout:
        return None
    except Exception:
        result = None
    return copy.deepcopy(result)
