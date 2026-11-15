"""Savings summary: money kept and lifecycle CO2e avoided by skipped items.

Skipped means the shopper pressed "Skip it" on a verdict (verdicts.action == "skipped"), the same rule as
money_kept in /dashboard. CO2e uses config/impact.json, which only holds sourced per-category figures.
"""
import json
from pathlib import Path

from backend.db import verdicts

IMPACT = json.loads((Path(__file__).parent / "config" / "impact.json").read_text())


def impact_summary(user_id, user: dict | None = None) -> dict:
    """user given (GET /impact): Nessie users get money_kept from their projected Savings balance, so the popup
    matches the bank; everyone else gets the local sum. Skips bought later (superseded) never count."""
    per_category = IMPACT["per_category"]
    money_kept, skipped, counted, co2e = 0.0, 0, 0, 0.0
    used = {}
    for doc in verdicts.find({"user_id": user_id, "action": "skipped", "superseded_by": None},
                          {"item.price": 1, "attributes.category": 1}):
        skipped += 1
        money_kept += (doc.get("item") or {}).get("price") or 0
        category = (doc.get("attributes") or {}).get("category")
        figure = per_category.get(category)
        if figure:
            counted += 1
            co2e += figure["kg_co2e"]
            used[category] = figure
    money_source = "local"
    if user is not None:
        from backend.services.goal import saved_cents  # lazy: goal imports money, money imports nessie
        cents, money_source = saved_cents(user)
        if money_source == "nessie":
            money_kept = cents / 100
    return {
        "money_kept": round(money_kept, 2),
        "money_source": money_source,
        "items_skipped": skipped,
        "co2e_kg": round(co2e, 1),
        "co2e_items_counted": counted,
        "co2e_sources": [{"category": c, "kg_co2e": f["kg_co2e"], "source": f["source"], "url": f["url"]}
                         for c, f in sorted(used.items())],
    }
