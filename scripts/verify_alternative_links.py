"""Re-check every stored trend alternative link and report dead ones. Read-only by default.

Run from the project root:
    python scripts/verify_alternative_links.py            # report only, writes nothing
    python scripts/verify_alternative_links.py --write    # also update verified_at on product links

Checks are the same as at seed time (backend.alternatives.check_url): HTTP 200, same registrable
domain after redirects, not a home, search, or error page. Stored product links came from web search
results, so a 403 or 429 (bot protection) counts as "blocked, kept", not dead.
--write only sets verified_at (now for a verified link, null for a blocked one). It never removes or
replaces a link: dead links are listed so a person can re-run the seed script with --refresh-links.
Alternatives with no product_url (trends stored before links existed) are listed as "no link"; the
API gives those a store search link when it reads them. Exit code 1 when any link is dead.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.alternatives import check_client, check_url, now  # noqa: E402


def verify_trend(doc: dict, http) -> tuple[list[tuple[str, str, str]], dict]:
    """Check one stored trend's alternatives. Returns (rows of (status, label, detail), $set updates)."""
    rows, updates = [], {}
    for i, alt in enumerate(doc.get("alternatives") or []):
        url = alt.get("product_url")
        kind = alt.get("link_kind") or ("product" if url else None)
        label = f"{doc['name']} / {alt.get('type')}"
        if not url:
            rows.append(("no link", label, "API falls back to a store search link"))
            continue
        check = check_url(url, from_search_result=(kind == "product"), client=http)
        if kind == "search":
            # A search link is fine whenever the store's search page loads.
            rows.append(("search" if check.status == 200 else "dead", label, f"{url} (HTTP {check.status})"))
        elif check.ok and check.verified:
            rows.append(("ok", label, url))
            updates[f"alternatives.{i}.verified_at"] = now()
        elif check.ok:
            rows.append(("blocked", label, f"{url} ({check.reason})"))
            updates[f"alternatives.{i}.verified_at"] = None
        else:
            rows.append(("dead", label, f"{url} ({check.reason}, final {check.final_url})"))
    return rows, updates


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--write", action="store_true", help="update verified_at on product links (default: read-only)")
    args = parser.parse_args()

    from backend.db import trends

    counts = {"ok": 0, "blocked": 0, "dead": 0, "search": 0, "no link": 0}
    dead = []
    with check_client() as http:
        for doc in trends.find({}, {"name": 1, "alternatives": 1}).sort("name", 1):
            rows, updates = verify_trend(doc, http)
            for status, label, detail in rows:
                counts[status] += 1
                print(f"{status.upper() if status == 'dead' else status:<8} {label}: {detail}")
                if status == "dead":
                    dead.append(f"{label}: {detail}")
            if args.write and updates:
                trends.update_one({"_id": doc["_id"]}, {"$set": updates})

    print(f"\n{sum(counts.values())} alternatives: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
    if dead:
        print("Dead links (for seed trends, re-run python scripts/seed_trends.py --refresh-links):")
        for line in dead:
            print(f"  {line}")
    print("verified_at updated." if args.write else "Read-only: nothing written.")
    raise SystemExit(1 if dead else 0)


if __name__ == "__main__":
    main()
