"""Rule baseline for micro-trend classification over sampled Bluesky social_trends docs.

All thresholds live in config/social_micro_trend.json. Evaluation labels and press_status
are never inputs. See docs/micro_trend_rules.md for definitions and measured counts.

PART 0 (press seed loading) is skipped: seed/trends_seed_press.json does not exist.

Corpus per week: social_trends.weekly_counts[].corpus_count, written by
backend.services.social.compute_trends. It is the number of unique search posts (all terms,
deduplicated by URI, thread-enrichment posts excluded) created in that UTC Monday week.
share_w = count / corpus_count, or 0 when corpus_count is 0.

CLI: python -m backend.services.micro_trend [--write] [--json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "social_micro_trend.json"
MANUAL_PATH = ROOT / "config" / "manual_labels.json"
LABELS = ("micro_trend", "established", "still_active", "unknown")
CENSORED_AT_CAP = "censored at cap"
NOT_COMPUTED = "not computed"


# ---------------------------------------------------------------- config

def rule_config() -> dict:
    return json.loads(CONFIG_PATH.read_text())


def config_hash(cfg: dict | None = None) -> str:
    cfg = rule_config() if cfg is None else cfg
    return hashlib.sha256(json.dumps(cfg, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


# ---------------------------------------------------------------- manual labels

def _norm(term) -> str:
    return " ".join(str(term or "").casefold().split())


@lru_cache(maxsize=1)
def _manual_file() -> dict:
    return json.loads(MANUAL_PATH.read_text())


def load_manual_labels() -> dict[str, dict]:
    """Return {normalized term: entry} from config/manual_labels.json."""
    return {_norm(e["term"]): dict(e) for e in _manual_file()["labels"]}


def is_manual(term) -> bool:
    if isinstance(term, dict):
        term = term.get("term")
    return _norm(term) in load_manual_labels()


def manual_decision(term) -> dict | None:
    """Editorial decision for a manual style, or None. Never carries lifespan fields."""
    if isinstance(term, dict):
        term = term.get("term")
    entry = load_manual_labels().get(_norm(term))
    if entry is None:
        return None
    return {"final_label": entry["final_label"], "decision_source": "manual",
            "confidence": entry["confidence"], "source_text": entry["source_text"],
            "display": _manual_file()["display"], "excluded_from_agreement": True}


# ---------------------------------------------------------------- doc access

def _doc(style, collection=None) -> dict | None:
    if isinstance(style, dict):
        return style
    if collection is None:
        from backend.db import db  # lazy: needs MONGODB_URI
        collection = db["social_trends"]
    return collection.find_one({"term": style}, {"_id": 0})


def metric_weeks(style, collection=None) -> list[dict]:
    """Weekly entries from the first to the last full weekly window (partial edge windows excluded).

    Uses the stored complete flag. Without any complete flags, drops the first and last entries.
    The stored observed_weeks field is not used.
    """
    doc = _doc(style, collection) or {}
    weekly = list(doc.get("weekly_counts") or [])
    if any("complete" in w for w in weekly):
        idx = [i for i, w in enumerate(weekly) if w.get("complete")]
        return weekly[idx[0]:idx[-1] + 1] if idx else []
    return weekly[1:-1]


def weeks_with_posts(style, collection=None) -> int:
    return sum(1 for w in metric_weeks(style, collection) if (w.get("count") or 0) > 0)


def post_count(style, collection=None) -> int:
    doc = _doc(style, collection) or {}
    if isinstance(doc.get("post_count"), int):
        return doc["post_count"]
    return sum(w.get("count") or 0 for w in doc.get("weekly_counts") or [])


def category_for(term: str, cfg: dict) -> str | None:
    key = _norm(term)
    if key in {_norm(k): v for k, v in cfg.get("term_categories", {}).items()}:
        return {_norm(k): v for k, v in cfg["term_categories"].items()}[key]
    tokens = set(re.findall(r"[a-z0-9']+", key))
    for category, words in cfg.get("category_tokens", {}).items():
        if tokens & set(words):
            return category
    return None


# ---------------------------------------------------------------- series helpers

def _smooth(values: list[float], window: int) -> list[float]:
    half = window // 2
    out = []
    for i in range(len(values)):
        part = values[max(0, i - half):min(len(values), i + half + 1)]
        out.append(sum(part) / len(part) if part else 0.0)
    return out


def _active_run(active: list[bool], peak: int, gap: int) -> tuple[int, int]:
    start = end = peak
    j = peak + 1
    while j < len(active):
        if active[j]:
            end = j
        elif j - end > gap:
            break
        j += 1
    j = peak - 1
    while j >= 0:
        if active[j]:
            start = j
        elif start - j > gap:
            break
        j -= 1
    return start, end


def _span(counts: list[int], fraction: float) -> int | None:
    total = sum(counts)
    if total <= 0:
        return None
    need = fraction * total
    best, left, acc = None, 0, 0
    for right, c in enumerate(counts):
        acc += c
        while acc - counts[left] >= need and left < right:
            acc -= counts[left]
            left += 1
        if acc >= need:
            width = right - left + 1
            best = width if best is None else min(best, width)
    return best


def _test(result: str, cause: str, **extra) -> dict:
    return {"result": result, "cause": cause, **extra}


# ---------------------------------------------------------------- rules

def compute_rule_metrics(style, *, config: dict | None = None, collection=None) -> dict:
    """Rule outputs for a social_trends doc or a term string. See module docstring and docs."""
    cfg = config or rule_config()
    doc = _doc(style, collection)
    if doc is None:
        raise LookupError(f"social_trends doc not found: {style!r}")
    term = doc.get("term")
    weeks = metric_weeks(doc)
    n = len(weeks)
    counts = [int(w.get("count") or 0) for w in weeks]
    shares = [(w.get("count") or 0) / w["corpus_count"] if w.get("corpus_count") else 0.0 for w in weeks]
    smooth = _smooth(shares, int(cfg["smoothing_window_weeks"]))
    peak_value = max(smooth, default=0.0)
    peak = smooth.index(peak_value) if n and peak_value > 0 else None
    posts = post_count(doc)
    wwp = sum(1 for c in counts if c > 0)
    all_weeks = doc.get("weekly_counts") or []
    saturated_weeks = sum(1 for w in all_weeks if w.get("saturated"))
    saturated_share = saturated_weeks / len(all_weeks) if all_weeks else 0.0
    category = category_for(term or "", cfg)
    seasonal = category in set(cfg["seasonal_categories"])
    half = cfg["half_fraction_of_peak"] * peak_value

    run = None
    peak_censored = False
    if peak is not None:
        peak_censored = bool(weeks[peak].get("saturated"))
        active = [v >= cfg["active_fraction_of_peak"] * peak_value for v in smooth]
        start, end = _active_run(active, peak, int(cfg["active_gap_weeks"]))
        run = {"start": start, "end": end, "weeks_active": end - start + 1,
               "touches_left": start == 0, "touches_right": end == n - 1}

    # Test 1: fast rise
    if run is None:
        t1 = _test("unknown", "no posts in metric weeks")
    elif peak_censored:
        t1 = _test("unknown", CENSORED_AT_CAP)
    elif run["touches_left"]:
        t1 = _test("unknown", "left-censored: run starts at window edge, rise not observed")
    else:
        i50 = next(i for i in range(run["start"], peak + 1) if smooth[i] >= half)
        ramp = i50 - run["start"]
        ok = ramp <= cfg["test1_max_ramp_weeks"]
        t1 = _test("pass" if ok else "fail", f"ramp {ramp} weeks", ramp_weeks=ramp)

    # Test 2: short life
    if run is None:
        t2 = _test("unknown", "no posts in metric weeks")
    elif peak_censored:
        t2 = _test("unknown", CENSORED_AT_CAP)
    else:
        wa = run["weeks_active"]
        edge = run["touches_left"] or run["touches_right"]
        if wa > cfg["test2_max_weeks_active"]:
            path_a = "fail"
        else:
            path_a = "unresolved" if edge else "pass"
        fall_within, rebound_within = int(cfg["test2_fall_within_weeks"]), int(cfg["test2_no_rebound_weeks"])
        fall = next((peak + k for k in range(1, fall_within + 1) if peak + k < n and smooth[peak + k] <= half), None)
        if fall is None:
            path_b = "fail" if peak + fall_within < n else "unresolved"
        else:
            after = smooth[fall + 1:fall + rebound_within + 1]
            if any(v > half for v in after):
                path_b = "fail"
            else:
                path_b = "pass" if fall + rebound_within < n else "unresolved"
        if "pass" in (path_a, path_b):
            result = "pass"
        elif path_a == "fail" and path_b == "fail":
            result = "fail"
        else:
            result = "unknown"
        cause = {"pass": "short active run" if path_a == "pass" else "fell below half of peak without rebound",
                 "fail": f"active {wa} weeks and no fall without rebound",
                 "unknown": "run touches window edge unresolved"}[result]
        t2 = _test(result, cause, weeks_active=wa, path_weeks_active=path_a, path_fall=path_b,
                   fall_week=weeks[fall]["week"] if fall is not None else None)

    # Test 3: not a staple
    first = int(cfg["test3_first_metric_weeks"])
    early_mean = sum(smooth[:first]) / first if n >= first else None
    if seasonal:
        t3 = _test("unknown", f"seasonal category: {category}")
    elif peak is None or early_mean is None:
        t3 = _test("unknown", "insufficient metric weeks or no posts")
    else:
        ok = early_mean < cfg["test3_max_mean_fraction_of_peak"] * peak_value
        t3 = _test("pass" if ok else "fail",
                   f"first {first} weeks mean {early_mean / peak_value:.2f} of peak",
                   early_mean_fraction_of_peak=round(early_mean / peak_value, 4))

    # Test 4: spreads fast (existing social_trends fields)
    wtb3, conc = doc.get("weeks_to_breadth_3"), doc.get("concentration")
    if wtb3 is None or conc is None:
        t4 = _test("unknown", "weeks_to_breadth_3 or concentration missing")
    else:
        ok = wtb3 <= cfg["test4_max_weeks_to_breadth_3"] and conc <= cfg["test4_max_concentration"]
        t4 = _test("pass" if ok else "fail", f"weeks_to_breadth_3 {wtb3}, concentration {conc:.2f}")
    t4["interaction_only"] = NOT_COMPUTED

    # Outcome, tests 1-3
    reason_codes, reasons = [], []

    def add(code, text):
        reason_codes.append(code)
        reasons.append(text)

    if posts < cfg["min_posts"]:
        add("posts_below_min", f"posts {posts} < {cfg['min_posts']}")
    if wwp < cfg["min_weeks_with_posts"]:
        add("weeks_with_posts_below_min", f"weeks with posts {wwp} < {cfg['min_weeks_with_posts']}")
    if seasonal:
        add("seasonal_category", f"seasonal category: {category}")
    if peak is None:
        add("no_posts", "no posts in metric weeks")
    if reason_codes:
        label = "unknown"
    elif t3["result"] == "fail":
        label = "established"
        add("left_censored_staple", "left-censored: early mean share >= half of peak")
    elif run["touches_right"] and smooth[-1] >= cfg["still_active_min_fraction_of_peak"] * peak_value:
        label = "still_active"
        add("right_censored_active", "right-censored: last metric week still >= half of peak")
    elif all(t["result"] == "pass" for t in (t1, t2, t3)):
        label = "micro_trend"
        add("tests_1_3_pass", "fast rise, short life, not a staple")
    else:
        label = "unknown"
        for name, t in (("test1", t1), ("test2", t2), ("test3", t3)):
            if t["result"] == "pass":
                continue
            if t["cause"] == CENSORED_AT_CAP:
                code = f"{name}_censored_at_cap"
            elif t["result"] == "fail":
                code = f"{name}_fail"
            elif "edge" in t["cause"] or "censored" in t["cause"]:
                code = f"{name}_edge_unresolved"
            else:
                code = f"{name}_unknown"
            add(code, f"{name}: {t['cause']}")

    if label == "micro_trend":
        label_1_4 = {"pass": "micro_trend"}.get(t4["result"], "unknown")
    else:
        label_1_4 = label

    # Confidence
    data_short = any(c in reason_codes for c in ("posts_below_min", "weeks_with_posts_below_min", "no_posts"))
    if data_short or posts < cfg["min_posts"]:
        confidence = "low"
    elif posts >= cfg["high_confidence_posts"] and wwp >= cfg["high_confidence_weeks_with_posts"]:
        confidence = "high"
    else:
        confidence = "medium"
    confidence_caps = []
    if saturated_share > cfg["saturated_share_medium_cap"]:
        confidence_caps.append("saturated share above cap")
    if (doc.get("saturation_unknown_windows") or 0) > 0:
        confidence_caps.append("saturation metadata missing")
    if confidence_caps and confidence == "high":
        confidence = "medium"

    first_post = next((i for i, c in enumerate(counts) if c > 0), None)
    first_k = int(cfg["first_weeks_share_weeks"])
    total_metric_posts = sum(counts)
    share_first = (sum(counts[first_post:first_post + first_k]) / total_metric_posts
                   if first_post is not None else None)

    return {
        "term": term,
        "rule_label": label,
        "reason_codes": reason_codes,
        "reasons": reasons,
        "test1": t1, "test2": t2, "test3": t3, "test4": t4,
        "tests_1_3": label,
        "tests_1_4": label_1_4,
        "confidence": confidence,
        "confidence_caps": confidence_caps,
        "posts": posts,
        "metric_weeks": n,
        "metric_window": [weeks[0]["week"], weeks[-1]["week"]] if n else None,
        "weeks_with_posts": wwp,
        "peak_week": weeks[peak]["week"] if peak is not None else None,
        "peak_share_smoothed": peak_value,
        "peak_censored": peak_censored,
        "active_run": ({"start_week": weeks[run["start"]]["week"], "end_week": weeks[run["end"]]["week"],
                        "weeks_active": run["weeks_active"], "touches_left": run["touches_left"],
                        "touches_right": run["touches_right"]} if run else None),
        "saturated_weeks": saturated_weeks,
        "saturated_share": round(saturated_share, 4),
        "category": category,
        "seasonal": seasonal,
        "span_75": _span(counts, cfg["span_fraction"]),
        "share_first_2_weeks": round(share_first, 4) if share_first is not None else None,
        "manual": is_manual(term),
        "excluded_from_agreement": is_manual(term),
        "metric_version": cfg["metric_version"],
        "config_hash": config_hash(cfg),
    }


def measured_active_run(style, *, config: dict | None = None, collection=None) -> dict | None:
    """Fully observed active run, or None when not measurable.

    None for manual styles, data-short or seasonal styles (any unknown input reason),
    a peak censored at the weekly cap, or a run touching either metric window edge.
    """
    m = compute_rule_metrics(style, config=config, collection=collection)
    blockers = {"posts_below_min", "weeks_with_posts_below_min", "seasonal_category", "no_posts"}
    run = m["active_run"]
    if (m["manual"] or blockers & set(m["reason_codes"]) or m["peak_censored"] or run is None
            or run["touches_left"] or run["touches_right"]):
        return None
    return {"start_week": run["start_week"], "end_week": run["end_week"],
            "weeks_active": run["weeks_active"], "peak_week": m["peak_week"]}


# ---------------------------------------------------------------- CLI

def summarize(results: list[dict]) -> dict:
    return {"total": len(results),
            "by_label": dict(Counter(r["rule_label"] for r in results)),
            "by_label_tests_1_4": dict(Counter(r["tests_1_4"] for r in results)),
            "by_reason": dict(Counter(c for r in results for c in r["reason_codes"])),
            "by_confidence": dict(Counter(r["confidence"] for r in results)),
            "peak_censored": sum(r["peak_censored"] for r in results)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Compute rule_label for every social_trends doc.")
    parser.add_argument("--write", action="store_true",
                        help="persist rule_label, rule_metrics, metric_version, rule_config_hash")
    parser.add_argument("--json", action="store_true", help="print per-term results as JSON")
    args = parser.parse_args(argv)
    import os
    from dotenv import load_dotenv
    from pymongo import MongoClient
    load_dotenv(ROOT / ".env")
    coll = MongoClient(os.environ["MONGODB_URI"], serverSelectionTimeoutMS=10000)["secondlook"]["social_trends"]
    cfg = rule_config()
    results = []
    for doc in coll.find({}).sort("term", 1):
        m = compute_rule_metrics(doc, config=cfg)
        results.append(m)
        if args.write:
            coll.update_one({"_id": doc["_id"]}, {"$set": {
                "rule_label": m["rule_label"], "rule_metrics": m,
                "metric_version": m["metric_version"], "rule_config_hash": m["config_hash"]}})
    if args.json:
        print(json.dumps(results, indent=1, default=str))
    else:
        for m in results:
            print(f"{m['term']:<20} {m['rule_label']:<13} 1-4={m['tests_1_4']:<13} {m['confidence']:<6} "
                  f"t1={m['test1']['result']:<7} t2={m['test2']['result']:<7} t3={m['test3']['result']:<7} "
                  f"t4={m['test4']['result']:<7} {'; '.join(m['reason_codes'])}")
    print(json.dumps(summarize(results), indent=1))
    if args.write:
        print(f"wrote {len(results)} docs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
