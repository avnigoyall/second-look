"""Offline tests for scripts/eval_verdicts.py with a fake engine and a fake database. No network.

Run from the project root:  python -m unittest discover tests
"""
import contextlib
import io
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.eval_verdicts import (DEFAULT_CSV, CsvError, agreement, format_override, load_rows, main,  # noqa: E402
                                   percentile, run_row, select_rows)

HEADER = "name,price,store,image_url,description,expected_verdict,notes\n"
CSV_TEXT = HEADER + (
    "Black tee,14.9,uniqlo,https://example.com/a.jpg,Black tee,have_this_look,owned black tee\n"
    "Navy sweater,$39.90,uniqlo,,Navy sweater,good_buy,closet gap\n"
    "Barrel jeans,39.9,uniqlo,,,TODO,needs seed trends\n"
    ",,,,,,\n"
    "TODO: trend item,,,,,TODO,user adds it\n"
    "Wide cargo pants,,uniqlo,,,good_buy,gray band\n"
)


def write_csv(text: str) -> Path:
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
        f.write(text)
    return Path(f.name)


class FakeEngine:
    """Answers from a {name: (verdict, signals)} table. A name mapped to an exception raises it."""

    def __init__(self, table):
        self.table, self.calls = table, []

    def __call__(self, user, item, description):
        self.calls.append(item)
        answer = self.table[item.name]
        if isinstance(answer, Exception):
            raise answer
        verdict, signals = answer
        return {"status": "ok", "verdict": verdict, "reason": "because"}, signals


class FakeCollection:
    def __init__(self, count=0, docs=()):
        self.count, self.docs = count, list(docs)

    def count_documents(self, query):
        return self.count

    def find(self, query, projection=None):
        return iter(self.docs)

    def find_one(self, query):
        return self.docs[0] if self.docs else None


class CsvTests(unittest.TestCase):
    def test_parses_rows_and_marks_todo(self):
        rows = load_rows(write_csv(CSV_TEXT))
        self.assertEqual([r["num"] for r in rows], [1, 2, 3, 5, 6])  # the blank line 4 is dropped
        self.assertEqual(rows[0]["price"], 14.9)
        self.assertEqual(rows[1]["price"], 39.9)  # "$39.90" parses
        self.assertIsNone(rows[1]["image_url"])
        self.assertIsNone(rows[4]["price"])
        self.assertEqual([r["todo"] for r in rows], [False, False, True, True, False])

    def test_bad_expected_verdict_is_an_error(self):
        with self.assertRaisesRegex(CsvError, "line 2"):
            load_rows(write_csv(HEADER + "Tee,10,uniqlo,,,maybe,\n"))

    def test_bad_price_is_an_error(self):
        with self.assertRaisesRegex(CsvError, "not a number"):
            load_rows(write_csv(HEADER + "Tee,ten,uniqlo,,,good_buy,\n"))

    def test_missing_column_is_an_error(self):
        with self.assertRaisesRegex(CsvError, "expected_verdict"):
            load_rows(write_csv("name,price\nTee,1\n"))

    def test_select_skips_todo_and_applies_row_and_limit(self):
        rows = load_rows(write_csv(CSV_TEXT))
        run, todo = select_rows(rows)
        self.assertEqual([r["num"] for r in run], [1, 2, 6])
        self.assertEqual([r["num"] for r in todo], [3, 5])
        run, todo = select_rows(rows, limit=1)
        self.assertEqual(([r["num"] for r in run], len(todo)), ([1], 2))
        run, todo = select_rows(rows, row="2")
        self.assertEqual(([r["num"] for r in run], todo), ([2], []))
        run, todo = select_rows(rows, row="barrel")
        self.assertEqual((run, [r["num"] for r in todo]), ([], [3]))

    def test_repo_csv_has_20_rows(self):
        rows = load_rows(DEFAULT_CSV)
        self.assertEqual(len(rows), 20)
        run, todo = select_rows(rows)
        self.assertEqual((len(run), len(todo)), (15, 5))
        for r in run:
            self.assertTrue(r["image_url"] and r["price"] is not None and r["notes"], r["name"])


