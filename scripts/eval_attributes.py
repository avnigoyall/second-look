"""Measure Part 2 item understanding against a labeled CSV.

Usage (from the project root): python scripts/eval_attributes.py [path/to/file.csv] [--crops DIR]

CSV columns: source, name, expected_category, expected_color_family
- source is an image URL or a local image path (relative to the project root, or absolute).
- Rows whose name starts with "EXAMPLE:" are the starter rows. They are scored like any other row.
  Delete them once you add real rows.
Runs the same path as POST /attributes (analyze_target: target item only, name-first category check), calling
the service directly (no server or login needed). Needs GEMINI_API_KEY and GEMINI_MODEL in .env.
--crops DIR saves each row's target_crop as a JPEG, so you can check that it shows only the target item.
"""
import argparse
import base64
import csv
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.services.item_understanding import (  # noqa: E402
    ImageFetchError, analyze_target, attribute_string, extract_attributes, fetch_image, name_category,
    resize_image)

DEFAULT_CSV = ROOT / "eval" / "attributes_eval.csv"
TARGET = 0.90
EXAMPLE_PREFIX = "EXAMPLE:"  # marks starter rows. Stripped before the name goes to Gemini
SCORED = ("category", "color_family")
CONFLICT_CHECKS = ("fixed_by_crop", "fixed_by_text", "forced_from_name")


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
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("csv", nargs="?", default=str(DEFAULT_CSV))
    parser.add_argument("--crops", help="folder to save each row's target_crop JPEG")
    args = parser.parse_args()
    csv_path = Path(args.csv)
    crops_dir = Path(args.crops) if args.crops else None
    if crops_dir:
        crops_dir.mkdir(parents=True, exist_ok=True)
    with open(csv_path, newline="") as f:
        rows = [r for r in csv.DictReader(f) if (r.get("source") or "").strip()]
    if not rows:
        print(f"No rows in {csv_path}")
        return 1

    calls = 0

    def counted_extract(*a, **kw):
        nonlocal calls
        calls += 1
        return extract_attributes(*a, **kw)

    hits = {field: 0 for field in SCORED}
    misses, failed, checks = [], 0, Counter()
    for i, row in enumerate(rows, 1):
        source, name = row["source"].strip(), row["name"].strip()
        clean_name = name.removeprefix(EXAMPLE_PREFIX).strip()
        image, note = load_image(source)
        attrs = analyze_target(image, clean_name, extract=counted_extract)
        expected = {field: row[f"expected_{field}"].strip() for field in SCORED}
        print(f"\n[{i}/{len(rows)}] {name}  ({note})")
        print(f"  source: {source}")
        if attrs["status"] != "ok":
            failed += 1
        checks[attrs.get("category_check") or "failed"] += 1
        for field in SCORED:
            got, want = attrs[field], expected[field]
            ok = got == want
            hits[field] += ok
            print(f"  {field:<13} {'ok  ' if ok else 'MISS'} got={got} expected={want}")
            if not ok:
                misses.append((i, name, field, got, want))
        print(f"  name check    name says {name_category(clean_name)}, category_check={attrs.get('category_check')}")
        print(f"  target_box_2d={attrs.get('target_box_2d')}  other_garments_visible={attrs.get('other_garments_visible')}"
              f"  crop={'yes' if attrs.get('target_crop') else 'no'}")
        print(f"  status={attrs['status']}  fabric={attrs['fabric']} ({attrs['fabric_confidence']})"
              f"  cut={attrs['cut']}  style={attrs['style']}  is_apparel={attrs['is_apparel']}")
        print(f"  string: {attribute_string(attrs)}")
        if crops_dir and attrs.get("target_crop"):
            out = crops_dir / f"row{i:02d}.jpg"
            out.write_bytes(base64.b64decode(attrs["target_crop"].split(",", 1)[1]))
            print(f"  crop saved: {out}")

    n = len(rows)
    print("\n=== Accuracy ===")
    for field in SCORED:
        acc = hits[field] / n
        print(f"  {field:<13} {hits[field]}/{n} = {acc:.0%}  {'PASS' if acc >= TARGET else 'BELOW'} target {TARGET:.0%}")
    if failed:
        print(f"  {failed} row(s) returned attributes_failed (counted as misses)")
    conflicts = sum(checks[c] for c in CONFLICT_CHECKS)
    print("\n=== Category conflicts (name check vs Gemini) ===")
    print(f"  conflicts: {conflicts}  fixed by retry: {checks['fixed_by_crop'] + checks['fixed_by_text']}"
          f" (crop {checks['fixed_by_crop']}, text {checks['fixed_by_text']})"
          f"  forced from name: {checks['forced_from_name']}")
    print(f"  agreed: {checks['agreed']}  no name category: {checks['no_name_category']}  failed: {checks['failed']}")
    print(f"  Gemini reads: {calls} (retries inside one read not counted)")
    print("\n=== Misses ===")
    if not misses:
        print("  none")
    for i, name, field, got, want in misses:
        print(f"  row {i} {name}: {field} got={got} expected={want}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
