"""Measure the Part 5 verdict engine against a labeled CSV, read-only, for the demo user.

Usage (from the project root):
  python scripts/eval_verdicts.py [--csv path] [--limit N] [--row N_or_name] [--email EMAIL] [--dry-run]

CSV columns: name, price, store, image_url, description, expected_verdict, notes
- expected_verdict is have_this_look, micro_trend or good_buy. A row whose expected_verdict or name
  starts with "TODO" is skipped and counted. Fill it in to score it.
- --row takes a 1-based data row number (as listed in the output) or a case-insensitive part of the name.
- --limit runs only the first N scorable rows (after --row).
- --dry-run reads the CSV, the user, the closet and the trends, and lists the rows. It never calls the engine.

Read-only: the engine runs with save=False, so no verdict documents are written. The script counts the
user's verdict and closet documents before and after the run and exits 3 if either count changed.
Exit codes: 0 finished (disagreements are reported, not failures), 1 the engine raised on a row,
2 bad input (CSV, user), 3 the database changed during the run.
Needs MONGODB_URI, GEMINI_API_KEY and ANTHROPIC_API_KEY in .env (the engine calls Gemini and Claude).
"""
import argparse
import csv
import inspect
import math
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.models import Item  # noqa: E402

DEFAULT_CSV = ROOT / "eval" / "verdicts_eval.csv"
DEFAULT_EMAIL = "demo@secondlook.test"
VERDICTS = ("have_this_look", "micro_trend", "good_buy")
TODO_PREFIX = "TODO"


# ENGINE ENTRY POINT: confirm name when feat/verdict-engine merges
def run_engine(user: dict, item: Item, description: str | None) -> tuple[object, dict]:
    """Run one verdict without saving it. Returns (response, signals).

    Expected engine: backend.services.verdict.decide(user, item, save=False) -> (response, signals).
    signals keys read here: decided_by, override {code_said, claude_said}, latency_ms, zone, margin,
    t_c, scores, matches, allowed. Missing keys are shown as "-".
    """
    from backend.services.verdict import decide

    kwargs = {"save": False}
    if description and "description" in inspect.signature(decide).parameters:
        kwargs["description"] = description
    return decide(user, item, **kwargs)


# ---------- CSV ----------

class CsvError(ValueError):
    pass


def is_todo(row: dict) -> bool:
    return any((row.get(k) or "").strip().upper().startswith(TODO_PREFIX) for k in ("expected_verdict", "name"))


def parse_price(text: str, line: int) -> float | None:
    text = (text or "").strip().lstrip("$").replace(",", "")
    if not text:
        return None
    try:
        price = float(text)
    except ValueError:
        raise CsvError(f"line {line}: price '{text}' is not a number") from None
    if price < 0:
        raise CsvError(f"line {line}: price is negative")
    return price


def load_rows(path: Path) -> list[dict]:
    """Return every non-blank data row as {num, name, item fields, description, expected, notes, todo}."""
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        missing = {"name", "expected_verdict"} - set(reader.fieldnames or [])
        if missing:
            raise CsvError(f"{path}: missing column(s) {', '.join(sorted(missing))}")
        rows = []
        for num, raw in enumerate(reader, 1):
            line = num + 1  # the header is line 1
            if not any((v or "").strip() for v in raw.values() if isinstance(v, str)):
                continue
            row = {
                "num": num,
                "name": (raw.get("name") or "").strip(),
                "store": (raw.get("store") or "").strip() or None,
                "image_url": (raw.get("image_url") or "").strip() or None,
                "description": (raw.get("description") or "").strip() or None,
                "expected": (raw.get("expected_verdict") or "").strip(),
                "notes": (raw.get("notes") or "").strip(),
                "todo": is_todo(raw),
            }
            if row["todo"]:
                row["price"] = None
            else:
                if not row["name"]:
                    raise CsvError(f"line {line}: name is empty")
                if row["expected"] not in VERDICTS:
                    raise CsvError(f"line {line}: expected_verdict '{row['expected']}' is not one of "
                                   f"{', '.join(VERDICTS)} or TODO")
                row["price"] = parse_price(raw.get("price"), line)
            rows.append(row)
    return rows


