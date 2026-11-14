"""Load the curated seed trends into the trends collection. Safe to run any number of times.

Run from the project root:  python scripts/seed_trends.py [path/to/trends_seed.json]

- Entries with "_placeholder": true are skipped. Keys starting with "_" are ignored.
- Every entry is checked first. If any entry is invalid, nothing is written.
- Upsert by name (case-insensitive). Re-running with an edited file updates the trend.
- origin is always "seed".
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pydantic import ValidationError  # noqa: E402

from backend.db import ensure_indexes, trends  # noqa: E402
from backend.trends import NAME_COLLATION, TrendDoc, name_key  # noqa: E402

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


def upsert(doc: TrendDoc) -> bool:
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
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_FILE
    docs, errors, skipped = validate(load_entries(path))
    if errors:
        print(f"Not loaded. Fix these in {path}:")
        for e in errors:
            print(f"  {e}")
        raise SystemExit(1)

    ensure_indexes()
    removed = trends.delete_many({"origin": "seed", "name": {"$regex": f"^{LEGACY_PLACEHOLDER_PREFIX}"}}).deleted_count
    if removed:
        print(f"removed {removed} Part 0 placeholder trends")
    added = sum(upsert(d) for d in docs)
    print(f"trends: {added} added, {len(docs) - added} updated, {skipped} placeholder entries skipped")
    print(f"trends collection now holds {trends.count_documents({})} trends "
          f"({trends.count_documents({'origin': 'seed'})} seed, {trends.count_documents({'origin': 'agent'})} agent)")


if __name__ == "__main__":
    main()
