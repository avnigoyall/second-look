"""Collect, scan and evaluate the photo-only material estimate (backend/services/materials_photo.py).

  collect  Read public Shopify product JSON (/products.json) from retailers whose robots.txt allows it, keep
           products with a photo and a stated composition, and write a seeded stratified sample to a manifest.
  scan     For each manifest item: fetch the first product photo, run FashionCLIP on the photo only
           (composition hidden), upsert to item_materials before the next item. Resumes by default.
  report   Read stored item_materials documents (no rescans) and write a markdown report.

Rules: one request per second, robots.txt checked with urllib.robotparser before any product fetch, a plain
identifying user agent, no workaround when a site blocks. Image bytes are cached in .social_runs/ (git-ignored)
and never stored in the database. No paid API is called.

  .venv/bin/python scripts/eval_materials_photo.py collect --plan cotton_denim=2,wool_knit=2,... --seed 7
  .venv/bin/python scripts/eval_materials_photo.py scan [--limit 10] [--rescan]
  .venv/bin/python scripts/eval_materials_photo.py report --out docs/eval_materials.md
"""
import argparse
import collections
import hashlib
import json
import random
import re
import statistics
import sys
import time
import urllib.robotparser
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.services import materials_photo as mp  # noqa: E402
from backend.services.item_materials_store import ItemMaterialsStore, build_doc, item_key  # noqa: E402

UA = "Mozilla/5.0 (compatible; research-bot)"
RUNS = ROOT / ".social_runs"
MANIFEST = RUNS / "materials_manifest.json"
IMAGE_DIR = RUNS / "materials_images"
SOURCE = "retailer_page"
# Not wearable: left out before sampling. Shoes, belts and bags stay in.
NON_WEARABLE = ("home", "gift", "ornament", "candle", "blanket", "towel", "bedding", "pillow", "card", "decor",
                "table", "napkin", "kitchen", "curtain", "sheet", "duvet", "rug")
GROUPS = list(mp.CONFIG["groups"])
DEFAULT_SITES = [
    "aimeleondore.com", "christydawn.com", "colorfulstandard.com", "everlane.com", "fahertybrand.com",
    "frankandoak.com", "gymshark.com", "kotn.com", "lunya.co", "magiclinen.com", "matethelabel.com",
    "ministryofsupply.com", "naadam.co", "organicbasics.com", "outerknown.com", "taylorstitch.com",
    "tentree.com", "thursdayboots.com", "toddsnyder.com",
]


class Polite:
    """One request per second across all hosts, robots.txt honored per host."""

    def __init__(self):
        import httpx
        self.client = httpx.Client(headers={"User-Agent": UA}, timeout=30, follow_redirects=True)
        self.robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._last = 0.0

    def _wait(self):
        delay = 1.0 - (time.monotonic() - self._last)
        if delay > 0:
            time.sleep(delay)
        self._last = time.monotonic()

    def allowed(self, base: str, url: str) -> bool:
        if base not in self.robots:
            self._wait()
            try:
                r = self.client.get(base + "/robots.txt")
                rp = None
                if r.status_code == 200:
                    rp = urllib.robotparser.RobotFileParser()
                    rp.parse(r.text.splitlines())
            except Exception:
                rp = None
            self.robots[base] = rp
        rp = self.robots[base]
        return bool(rp and rp.can_fetch(UA, url))

    def get(self, url: str):
        self._wait()
        return self.client.get(url)


def _first_image(product: dict) -> str | None:
    imgs = product.get("images") or []
    return imgs[0].get("src") if imgs else None


def _title_key(item: dict) -> tuple[str, str]:
    """Retailer plus the product name before any ' - color' or ' | color' suffix, lowercased."""
    name = re.split(r"\s[-|]\s", item.get("title") or "")[0]
    return item["retailer"], re.sub(r"\s+", " ", name).strip().lower()