def select_rows(rows: list[dict], row: str | None = None, limit: int | None = None) -> tuple[list[dict], list[dict]]:
    """Return (rows to run, TODO rows skipped) after --row and --limit."""
    if row:
        if row.isdigit():
            rows = [r for r in rows if r["num"] == int(row)]
        else:
            rows = [r for r in rows if row.lower() in r["name"].lower()]
    todo = [r for r in rows if r["todo"]]
    runnable = [r for r in rows if not r["todo"]]
    if limit is not None:
        runnable = runnable[:limit]
    return runnable, todo


# ---------- math and formatting ----------

def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile: the smallest value with at least p percent of values at or below it."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


def agreement(results: list[dict]) -> tuple[tuple[int, int], dict[str, tuple[int, int]]]:
    """Return ((hits, total) overall, {expected verdict: (hits, total)}) over rows that ran without an error."""
    scored = [r for r in results if r.get("error") is None]
    per = {}
    for v in VERDICTS:
        group = [r for r in scored if r["expected"] == v]
        if group:
            per[v] = (sum(r["got"] == v for r in group), len(group))
    return (sum(r["got"] == r["expected"] for r in scored), len(scored)), per


def format_override(name: str, override) -> str | None:
    """'row: code said X, Claude said Y' or None when there is no override."""
    if not override:
        return None
    if not isinstance(override, dict):
        override = as_dict(override)
    return (f"{name}: code said {override.get('code_said') or '-'}, "
            f"Claude said {override.get('claude_said') or '-'}")


def as_dict(obj) -> dict:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return dict(vars(obj))


def fmt(value, digits: int = 3) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def ratio(hits: int, total: int) -> str:
    return f"{hits}/{total} = {hits / total:.0%}" if total else "0/0"


# ---------- database (reads only) ----------

def db_counts(user_id) -> dict:
    from backend.db import closet_items, verdicts
    return {"verdicts": verdicts.count_documents({"user_id": user_id}),
            "closet_items": closet_items.count_documents({"user_id": user_id})}


def describe_context(user_id) -> None:
    from backend.db import closet_items, trends
    owned = closet_items.count_documents({"user_id": user_id, "status": {"$ne": "incoming"}})
    incoming = closet_items.count_documents({"user_id": user_id, "status": "incoming"})
    names = [t["name"] for t in trends.find({}, {"name": 1})]
    print(f"closet: {owned} owned, {incoming} incoming   trends: {len(names)}")
    placeholders = [n for n in names if n.upper().startswith(("PLACEHOLDER", "EXAMPLE", "TEST"))]
    if placeholders:
        print(f"  note: {len(placeholders)} placeholder or example trend(s) loaded. micro_trend results "
              "are not meaningful until the real seed trends replace them")


# ---------- run ----------

def run_row(engine, user: dict, row: dict) -> dict:
    item = Item(name=row["name"], image_url=row["image_url"], price=row["price"], store=row["store"])
    result = {"num": row["num"], "name": row["name"], "expected": row["expected"], "got": None,
              "error": None, "signals": {}, "latency_ms": None}
    start = time.perf_counter()
    try:
        response, signals = engine(user, item, row["description"])
    except Exception as e:  # noqa: BLE001 - report and continue, the exit code says a row crashed
        result["error"] = f"{type(e).__name__}: {e}"
        result["latency_ms"] = (time.perf_counter() - start) * 1000
        return result
    wall_ms = (time.perf_counter() - start) * 1000
    response, signals = as_dict(response), as_dict(signals)
    result.update(got=response.get("verdict"), reason=response.get("reason"), signals=signals,
                  latency_ms=signals.get("latency_ms") if signals.get("latency_ms") is not None else wall_ms)
    return result


