"""Measure Part 2 item understanding against a labeled CSV.

Usage (from the project root): python scripts/eval_attributes.py [path/to/file.csv]

CSV columns: source, name, expected_category, expected_color_family
- source is an image URL or a local image path (relative to the project root, or absolute).
- Rows whose name starts with "EXAMPLE:" are the starter rows. They are scored like any other row.
  Delete them once you add real rows.
Calls the item understanding service directly (no server or login needed). Needs GEMINI_API_KEY and
GEMINI_MODEL in .env.
"""
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.services.item_understanding import (  # noqa: E402
    ImageFetchError, attribute_string, extract_attributes, fetch_image, resize_image)

DEFAULT_CSV = ROOT / "eval" / "attributes_eval.csv"
TARGET = 0.90
EXAMPLE_PREFIX = "EXAMPLE:"  # marks starter rows. Stripped before the name goes to Gemini
SCORED = ("category", "color_family")


def load_image(source: str) -> tuple[bytes | None, str]:
    """Return (jpeg bytes or None, note). A failed load is reported, then the row runs text only."""
    try:
        if source.lower().startswith(("http://", "https://")):
            return fetch_image(source), "image"
        path = Path(source).expanduser()
        path = path if path.is_absolute() else ROOT / path
        return resize_image(path.read_bytes()), "image"
    except (ImageFetchError, OSError, ValueError) as e:
        return None, f"TEXT ONLY, image failed: {e}"


def main():
    csv_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CSV
    with open(csv_path, newline="") as f:
        rows = [r for r in csv.DictReader(f) if (r.get("source") or "").strip()]
    if not rows:
        print(f"No rows in {csv_path}")
        return 1

    hits = {field: 0 for field in SCORED}
    misses, failed = [], 0
    for i, row in enumerate(rows, 1):
        source, name = row["source"].strip(), row["name"].strip()
        image, note = load_image(source)
        attrs = extract_attributes(image, name.removeprefix(EXAMPLE_PREFIX).strip())
        expected = {field: row[f"expected_{field}"].strip() for field in SCORED}
        print(f"\n[{i}/{len(rows)}] {name}  ({note})")
        print(f"  source: {source}")
        if attrs["status"] != "ok":
            failed += 1
        for field in SCORED:
            got, want = attrs[field], expected[field]
            ok = got == want
            hits[field] += ok
            print(f"  {field:<13} {'ok  ' if ok else 'MISS'} got={got} expected={want}")
            if not ok:
                misses.append((i, name, field, got, want))
        print(f"  status={attrs['status']}  fabric={attrs['fabric']} ({attrs['fabric_confidence']})"
              f"  cut={attrs['cut']}  style={attrs['style']}  is_apparel={attrs['is_apparel']}")
        print(f"  string: {attribute_string(attrs)}")

    n = len(rows)
    print("\n=== Accuracy ===")
    for field in SCORED:
        acc = hits[field] / n
        print(f"  {field:<13} {hits[field]}/{n} = {acc:.0%}  {'PASS' if acc >= TARGET else 'BELOW'} target {TARGET:.0%}")
    if failed:
        print(f"  {failed} row(s) returned attributes_failed (counted as misses)")
    print("\n=== Misses ===")
    if not misses:
        print("  none")
    for i, name, field, got, want in misses:
        print(f"  row {i} {name}: {field} got={got} expected={want}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
