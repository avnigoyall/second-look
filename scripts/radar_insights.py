#!/usr/bin/env python3
"""Cap-robust composition metrics for Radar terms. Read-only on Atlas, no paid calls.

  python scripts/radar_insights.py           # print the report tables
  python scripts/radar_insights.py --json    # full numbers as JSON
  python scripts/radar_insights.py --samples resale   # print a labelled sample for regex precision checks (local only)

Idea: weekly counts are capped and collection depth changed on 2026-08-31, so raw volume is unreliable.
These metrics describe WHAT KIND of posts appear, not how many. To also remove the depth change, every
metric is computed on a cap-consistent sample: the newest CAP posts of each term-week (what the first page
returned before 2026-08-31). Every metric definition lives in METRICS / the regexes below.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TERMS_PATH = ROOT / "config" / "social_terms.json"

CAP = 20                      # first-page cap per term-week
BLOCK_WEEKS = 4               # block length in weeks
MIN_N = 20                    # minimum posts in a block for a block value to count
FIRST_WEEK = datetime(2025, 11, 10, tzinfo=UTC)   # Monday of the first window week
DEPTH_CHANGE = datetime(2026, 8, 31, tzinfo=UTC)  # collection got deeper from this week
RECENT_BLOCKS = 3             # "recent" = last 3 complete blocks; "early" = the blocks before
RAREFY_N = 20                 # subsample size for distinct-community counts
SEED = 7
BASELINE_EXCLUDE = {"Levi's 501"}  # one resale bot drives this term (measured: 70% commerce in the last quarter)

# ---- text classifiers (one place) -------------------------------------------------------------
# Commerce: any listing, deal or promo. Precision check (40 random matches, read by hand): 33 of 40 were listings,
# deals or promos; the rest were articles or comments that mention a price. Most matches are NEW-product deal
# posts (Amazon affiliate style), not secondhand resale, so commerce is split into DEAL and SECONDHAND.
DEAL_RE = re.compile(
    r"(amazon\.com|amzn\.to|a\.co/|only [$£€]\s?\d|\breg\.? [$£€]|\(reg|\d+% off|price drop|\bdeal(s)?\b|"
    r"coupon|promo code|with code|\bcode [a-z0-9]{4,}|#ad\b|#sponsored|#affiliate|shop now|buy now|link in bio|"
    r"add to cart|free shipping|dropped to|now [$£€]\s?\d|was [$£€]\s?\d)", re.IGNORECASE)
SECONDHAND_RE = re.compile(
    r"(depop|poshmark|posh\.mk|ebay|vinted|mercari|grailed|offerup|thredup|\bnwt\b|\bnwot\b|new with tags|"
    r"pre-?owned|pre-?loved|secondhand|second-hand|thrifted|\bused\b|some wear|gently worn|\blistings?\b|"
    r"just listed|for sale|\bwts\b|\bobo\b)", re.IGNORECASE)
COMMERCE_RE = re.compile(
    r"(" + DEAL_RE.pattern + "|" + SECONDHAND_RE.pattern + r"|etsy\.(me|com)|[$£€]\s?\d+(\.\d\d)?\b|"
    r"\d+(\.\d\d)? ?(usd|gbp|eur)\b|check out this)", re.IGNORECASE)
BACKLASH_RE = re.compile(
    r"(\bis over\b|\bare over\b|\bso over\b|\bdead\b|\bdied\b|\bugly\b|\bcringe\b|\bhate\b|\bhated\b|"
    r"\brip\b|\bout of style\b|\boutdated\b|\bdated\b|\bnever again\b|\bstop wearing\b|\bwhy is everyone\b|"
    r"\bover it\b|\btacky\b|\bembarrassing\b)", re.IGNORECASE)
REVIVAL_RE = re.compile(
    r"(\bbring back\b|\bbrought back\b|\bis back\b|\bare back\b|\bcoming back\b|\bcomeback\b|"
    r"\bcome back\b|\bstill wear(ing)?\b|\brevival\b|\bin again\b|\bmaking a return\b)", re.IGNORECASE)
DESIRE_RE = re.compile(
    r"(\bi want\b|\bi need\b|\bneed some\b|\bwant some\b|\bwhere (can|do) i (find|get|buy)\b|"
    r"\bobsessed\b|\bjust bought\b|\bjust got\b|\bordered\b|\btiktok made me\b|\bwishlist\b|\bcan'?t wait\b|"
    r"\bin love with\b|\bdying for\b|\bon the hunt\b|\blooking for\b|\brecommend\b)", re.IGNORECASE)
WEARING_RE = re.compile(
    r"(\b(i'?m|im|i am) wearing\b|\bwore\b|\bwearing my\b|\bmy new\b|\bmy (favou?rite|fave)\b|\bootd\b|"
    r"\boutfit of the day\b|\bfit check\b|\btoday'?s (fit|outfit)\b|\bput on my\b|\bin my\b)", re.IGNORECASE)

FLAGS = {"commerce": COMMERCE_RE, "deal": DEAL_RE, "secondhand": SECONDHAND_RE, "backlash": BACKLASH_RE, "revival": REVIVAL_RE, "desire": DESIRE_RE,
         "wearing": WEARING_RE}

# metric name -> short definition (doc table uses this)
METRICS = {
    "likes_median": "median like_count per post",
    "likes_mean": "mean like_count per post",
    "zero_engagement": "share of posts with 0 likes, 0 reposts, 0 replies",
    "commerce": "share matching COMMERCE_RE (any listing, deal, promo or price)",
    "commerce_excess": "commerce share minus the same block's pooled commerce share in all OTHER terms, Levi's 501 bot excluded",
    "deal": "share matching DEAL_RE (new-product deal posts: amazon links, 'only $', '% off', codes, #ad)",
    "secondhand": "share matching SECONDHAND_RE (depop, poshmark, ebay, vinted, pre-owned, listings ...)",
    "backlash": "share matching BACKLASH_RE (over, dead, ugly, cringe, hate, RIP ...)",
    "revival": "share matching REVIVAL_RE (bring back, is back, comeback, still wearing ...)",
    "desire": "share matching DESIRE_RE (want, need, where can I find, obsessed, just bought ...)",
    "wearing": "share matching WEARING_RE (first-person wearing: wore, wearing my, OOTD, my new ...)",
    "conversation": "share of posts that are replies or quotes",
    "newcomer": "share of a block's authors who never posted the term in an earlier block",
    "top_author_share": "share of a block's posts written by its single most active author",
    "communities_per20": "distinct graph communities among 20 random posts (rarefied, mean of 50 draws)",
    "drift_from_first": "cosine distance of block embedding centroid to the term's first-block centroid",
    "dispersion": "mean cosine distance of block posts to their block centroid",
    "velocity_pph": "saturated weeks only: CAP / hours spanned by the newest CAP posts (posts per hour)",
}


def utc(value):
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromisoformat(str(value))


def week_index(ts):
    return (utc(ts) - FIRST_WEEK).days // 7


def flag(text, name):
    return bool(FLAGS[name].search(text or ""))


def cap_sample(posts, cap=CAP):
    """Newest `cap` posts per (term, week). Removes the 2026-08-31 depth change."""
    groups = defaultdict(list)
    for p in posts:
        groups[(p["term"], week_index(p["created_at"]))].append(p)
    out = []
    for group in groups.values():
        group.sort(key=lambda p: utc(p["created_at"]), reverse=True)
        out.extend(group[:cap])
    return out


def velocity(group, week_end, cap=CAP):
    """Posts per hour from the time the newest `cap` posts span back from the window end. None if not saturated."""
    if len(group) < cap:
        return None
    times = sorted((utc(p["created_at"]) for p in group), reverse=True)
    hours = max((week_end - times[cap - 1]).total_seconds() / 3600.0, 0.25)
    return cap / hours


def cosine_distance(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return 1.0 - dot / (na * nb) if na and nb else None


def share(posts, pred):
    return sum(1 for p in posts if pred(p)) / len(posts) if posts else None


def block_metrics(block_posts, seen_authors, community_of, rng, centroid_fn=None, first_centroid=None):
    """Composition metrics for one block. seen_authors = authors seen in earlier blocks (not mutated)."""
    n = len(block_posts)
    likes = [p.get("like_count") or 0 for p in block_posts]
    authors = Counter(p.get("author_hash") for p in block_posts)
    m = {
        "n": n,
        "likes_median": statistics.median(likes),
        "likes_mean": sum(likes) / n,
        "zero_engagement": share(block_posts, lambda p: not ((p.get("like_count") or 0) + (p.get("repost_count") or 0)
                                                             + (p.get("reply_count") or 0))),
        "conversation": share(block_posts, lambda p: bool(p.get("reply_parent_uri") or p.get("quote_uri"))),
        "newcomer": sum(1 for a in authors if a not in seen_authors) / len(authors),
        "top_author_share": max(authors.values()) / n,
    }
    for name in FLAGS:
        m[name] = share(block_posts, lambda p, name=name: flag(p.get("text"), name))
    comms = [community_of.get(p.get("author_hash")) for p in block_posts]
    if n >= RAREFY_N:
        draws = [len({c if c else f"solo:{i}" for i, c in enumerate(rng.sample(comms, RAREFY_N))}) for _ in range(50)]
        m["communities_per20"] = sum(draws) / len(draws)
    if centroid_fn:
        cen, disp = centroid_fn(block_posts)
        m["dispersion"] = disp
        m["drift_from_first"] = cosine_distance(cen, first_centroid) if cen is not None and first_centroid is not None else None
    return m


def trajectory(values, recent=RECENT_BLOCKS):
    """Recent minus early, on block values that exist. None if either side is empty."""
    vals = [v for v in values if v is not None]
    if len(vals) < recent + 2:
        return None
    early, late = vals[:-recent], vals[-recent:]
    return statistics.mean(late) - statistics.mean(early)


def mann_whitney(a, b):
    """U for a > b, plus exact permutation p (two-sided) over all splits. Small n only."""
    from itertools import combinations
    a, b = [x for x in a if x is not None], [x for x in b if x is not None]
    if not a or not b:
        return None
    def u_stat(xs, ys):
        return sum(1.0 if x > y else 0.5 if x == y else 0.0 for x in xs for y in ys)
    u = u_stat(a, b)
    pooled = a + b
    total = len(a) * len(b)
    obs = abs(u - total / 2)
    hits = count = 0
    for idx in combinations(range(len(pooled)), len(a)):
        s = set(idx)
        xs = [pooled[i] for i in idx]
        ys = [pooled[i] for i in range(len(pooled)) if i not in s]
        count += 1
        hits += abs(u_stat(xs, ys) - total / 2) >= obs - 1e-9
    return {"U": u, "pairs": total, "auc": u / total, "p_perm": hits / count, "n_a": len(a), "n_b": len(b)}


def spearman(xs, ys):
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 4:
        return None
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(v):
            j = i
            while j + 1 < len(v) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2
            i = j + 1
        return r
    rx, ry = ranks([p[0] for p in pairs]), ranks([p[1] for p in pairs])
    mx, my = statistics.mean(rx), statistics.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else None


# ---- data loading and the full analysis -------------------------------------------------------
def load():
    from dotenv import load_dotenv
    from pymongo import MongoClient
    load_dotenv(ROOT / ".env")
    db = MongoClient(os.environ["MONGODB_URI"], tz_aware=True, serverSelectionTimeoutMS=10000)["secondlook"]
    proj = {k: 1 for k in ["term", "text", "created_at", "author_hash", "like_count", "repost_count", "reply_count", "reply_parent_uri", "quote_uri", "embedding_vultr"]}
    posts = list(db.social_posts.find({}, proj))
    community_of = {c["author_hash"]: c["community_id"] for c in db.social_communities.find({}, {"author_hash": 1, "community_id": 1})}
    return posts, community_of


def analyse(posts, community_of, press, block_weeks=BLOCK_WEEKS, min_n=MIN_N, recent=RECENT_BLOCKS, with_embeddings=True):
    rng = random.Random(SEED)
    sample = cap_sample(posts)
    last_week = max(week_index(p["created_at"]) for p in posts)
    n_blocks = (last_week + 1) // block_weeks          # complete blocks only; trailing partial block dropped
    change_block = week_index(DEPTH_CHANGE) // block_weeks
    by_term = defaultdict(list)
    for p in sample:
        b = week_index(p["created_at"]) // block_weeks
        if 0 <= b < n_blocks:
            by_term[p["term"]].append(p)

    def centroid_fn(block_posts):
        vecs = [p["embedding_vultr"] for p in block_posts if p.get("embedding_vultr")]
        if len(vecs) < min_n:
            return None, None
        units = []
        for v in vecs:
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            units.append([x / norm for x in v])
        dim = len(units[0])
        c = [sum(u[i] for u in units) / len(units) for i in range(dim)]
        cn = math.sqrt(sum(x * x for x in c)) or 1.0
        disp = statistics.mean(1 - sum(a * b for a, b in zip(u, c)) / cn for u in units)
        return c, disp

    terms = {}
    for term, tposts in sorted(by_term.items()):
        blocks = defaultdict(list)
        for p in tposts:
            blocks[week_index(p["created_at"]) // block_weeks].append(p)
        seen, rows, first_c = set(), [], None
        for b in range(n_blocks):
            bp = blocks.get(b, [])
            if len(bp) >= min_n:
                if first_c is None and with_embeddings:
                    first_c, _ = centroid_fn(bp)
                row = block_metrics(bp, seen, community_of, rng, centroid_fn if with_embeddings else None, first_c)
                row["block"] = b
                rows.append(row)
            seen.update(p.get("author_hash") for p in bp)
        # velocity on saturated weeks, uncapped by construction
        weeks = defaultdict(list)
        for p in posts:
            if p["term"] == term:
                weeks[week_index(p["created_at"])].append(p)
        vel = {}
        for wk, group in weeks.items():
            end = min(FIRST_WEEK + timedelta(weeks=wk + 1), max(utc(p["created_at"]) for p in posts))
            v = velocity(group, end)
            if v is not None:
                vel[wk] = v
        for row in rows:
            vs = [vel[w] for w in range(row["block"] * block_weeks, (row["block"] + 1) * block_weeks) if w in vel]
            row["velocity_pph"] = statistics.median(vs) if len(vs) >= 2 else None
        for row in rows:
            row["start"] = block_label(row["block"], block_weeks)
        terms[term] = {"press": press.get(term), "n_sample": len(tposts), "blocks": rows}

    # platform baseline for commerce: same block, all other terms pooled
    tot = defaultdict(lambda: [0, 0.0])
    for t, d in terms.items():
        if t in BASELINE_EXCLUDE:
            continue
        for r in d["blocks"]:
            tot[r["block"]][0] += r["n"]
            tot[r["block"]][1] += r["commerce"] * r["n"]
    for t, d in terms.items():
        own = 0 if t in BASELINE_EXCLUDE else 1
        for r in d["blocks"]:
            n_o, c_o = tot[r["block"]][0] - own * r["n"], tot[r["block"]][1] - own * r["commerce"] * r["n"]
            r["commerce_excess"] = r["commerce"] - c_o / n_o if n_o else None
    baseline = {block_label(b, block_weeks): v[1] / v[0] for b, v in sorted(tot.items())}

    # validation: trajectory recent-vs-early, rising vs fading; artifact checks
    names = list(METRICS)
    sat_all = {}
    for t, tp in by_term.items():
        wk_counts = Counter(week_index(p["created_at"]) for p in tp)
        sat_all[t] = sum(1 for c in wk_counts.values() if c >= CAP) / (n_blocks * block_weeks)
    validation = {}
    for name in names:
        traj = {t: trajectory([r.get(name) for r in d["blocks"]], recent) for t, d in terms.items()}
        rising = [traj[t] for t, d in terms.items() if d["press"] == "rising"]
        fading = [traj[t] for t, d in terms.items() if d["press"] == "fading"]
        mw = mann_whitney(fading, rising)
        # artifact 1: step at the depth change, averaged over all terms (pre block vs post block)
        steps = []
        for d in terms.values():
            pre = [r.get(name) for r in d["blocks"] if r["block"] < change_block and r.get(name) is not None]
            post = [r.get(name) for r in d["blocks"] if r["block"] > change_block and r.get(name) is not None]
            if pre and post:
                steps.append(statistics.mean(post) - statistics.mean(pre))
        # artifact 2: correlation with saturation (share of weeks at cap) across terms, level not trend
        levels = {t: statistics.mean([r[name] for r in d["blocks"] if r.get(name) is not None])
                  for t, d in terms.items() if any(r.get(name) is not None for r in d["blocks"])}
        sat = {t: sat_all[t] for t in levels}
        level_sd = statistics.pstdev(levels.values()) if len(levels) > 1 else None
        validation[name] = {
            "traj_rising": dict(zip([t for t, d in terms.items() if d["press"] == "rising"], rising)),
            "traj_fading": dict(zip([t for t, d in terms.items() if d["press"] == "fading"], fading)),
            "fading_gt_rising": mw,
            "depth_step_median": statistics.median(steps) if steps else None,
            "depth_step_over_sd": (statistics.median(steps) / level_sd) if steps and level_sd else None,
            "spearman_level_vs_saturation": spearman([levels[t] for t in levels], [sat[t] for t in levels]),
        }
    return {"config": {"cap": CAP, "block_weeks": block_weeks, "min_n": min_n, "n_blocks": n_blocks,
                       "depth_change_block": change_block, "recent_blocks": RECENT_BLOCKS,
                       "posts_total": len(posts), "posts_cap_sample": len(sample)},
            "commerce_baseline_all_terms": baseline,
            "metrics": METRICS, "terms": terms, "validation": validation}


def commerce_checks(posts, terms=("quarter zip", "poplin shirt", "straight leg jeans", "capri pants")):
    """Artifact checks for the commerce finding: weekday mix, page depth, and how many accounts write the posts."""
    out = {}
    wd = defaultdict(lambda: [0, 0])
    for p in posts:
        w = utc(p["created_at"]).strftime("%a")
        wd[w][0] += 1
        wd[w][1] += flag(p.get("text"), "commerce")
    out["weekday_commerce_share"] = {k: v[1] / v[0] for k, v in wd.items()}
    groups = defaultdict(list)
    for p in posts:
        groups[(p["term"], week_index(p["created_at"]))].append(p)
    depth = {"all": [0, 0, 0, 0], "quarter zip": [0, 0, 0, 0]}
    n_deep = 0
    for (term, _wk), group in groups.items():
        if len(group) <= 2 * CAP:
            continue
        n_deep += 1
        group.sort(key=lambda p: utc(p["created_at"]), reverse=True)
        top, rest = group[:CAP], group[CAP:]
        for key in ("all", term):
            if key in depth:
                d = depth[key]
                d[0] += len(top); d[1] += sum(flag(p.get("text"), "commerce") for p in top)
                d[2] += len(rest); d[3] += sum(flag(p.get("text"), "commerce") for p in rest)
    out["deep_weeks"] = n_deep
    out["depth_newest_vs_older"] = {k: {"newest": d[1] / d[0], "older": d[3] / d[2]} for k, d in depth.items() if d[0] and d[2]}
    sample = cap_sample(posts)
    acc = {}
    for t in terms:
        for label, lo, hi in (("first_quarter", 0, 13), ("last_quarter", 39, 52)):
            hits = [p for p in sample if p["term"] == t and lo <= week_index(p["created_at"]) < hi
                    and flag(p.get("text"), "commerce")]
            c = Counter(p.get("author_hash") for p in hits)
            acc[f"{t} {label}"] = {"posts": len(hits), "accounts": len(c),
                                   "top3_share": (sum(x for _, x in c.most_common(3)) / len(hits)) if hits else None}
    out["commerce_accounts"] = acc
    return out


def press_status():
    data = json.loads(TERMS_PATH.read_text())
    return {t["term"]: t["press_status"] for t in data["terms"] if t.get("press_status")}


def block_label(b, block_weeks=BLOCK_WEEKS):
    start = FIRST_WEEK + timedelta(weeks=b * block_weeks)
    return start.strftime("%Y-%m-%d")


def fmt(v, pct=False):
    if v is None:
        return "-"
    return f"{100 * v:.0f}%" if pct else f"{v:.2f}"


PCT = {"zero_engagement", "commerce", "commerce_excess", "deal", "secondhand", "backlash", "revival", "desire", "wearing", "conversation", "newcomer",
       "top_author_share"}


def print_report(res, focus):
    cfg = res["config"]
    print(f"blocks={cfg['n_blocks']} x {cfg['block_weeks']}w, cap sample {cfg['posts_cap_sample']} of "
          f"{cfg['posts_total']} posts, min n={cfg['min_n']}, depth change in block {cfg['depth_change_block']}")
    print("commerce baseline (all terms):", {k: round(v, 3) for k, v in res["commerce_baseline_all_terms"].items()})
    print_validation(res, "4-week blocks, last 3 vs earlier")
    print_validation(res["quarterly"], "13-week quarters, min n=15, last quarter vs earlier")
    print("\ncommerce checks:", json.dumps(res["commerce_checks"], indent=1, default=str))
    print_terms(res, focus)


def print_validation(res, title):
    print(f"\nVALIDATION [{title}] (trajectory = mean of last 3 blocks - mean of earlier blocks; AUC = P(fading traj > rising traj))")
    for name, v in res["validation"].items():
        mw = v["fading_gt_rising"] or {}
        print(f"  {name:20s} AUC={fmt(mw.get('auc'))} p={fmt(mw.get('p_perm'))} n={mw.get('n_b')}r/{mw.get('n_a')}f "
              f"depth_step={fmt(v['depth_step_median'])} step/sd={fmt(v['depth_step_over_sd'])} "
              f"rho_sat={fmt(v['spearman_level_vs_saturation'])}")
        print("     rising " + ", ".join(f"{t}={fmt(x)}" for t, x in v["traj_rising"].items())
              + " | fading " + ", ".join(f"{t}={fmt(x)}" for t, x in v["traj_fading"].items()))


def print_terms(res, focus):
    for term in focus:
        d = res["terms"].get(term)
        if not d:
            continue
        print(f"\n{term} (press={d['press']}, cap-sample n={d['n_sample']})")
        cols = ["n", "likes_median", "likes_mean", "zero_engagement", "commerce", "commerce_excess", "deal", "secondhand", "backlash", "revival", "desire",
                "wearing", "conversation", "newcomer", "communities_per20", "drift_from_first", "dispersion",
                "velocity_pph"]
        print("  block       " + " ".join(f"{c[:8]:>8s}" for c in cols))
        for r in d["blocks"]:
            print(f"  {r['start']}  " + " ".join(
                f"{(str(r.get(c)) if c == 'n' else fmt(r.get(c), c in PCT)):>8s}" for c in cols))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--terms", default="")
    ap.add_argument("--samples", default="", help="flag name: print 40 random matches for a local precision check")
    args = ap.parse_args()
    if os.environ.get("RADAR_INSIGHTS_CACHE"):  # local pickle for fast iteration; same fields as load()
        import pickle
        with open(os.environ["RADAR_INSIGHTS_CACHE"], "rb") as fh:
            d = pickle.load(fh)
        posts, community_of = d["posts"], {c["author_hash"]: c["community_id"] for c in d["comms"]}
    else:
        posts, community_of = load()
    if args.samples:
        rng = random.Random(SEED)
        hits = [p for p in cap_sample(posts) if flag(p.get("text"), args.samples)]
        for p in rng.sample(hits, min(40, len(hits))):
            print(f"[{p['term']}] {(p.get('text') or '')[:160]!r}")
        print(f"\n{len(hits)} matches")
        return
    res = analyse(posts, community_of, press_status())
    # coarser pass so more of the 10 press terms have enough posts: 13-week quarters, last quarter vs earlier
    res["quarterly"] = analyse(posts, community_of, press_status(), block_weeks=13, min_n=15, recent=1,
                               with_embeddings=False)
    res["commerce_checks"] = commerce_checks(posts)
    if args.json:
        json.dump(res, sys.stdout, indent=1, default=str)
        return
    focus = [t.strip() for t in args.terms.split(",") if t.strip()] or sorted(res["terms"])
    print_report(res, focus)


if __name__ == "__main__":
    main()