def collect(args) -> None:
    plan = {k: int(v) for k, v in (p.split("=") for p in args.plan.split(","))}
    unknown = set(plan) - set(GROUPS)
    if unknown:
        sys.exit(f"unknown groups in plan: {sorted(unknown)}")
    existing = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else []
    have = {it["url"] for it in existing}
    have_titles = {_title_key(it) for it in existing}  # colorways of one product count once
    have_counts = collections.Counter(it["stated_group"] for it in existing)
    web = Polite()
    pool: dict[str, list[dict]] = collections.defaultdict(list)
    seen_titles = set(have_titles)
    blocked = {}
    for site in args.sites:
        base = f"https://{site}"
        url = base + "/products.json?limit=250"
        if not web.allowed(base, url):
            blocked[site] = "robots.txt unreadable or disallows /products.json"
            continue
        try:
            r = web.get(url)
        except Exception as exc:
            blocked[site] = f"{type(exc).__name__}: {exc}"
            continue
        if r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
            blocked[site] = f"HTTP {r.status_code}"
            continue
        for p in r.json().get("products", []):
            img, comp = _first_image(p), p.get("body_html")
            group = mp.stated_group(comp)
            page = f"{base}/products/{p.get('handle')}"
            ptype = (p.get("product_type") or "").lower()
            if not img or group in (None, "ambiguous") or page in have or any(w in ptype for w in NON_WEARABLE):
                continue
            item = {"retailer": site, "url": page, "title": p.get("title"), "product_type": p.get("product_type"),
                    "image_url": img, "stated_composition": comp, "stated_group": group}
            if _title_key(item) in seen_titles:
                continue
            seen_titles.add(_title_key(item))
            pool[group].append(item)
    rng = random.Random(args.seed)
    added = []
    for group, want in plan.items():
        need = max(0, want - have_counts[group])
        candidates = sorted(pool[group], key=lambda it: it["url"])
        rng.shuffle(candidates)
        # Round-robin across retailers so one store does not fill a group.
        by_site = collections.defaultdict(list)
        for it in candidates:
            by_site[it["retailer"]].append(it)
        order = sorted(by_site, key=lambda s: rng.random())
        picked = []
        while len(picked) < need and any(by_site.values()):
            for s in order:
                if by_site[s] and len(picked) < need:
                    picked.append(by_site[s].pop(0))
        if len(picked) < need:
            print(f"  {group}: only {len(picked)} of {need} available")
        added += picked
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(existing + added, indent=1))
    print(f"pool per group: { {g: len(v) for g, v in pool.items()} }")
    print(f"blocked: {blocked or 'none'}")
    print(f"manifest: {len(existing)} existing + {len(added)} added -> {MANIFEST}")


def _fetch_image(web: Polite, url: str) -> bytes:
    cache = IMAGE_DIR / (hashlib.sha256(url.encode()).hexdigest() + ".img")
    if cache.exists():
        return cache.read_bytes()
    sized = url + ("&" if "?" in url else "?") + "width=640"  # Shopify CDN resize; the model uses 224 px
    r = web.get(sized)
    r.raise_for_status()
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(r.content)
    return r.content


def scan(args) -> None:
    items = json.loads(MANIFEST.read_text())
    if args.limit:
        items = items[:args.limit]
    store = ItemMaterialsStore()
    synced = store.sync_fallback()
    run_id = args.run_id or f"materials-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    lh, th = mp.labels_config_hash(), mp.thresholds_hash()
    web = Polite()
    print(f"run_id {run_id}  fallback synced {synced}  items {len(items)}")
    print(f"{'#':>2} {'retailer':20} {'stated':16} {'estimate':16} {'score':>6} {'margin':>6} {'conf':6} {'ms':>6}  status")
    counts = collections.Counter()
    for i, it in enumerate(items, 1):
        key = item_key(it["url"])
        if store.should_skip(key, mp.MODEL_ID, lh, th, args.rescan):
            counts["skipped"] += 1
            print(f"{i:>2} {it['retailer']:20} {it['stated_group']:16} (stored, skipped)")
            continue
        estimate, status, extra = None, "ok", {"retailer": it["retailer"], "title": it["title"],
                                              "product_type": it["product_type"], "image_url": it["image_url"]}
        try:
            img = _fetch_image(web, it["image_url"])
        except Exception as exc:
            status, extra["error"] = "image_fetch_failed", f"{type(exc).__name__}: {exc}"
        if status == "ok":
            # Composition hidden: the estimator sees only the image bytes.
            estimate = mp.estimate_image(img)
            if estimate["status"] != "ok":
                status = "model_unavailable" if estimate["status"] == "model_unavailable" else "image_fetch_failed"
                extra["error"] = estimate.get("error")
            else:
                extra.update(latency_ms=estimate["latency_ms"], probs=estimate["probs"])
        doc = build_doc(key=key, source=SOURCE, source_url=it["url"], stated_composition=it["stated_composition"],
                        stated_group=it["stated_group"], estimate=estimate, model_id=mp.MODEL_ID, labels_hash=lh,
                        thresholds_hash=th, run_id=run_id, status=status, hand_labeled=False,
                        composition_hidden_for_eval=True, extra=extra)
        where = store.upsert(doc)
        counts[status] += 1
        e = doc["estimate"]
        print(f"{i:>2} {it['retailer']:20} {it['stated_group']:16} {str(e['group']):16} {_f(e['score']):>6} "
              f"{_f(e['margin']):>6} {str(e['confidence']):6} {_f(extra.get('latency_ms'), 1):>6}  {status}"
              f"{'' if where == 'db' else ' (saved to jsonl fallback)'}")
    print(dict(counts))
    if model := mp.model_status():
        print(f"model: device {model['device']}, load {_f(model['load_seconds'], 2)} s")


