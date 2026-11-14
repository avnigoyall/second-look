"""Load the curated seed trends into the trends collection. Safe to run any number of times.

Run from the project root:
    python scripts/seed_trends.py [path/to/trends_seed.json]       # validate, find links, write
    python scripts/seed_trends.py --dry-run                        # validate and find links, write nothing
    python scripts/seed_trends.py --no-links                       # skip the web search (search links only)

- Entries with "_placeholder": true are skipped. Keys starting with "_" are ignored.
- Every entry is checked first. If any entry is invalid, nothing is written.
- Upsert by name (case-insensitive). Re-running with an edited file updates the trend.
- origin is always "seed".
- Alternative links: each alternative without a product link gets one from Claude web search
  (backend/alternative_finder.py, about $0.01 to $0.03 per trend). A link already stored for the same
  alternative type is reused, so re-running does not search again (--refresh-links forces it).
  Anything left without a product link gets a store search link. Audit saved to .trend_runs/.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pydantic import ValidationError  # noqa: E402

from backend.trends import NAME_COLLATION, TrendDoc, name_key  # noqa: E402

RUNS_DIR = ROOT / ".trend_runs"

DEFAULT_FILE = ROOT / "seed" / "trends_seed.json"
# Part 0 seeded three fake trends. They would leak into the verdict prompt, so they are removed.
LEGACY_PLACEHOLDER_PREFIX = "PLACEHOLDER trend "


def load_entries(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    entries = data["trends"] if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise SystemExit(f"{path}: expected a list of trends, or an object with a 'trends' list")
    return entries


def validate(entries: list[dict]) -> tuple[list[TrendDoc], list[str], int]:
    docs, errors, skipped, seen = [], [], 0, {}
    for i, raw in enumerate(entries):
        if raw.get("_placeholder"):
            skipped += 1
            continue
        clean = {k: v for k, v in raw.items() if not k.startswith("_")}
        label = f"entry {i} ({clean.get('name', 'no name')})"
        try:
            doc = TrendDoc.model_validate({**clean, "origin": "seed"})
        except ValidationError as e:
            errors += [f"{label}: {'.'.join(map(str, err['loc'])) or 'trend'}: {err['msg']}" for err in e.errors()]
            continue
        key = name_key(doc.name)
        if key in seen:
            errors.append(f"{label}: same name as entry {seen[key]}")
            continue
        seen[key] = i
        for w in doc.vocabulary_warnings():
            print(f"note  {label}: {w}")
        docs.append(doc)
    return docs, errors, skipped


def add_links(docs: list[TrendDoc], find: bool, refresh: bool, stored: dict) -> list[TrendDoc]:
    """Give every alternative a link. stored: name_key -> stored trend document (for link reuse)."""
    from backend.alternative_finder import find_links, reuse_links, save_audit
    from backend.alternatives import with_search_fallback

    out, audits = [], []
    for doc in docs:
        data = doc.model_dump()
        old = stored.get(name_key(doc.name))
        if old and not refresh:
            data["alternatives"] = reuse_links(data["alternatives"], old.get("alternatives"))
        if find and data["alternatives"]:
            print(f"links for '{doc.name}':")
            data, audit = find_links(data)
            audits.append(audit)
        else:
            data["alternatives"] = [with_search_fallback(a, data.get("category")) for a in data["alternatives"]]
        out.append(TrendDoc.model_validate(data))
    if audits:
        print(f"link search audit saved to {save_audit(audits, RUNS_DIR).relative_to(ROOT)}")
    return out


def upsert(trends, doc: TrendDoc) -> bool:
    """Returns True if the trend was new."""
    fields = doc.model_dump(exclude={"first_seen", "search_suggestions_html"})
    update = {"$set": fields, "$unset": {"search_suggestions_html": ""}}
    if doc.first_seen:
        fields["first_seen"] = doc.first_seen
    else:
        update["$setOnInsert"] = {"first_seen": datetime.now(timezone.utc)}
    result = trends.update_one({"name": doc.name}, update, upsert=True, collation=NAME_COLLATION)
    return result.upserted_id is not None


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_FILE, help="seed file (default seed/trends_seed.json)")
    parser.add_argument("--dry-run", action="store_true", help="validate and find links, write nothing to the database")
    parser.add_argument("--no-links", action="store_true", help="no web search; alternatives get store search links")
    parser.add_argument("--refresh-links", action="store_true", help="search again even when a product link is stored")
    args = parser.parse_args()
    path = args.path
    docs, errors, skipped = validate(load_entries(path))
    if errors:
        print(f"Not loaded. Fix these in {path}:")
        for e in errors:
            print(f"  {e}")
        raise SystemExit(1)

    from backend.db import ensure_indexes, trends  # imported late so --help and validation work offline
    stored = {name_key(d["name"]): d for d in trends.find({}, {"name": 1, "alternatives": 1})}
    docs = add_links(docs, find=not args.no_links, refresh=args.refresh_links, stored=stored)
    if args.dry_run:
        print(f"dry run: {len(docs)} valid trends, {skipped} placeholder entries skipped. Nothing written.")
        for d in docs:
            for a in d.alternatives:
                print(f"  {d.name}: {a.type} -> [{a.link_kind}] {a.product_name or ''} {a.product_url}")
        return

    ensure_indexes()
    removed = trends.delete_many({"origin": "seed", "name": {"$regex": f"^{LEGACY_PLACEHOLDER_PREFIX}"}}).deleted_count
    if removed:
        print(f"removed {removed} Part 0 placeholder trends")
    added = sum(upsert(trends, d) for d in docs)
    print(f"trends: {added} added, {len(docs) - added} updated, {skipped} placeholder entries skipped")
    print(f"trends collection now holds {trends.count_documents({})} trends "
          f"({trends.count_documents({'origin': 'seed'})} seed, {trends.count_documents({'origin': 'agent'})} agent)")


if __name__ == "__main__":
    main()