class MathTests(unittest.TestCase):
    def test_percentile_nearest_rank(self):
        values = list(range(1, 21))  # 1..20
        self.assertEqual(percentile(values, 50), 10)
        self.assertEqual(percentile(values, 95), 19)
        self.assertEqual(percentile([7.0], 95), 7.0)
        self.assertEqual(percentile([3, 1, 2], 50), 2)
        self.assertIsNone(percentile([], 50))

    def test_agreement_overall_and_per_verdict(self):
        results = [
            {"expected": "have_this_look", "got": "have_this_look", "error": None},
            {"expected": "have_this_look", "got": "good_buy", "error": None},
            {"expected": "good_buy", "got": "good_buy", "error": None},
            {"expected": "good_buy", "got": None, "error": "RuntimeError: boom"},
        ]
        overall, per = agreement(results)
        self.assertEqual(overall, (2, 3))
        self.assertEqual(per, {"have_this_look": (1, 2), "good_buy": (1, 1)})

    def test_format_override(self):
        self.assertIsNone(format_override("Tee", None))
        self.assertIsNone(format_override("Tee", {}))
        self.assertEqual(format_override("Tee", {"code_said": "have_this_look", "claude_said": "good_buy"}),
                         "Tee: code said have_this_look, Claude said good_buy")
        self.assertEqual(format_override("Tee", {"claude_said": "good_buy"}), "Tee: code said -, Claude said good_buy")


class RunTests(unittest.TestCase):
    def test_run_row_uses_engine_latency_and_catches_errors(self):
        row = {"num": 1, "name": "Black tee", "price": 14.9, "store": "uniqlo", "image_url": None,
               "description": None, "expected": "have_this_look", "notes": ""}
        ok = run_row(FakeEngine({"Black tee": ("have_this_look", {"latency_ms": 1234.0})}), {}, row)
        self.assertEqual((ok["got"], ok["latency_ms"], ok["error"]), ("have_this_look", 1234.0, None))
        bad = run_row(FakeEngine({"Black tee": RuntimeError("boom")}), {}, row)
        self.assertEqual(bad["error"], "RuntimeError: boom")
        self.assertIsNone(bad["got"])


class MainTests(unittest.TestCase):
    """main() end to end with a fake backend.db module, so nothing touches the real database."""

    def setUp(self):
        self.verdicts = FakeCollection(count=5)
        fake_db = types.ModuleType("backend.db")
        fake_db.users = FakeCollection(docs=[{"_id": "u1", "email": "demo@secondlook.test"}])
        fake_db.verdicts = self.verdicts
        fake_db.closet_items = FakeCollection(count=9)
        fake_db.trends = FakeCollection(docs=[{"name": "PLACEHOLDER trend 1"}])
        self.saved = sys.modules.get("backend.db")
        sys.modules["backend.db"] = fake_db
        self.csv = str(write_csv(CSV_TEXT))
        self.table = {
            "Black tee": ("have_this_look", {"decided_by": "code", "latency_ms": 100.0}),
            "Navy sweater": ("good_buy", {"decided_by": "claude", "latency_ms": 300.0}),
            "Wide cargo pants": ("have_this_look", {"decided_by": "claude", "latency_ms": 200.0,
                                                    "override": {"code_said": "good_buy",
                                                                 "claude_said": "have_this_look"}}),
        }

    def tearDown(self):
        if self.saved is None:
            sys.modules.pop("backend.db", None)
        else:
            sys.modules["backend.db"] = self.saved

    def run_main(self, args, engine):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["--csv", self.csv, *args], engine=engine)
        return code, out.getvalue()

    def test_disagreement_exits_zero_and_reports(self):
        engine = FakeEngine(self.table)
        code, out = self.run_main([], engine)
        self.assertEqual(code, 0)
        self.assertEqual(len(engine.calls), 3)
        self.assertIn("overall        2/3 = 67%", out)
        self.assertIn("good_buy       1/2 = 50%", out)
        self.assertIn("TODO rows skipped: 2 (rows 3, 5)", out)
        self.assertIn("Wide cargo pants: code said good_buy, Claude said have_this_look", out)
        self.assertIn("claude    2", out)
        self.assertIn("p50 200 ms   p95 300 ms   n=3", out)
        self.assertIn("read-only check, after:  verdicts=5", out)

    def test_engine_error_exits_one(self):
        self.table["Navy sweater"] = RuntimeError("boom")
        code, out = self.run_main([], FakeEngine(self.table))
        self.assertEqual(code, 1)
        self.assertIn("row 2 Navy sweater: RuntimeError: boom", out)

    def test_db_change_exits_three(self):
        def writing_engine(user, item, description):
            self.verdicts.count += 1
            return {"verdict": "good_buy"}, {}
        code, out = self.run_main(["--row", "2"], writing_engine)
        self.assertEqual(code, 3)
        self.assertIn("FAIL", out)

    def test_dry_run_never_calls_engine(self):
        engine = FakeEngine(self.table)
        code, out = self.run_main(["--dry-run", "--limit", "2"], engine)
        self.assertEqual((code, engine.calls), (0, []))
        self.assertIn("rows to run: 2", out)

    def test_bad_csv_exits_two(self):
        self.csv = str(write_csv(HEADER + "Tee,10,uniqlo,,,maybe,\n"))
        code, _ = self.run_main([], FakeEngine({}))
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