def _f(x, nd=3):
    return "-" if x is None else f"{x:.{nd}f}"


def metrics(docs: list[dict]) -> dict:
    ok = [d for d in docs if d["status"] == "ok" and d.get("stated_group") in GROUPS]
    n = len(ok)
    est = [d["estimate"]["group"] for d in ok]
    gold = [d["stated_group"] for d in ok]
    unknown = sum(g == "unknown" for g in est)
    correct = sum(e == g for e, g in zip(est, gold))
    answered = n - unknown
    counts = collections.Counter(gold)
    majority, majority_n = counts.most_common(1)[0] if counts else (None, 0)
    lat = [d["latency_ms"] for d in ok if d.get("latency_ms") is not None]
    top1 = sum(max(d["probs"], key=d["probs"].get) == d["stated_group"] for d in ok if d.get("probs"))
    return {
        "n": n, "correct": correct, "accuracy": correct / n if n else None,
        "unknown": unknown, "unknown_rate": unknown / n if n else None,
        "answered": answered, "accuracy_when_answered": (correct / answered) if answered else None,
        "top1_ignoring_thresholds": top1, "top1_accuracy": top1 / n if n else None,
        "majority_class": majority, "majority_baseline": majority_n / n if n else None,
        "per_class": dict(counts), "latency_median_ms": statistics.median(lat) if lat else None,
        "latency_mean_ms": statistics.mean(lat) if lat else None,
        "confusion": collections.Counter(zip(gold, est)),
        "by_confidence": {c: (sum(d["estimate"]["group"] == d["stated_group"] for d in ok
                                  if d["estimate"]["confidence"] == c),
                              sum(d["estimate"]["confidence"] == c for d in ok)) for c in ("medium", "low")},
        "not_ok": [d for d in docs if d["status"] != "ok"],
    }


def _pct(x):
    return "-" if x is None else f"{100 * x:.1f}%"


def _cell(text) -> str:
    return " ".join(str(text or "").split()).replace("|", "\\|")


