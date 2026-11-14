"""Print the cosine similarity of description pairs, to choose the thresholds in backend/config/thresholds.json.

Run from the project root:  python scripts/tune_matching.py [path/to/pairs.csv]
CSV columns: description_a, description_b, same_look (yes or no).
Descriptions are embedded exactly as written. For the closest match to production, write them in the
attribute_string format: "{color_family} {cut} {fabric} {category}, {style} style. {description}".
Needs GEMINI_API_KEY in .env. No database or server needed.
"""
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.services.closet import thresholds  # noqa: E402
from backend.services.embeddings import embed_many  # noqa: E402

DEFAULT_CSV = ROOT / "eval" / "match_pairs.csv"


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CSV
    rows = [r for r in csv.DictReader(path.open()) if r.get("description_a", "").strip()]
    if not rows:
        sys.exit(f"No rows in {path}")
    texts = list(dict.fromkeys(t for r in rows for t in (r["description_a"].strip(), r["description_b"].strip())))
    vectors = dict(zip(texts, embed_many(texts)))
    limit = thresholds()["look_similarity"]

    scored = []
    for r in rows:
        a, b = vectors[r["description_a"].strip()], vectors[r["description_b"].strip()]
        scored.append((sum(x * y for x, y in zip(a, b)), r["same_look"].strip().lower() == "yes", r))
    scored.sort(key=lambda s: -s[0])

    print(f"{'cosine':>7}  same  above {limit:.2f}  pair")
    for score, same, r in scored:
        flag = "ok " if (score >= limit) == same else "MISS"
        print(f"{score:7.3f}  {'yes' if same else 'no ':3}   {flag}        {r['description_a']}  <>  {r['description_b']}")

    yes = [s for s, same, _ in scored if same]
    no = [s for s, same, _ in scored if not same]
    print()
    if yes:
        print(f"same_look yes: min {min(yes):.3f}  mean {sum(yes) / len(yes):.3f}")
    if no:
        print(f"same_look no:  max {max(no):.3f}  mean {sum(no) / len(no):.3f}")
    if yes and no:
        best = max(((t, sum((s >= t) == same for s, same, _ in scored)) for t, _, _ in scored), key=lambda x: x[1])
        print(f"best single cutoff on these pairs: {best[0]:.3f} ({best[1]} of {len(scored)} correct). "
              "Similarity alone ignores require_color_match.")


if __name__ == "__main__":
    main()