def print_row(i: int, n: int, row: dict, res: dict) -> None:
    print(f"\n[{i}/{n}] row {row['num']} {row['name']}")
    if res["error"]:
        print(f"  ERROR {res['error']}")
        return
    s = res["signals"]
    flag = "ok  " if res["got"] == res["expected"] else "MISS"
    print(f"  {flag} got={res['got']} expected={res['expected']}  decided_by={s.get('decided_by') or '-'}"
          f"  latency={res['latency_ms']:.0f} ms")
    scores = s.get("scores")
    top = max(scores) if isinstance(scores, (list, tuple)) and scores else scores
    print(f"  zone={fmt(s.get('zone'))}  top_score={fmt(top)}  t_c={fmt(s.get('t_c'))}  "
          f"margin={fmt(s.get('margin'))}  allowed={fmt(s.get('allowed'))}")
    override = format_override(row["name"], s.get("override"))
    if override:
        print(f"  OVERRIDE {override}")
    if res.get("reason"):
        print(f"  reason: {res['reason']}")
    if res["got"] != res["expected"] and row["notes"]:
        print(f"  why expected: {row['notes']}")


def print_summary(results: list[dict], todo: list[dict]) -> None:
    (hits, total), per = agreement(results)
    errors = [r for r in results if r["error"]]
    print("\n=== Agreement ===")
    print(f"  overall        {ratio(hits, total)}")
    for v in VERDICTS:
        if v in per:
            print(f"  {v:<14} {ratio(*per[v])}")
    print(f"  TODO rows skipped: {len(todo)}" + (f" (rows {', '.join(str(r['num']) for r in todo)})" if todo else ""))
    if errors:
        print(f"  rows that raised: {len(errors)} (not scored)")

    print("\n=== Overrides (Claude overrode code inside its zone) ===")
    lines = [format_override(r["name"], r["signals"].get("override")) for r in results if not r["error"]]
    lines = [line for line in lines if line]
    print("\n".join(f"  {line}" for line in lines) if lines else "  none")

    print("\n=== decided_by ===")
    counts = Counter(r["signals"].get("decided_by") or "unknown" for r in results if not r["error"])
    print("\n".join(f"  {k:<9} {v}" for k, v in counts.most_common()) if counts else "  none")

    print("\n=== Latency (engine latency_ms, else wall time) ===")
    times = [r["latency_ms"] for r in results if not r["error"] and r["latency_ms"] is not None]
    if times:
        print(f"  p50 {percentile(times, 50):.0f} ms   p95 {percentile(times, 95):.0f} ms   n={len(times)}")
    else:
        print("  no rows")

    misses = [r for r in results if not r["error"] and r["got"] != r["expected"]]
    print("\n=== Disagreements ===")
    print("\n".join(f"  row {r['num']} {r['name']}: got={r['got']} expected={r['expected']}" for r in misses)
          if misses else "  none")
    if errors:
        print("\n=== Errors ===")
        print("\n".join(f"  row {r['num']} {r['name']}: {r['error']}" for r in errors))


def main(argv=None, engine=run_engine) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--row", help="1-based data row number, or part of the name")
    ap.add_argument("--email", default=DEFAULT_EMAIL)
    ap.add_argument("--dry-run", action="store_true", help="read inputs and list rows, do not call the engine")
    args = ap.parse_args(argv)

    try:
        rows = load_rows(args.csv)
    except (CsvError, OSError) as e:
        print(f"CSV error: {e}")
        return 2
    runnable, todo = select_rows(rows, args.row, args.limit)
    if not runnable and not todo:
        print("No rows selected")
        return 2

    from backend.db import users
    user = users.find_one({"email": args.email})
    if not user:
        print(f"User {args.email} not found")
        return 2
    before = db_counts(user["_id"])
    print(f"user: {args.email}   csv: {args.csv}   rows to run: {len(runnable)}   TODO skipped: {len(todo)}")
    describe_context(user["_id"])
    print(f"read-only check, before: verdicts={before['verdicts']} closet_items={before['closet_items']}")

    results = []
    if args.dry_run:
        for row in runnable:
            print(f"  row {row['num']:>2} {row['expected']:<14} {row['name']}")
    else:
        for i, row in enumerate(runnable, 1):
            res = run_row(engine, user, row)
            results.append(res)
            print_row(i, len(runnable), row, res)
        print_summary(results, todo)

    after = db_counts(user["_id"])
    print(f"\nread-only check, after:  verdicts={after['verdicts']} closet_items={after['closet_items']}")
    if after != before:
        print("FAIL: the user's document counts changed during the run. The engine must not write with "
              "save=False (or the user used the app meanwhile)")
        return 3
    return 1 if any(r["error"] for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
