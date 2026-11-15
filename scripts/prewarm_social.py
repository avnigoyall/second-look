"""Prewarm and check the Social Radar lookups for the demo products.

Run from the project root:
    .venv/bin/python scripts/prewarm_social.py              # reads config/demo_products.json
    .venv/bin/python scripts/prewarm_social.py --products path/to/file.json

config/demo_products.json: a JSON list (or {"products": [...]}) of items shaped like the /analyze item, for
example {"name": "Barrel leg jeans", "category": "jeans", "color_family": "blue", "expected_term": "barrel jeans"}.
expected_term is optional. When the file does not exist, the script prints
"skipped: config/demo_products.json not found" and exits 0.

Per product the CLI runs the same lookup as the runtime (backend.services.social: embed_query, then Atlas
vector search, then term voting), without the runtime sub-second deadline, so the first slow embed cannot hide a
match. It prints the matched term, the latest Radar label (backend.services.radar_read) and whether stored
Bluesky sample numbers would reach the verdict prompt. Each lookup embeds one query with Vultr, so every call
first passes scripts/radar_spend.py check_budget("vultr"); the embed guard ledger records the tokens.

Caches are per process. The CLI warms the Vultr connection and Atlas, not a running server's memory caches.
To fill a server's caches, call warm(products) inside that server process (for example from
backend/warmup.py) with SOCIAL_RADAR_ENABLED=1. warm() uses social_context, so it does nothing when the flag is
off. This script never changes the flag.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

DEFAULT_PRODUCTS = ROOT / "config" / "demo_products.json"


def load_products(path: Path) -> list[dict] | None:
    """The product list, or None when the file does not exist. Raises ValueError on a malformed file."""
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    products = data.get("products") if isinstance(data, dict) else data
    if not isinstance(products, list) or not all(isinstance(p, dict) and p.get("name") for p in products):
        raise ValueError(f"{path.name} must be a list of objects with a name")
    return products


def warm(products: list[dict]) -> int:
    """Fill this process's social caches through the runtime path. Returns the number of matches."""
    from backend.services import social
    return sum(1 for product in products if social.social_context(product))


def check(product: dict, *, lookup=None, budget=None, label_for=None) -> dict:
    """One product: runtime lookup without the deadline, then the Radar label for the matched term."""
    from backend.services import social
    query = social.item_query(product)
    row = {"name": product.get("name"), "query": query, "expected_term": product.get("expected_term")}
    if not query:
        return {**row, "status": "no_query"}
    if budget is None:
        import radar_spend
        budget = lambda: radar_spend.check_budget("vultr")
    budget()
    try:
        context = (lookup or (lambda q: social._lookup(q, social.momentum_config())))(query)
    except Exception as exc:  # noqa: BLE001 - SDK messages can carry URLs; report the type only
        return {**row, "status": "lookup_failed", "error": type(exc).__name__}
    if not context:
        return {**row, "status": "no_match"}
    row.update(status="match", term=context.get("term"), similarity=round(float(context.get("similarity", 0)), 3),
               momentum=context.get("momentum"), confidence=context.get("confidence"))
    row.update((label_for or radar_label)(context.get("term")))
    if row["expected_term"]:
        row["expected_ok"] = row["term"] == row["expected_term"]
    return row


def radar_label(term: str) -> dict:
    """final_label and decision_source as the timeline endpoint shows them, plus the sample numbers if any."""
    from backend.db import db
    from backend.services import micro_trend, radar_read
    manual = micro_trend.manual_decision(term)
    if manual:
        return {"final_label": manual["final_label"], "decision_source": "manual", "sample_lifespan": None}
    doc = db["social_trends"].find_one({"term": term}, {"_id": 0, "embedding": 0})
    if not doc:
        return {"final_label": None, "decision_source": None, "sample_lifespan": None}
    metrics = micro_trend.compute_rule_metrics(doc)
    decision = radar_read.latest_decision(term, db)
    final = (decision or {}).get("final_label") or metrics["rule_label"]
    return {"final_label": final, "decision_source": (decision or {}).get("decision_source") or "rule",
            "sample_lifespan": radar_read.sample_lifespan(metrics, decision)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--products", type=Path, default=DEFAULT_PRODUCTS)
    args = parser.parse_args(argv)
    try:
        products = load_products(args.products)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if products is None:
        shown = args.products.relative_to(ROOT) if args.products.is_relative_to(ROOT) else args.products
        print(f"skipped: {shown} not found")
        return 0
    import radar_spend
    rows = []
    for product in products:
        try:
            row = check(product)
        except radar_spend.BudgetExceeded as exc:
            radar_spend.stop_cleanly(exc)
        rows.append(row)
        print(json.dumps(row, default=str))
    matched = sum(1 for r in rows if r["status"] == "match")
    wrong = [r["name"] for r in rows if r.get("expected_ok") is False]
    print(f"done: {len(rows)} products, {matched} matched, {len(wrong)} unexpected terms")
    return 1 if wrong else 0


if __name__ == "__main__":
    raise SystemExit(main())