def item_table(docs: list[dict]) -> str:
    rows = ["| # | Retailer | Product | Stated composition (main fabric) | Stated group | Estimate | Score | Margin | Confidence | Top-1 ignoring thresholds | Correct |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    from backend.services.composition import parse_composition
    for i, d in enumerate(docs, 1):
        fib = ", ".join(f"{f['pct']:g}% {f['fiber']}" for f in parse_composition(d.get("stated_composition")))
        e = d["estimate"]
        top1 = max(d["probs"], key=d["probs"].get) if d.get("probs") else "-"
        ok = "yes" if e["group"] == d["stated_group"] else ("unknown" if e["group"] == "unknown" else "no")
        rows.append(f"| {i} | {d.get('retailer')} | [{_cell(d.get('title'))}]({d['source_url']}) | {fib} | "
                    f"{d['stated_group']} | {e['group']} | {_f(e['score'])} | {_f(e['margin'])} | "
                    f"{e['confidence'] or '-'} | {top1} | {ok} |")
    return "\n".join(rows)


def summary_lines(m: dict) -> list[str]:
    return [
        f"- n = {m['n']} (status ok, unambiguous stated group)",
        f"- Accuracy (unknown counts as wrong): {m['correct']}/{m['n']} = {_pct(m['accuracy'])}",
        f"- Unknown rate: {m['unknown']}/{m['n']} = {_pct(m['unknown_rate'])}",
        f"- Accuracy on answered items: {m['correct']}/{m['answered']} = {_pct(m['accuracy_when_answered'])}",
        f"- Top-1 group ignoring thresholds: {m['top1_ignoring_thresholds']}/{m['n']} = {_pct(m['top1_accuracy'])}",
        f"- Majority-class baseline (always '{m['majority_class']}'): {_pct(m['majority_baseline'])}",
        f"- Per-image latency, warm: median {_f(m['latency_median_ms'], 1)} ms, mean {_f(m['latency_mean_ms'], 1)} ms",
        f"- Correct when confidence is medium: {m['by_confidence']['medium'][0]}/{m['by_confidence']['medium'][1]}; "
        f"when low: {m['by_confidence']['low'][0]}/{m['by_confidence']['low'][1]}",
        f"- Per-class counts: {m['per_class']}",
    ]


def confusion_table(m: dict) -> str:
    cols = GROUPS + ["unknown"]
    rows = ["| Stated \\ Estimate | " + " | ".join(cols) + " |", "|---" * (len(cols) + 1) + "|"]
    for g in GROUPS:
        rows.append(f"| {g} | " + " | ".join(str(m["confusion"].get((g, c), 0)) for c in cols) + " |")
    return "\n".join(rows)


def report(args) -> None:
    store = ItemMaterialsStore()
    store.sync_fallback()
    q = {"source": SOURCE, "model_id": mp.MODEL_ID, "labels_config_hash": mp.labels_config_hash(),
         "thresholds_hash": mp.thresholds_hash()}
    docs = sorted(store.all_docs(q), key=lambda d: d["scanned_at"])
    m = metrics(docs)
    lines = summary_lines(m)
    print("\n".join(lines))
    print(confusion_table(m))
    if not args.out:
        return
    beats = m["accuracy"] is not None and m["majority_baseline"] is not None and m["accuracy"] > m["majority_baseline"]
    body = [
        "# Photo material estimate: evaluation",
        "",
        f"Generated by `scripts/eval_materials_photo.py report` from stored `item_materials` documents (no rescans). "
        f"Model `{mp.MODEL_ID}`, labels hash `{mp.labels_config_hash()[:12]}`, thresholds hash "
        f"`{mp.thresholds_hash()[:12]}` (thresholds are PROVISIONAL placeholders, not tuned).",
        "",
        "Labels come from each product page's stated composition, mapped to a group by the majority-fiber rule in "
        "`config/materials_labels.json`. The estimator saw only the first product photo. The sample is a seeded, "
        "stratified random draw across Shopify retailers whose robots.txt allows `/products.json`.",
        "",
        "## Results",
        "",
        *lines,
        "",
        f"**Accuracy {'is' if beats else 'is not'} above the majority-class baseline of this sample "
        f"({_pct(m['majority_baseline'])}).**"
        + (" The sample is stratified, so that baseline is low by design (see the limits below)." if beats
           else " MATERIALS_PHOTO_ENABLED stays off in the demo config."),
        "",
        "## Confusion table",
        "",
        confusion_table(m),
        "",
        "## Items",
        "",
        item_table([d for d in docs if d["status"] == "ok"]),
        "",
        "## What this cannot show",
        "",
        f"n = {m['n']} is small: one or two items flip a percentage by several points, so this is not a general "
        "accuracy claim. The sample is stratified (near equal per group), so the majority-class baseline here is "
        "much lower than it would be on a real shopping mix, where cotton dominates. The products come from a "
        "small set of mostly US direct-to-consumer brands on Shopify, with their own photo styles. The groups are "
        "coarse and the labels are fiber-based, so an acrylic sweater counts as synthetic even when it looks like wool.",
    ]
    if m["not_ok"]:
        body += ["", f"Documents not counted (status not ok): {len(m['not_ok'])}"]
    Path(args.out).write_text("\n".join(body) + "\n")
    print(f"wrote {args.out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--plan", required=True, help="group=count,... (totals including items already in the manifest)")
    c.add_argument("--seed", type=int, default=7)
    c.add_argument("--sites", nargs="*", default=DEFAULT_SITES)
    s = sub.add_parser("scan")
    s.add_argument("--limit", type=int)
    s.add_argument("--rescan", action="store_true")
    s.add_argument("--run-id")
    r = sub.add_parser("report")
    r.add_argument("--out")
    args = ap.parse_args()
    {"collect": collect, "scan": scan, "report": report}[args.cmd](args)


if __name__ == "__main__":
    main()
