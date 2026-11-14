"""Offline tests for the trend agent guards and the seed document rules. No network, no database.

Run from the project root:  python -m unittest discover tests
"""
import sys
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import ValidationError  # noqa: E402

from backend.trends import TrendDoc  # noqa: E402
from scripts.run_trend_agent import (MAX_NEW_PER_RUN, StructuredAlternative, StructuredTrend,  # noqa: E402
                                     annotate, apply_guards, clean_label, date_from_url, parse_page_age,
                                     parse_search_content)

TODAY = date(2026, 11, 14)
RUN = {
    "chunks": [
        {"title": "Source 1", "url": "https://example.com/1", "page_age": "October 2, 2026"},
        {"title": "Source 2", "url": "https://example.com/2", "page_age": None},
        {"title": "Source 3", "url": "https://example.com/3", "page_age": "3 weeks ago"},
        {"title": "Old 4", "url": "https://example.com/4", "page_age": "March 1, 2025"},
        {"title": "Old 5", "url": "https://example.com/2024/12/fading/", "page_age": None},
    ],
    "search_entry_point_html": None,
}


def trend(name, source_ids=("S1",), category="jeans", **kw):
    return StructuredTrend(name=name, source_ids=list(source_ids), category=category,
                           lifespan_label="label", **kw)


def guard(trends, existing=(), max_new=5):
    return apply_guards(trends, RUN, list(existing), max_new, today=TODAY)


class GuardTests(unittest.TestCase):
    def test_no_source_is_discarded(self):
        kept, notes = guard([trend("A", []), trend("B", ["S9"]), trend("C", ["nonsense"])])
        self.assertEqual(kept, [])
        self.assertEqual(len(notes), 3)

    def test_no_category_is_discarded(self):
        kept, notes = guard([trend("Cherry red", category=None, color="red")])
        self.assertEqual(kept, [])
        self.assertIn("no category", notes[0])

    def test_dedupe_by_name_against_existing_and_batch(self):
        kept, _ = guard([trend("Barrel  JEANS"), trend("Ballet flats", category="shoes"),
                         trend("ballet Flats", category="shoes", color="black")],
                        existing=[{"name": "barrel jeans", "category": "jeans", "features": {}}])
        self.assertEqual([d.name for d in kept], ["Ballet flats"])

    def test_dedupe_by_category_and_features(self):
        existing = [{"name": "Barrel jeans", "category": "jeans",
                     "features": {"cut": "wide", "color": "blue", "fabric": "denim"}}]
        same = dict(category="jeans", cut="wide", color="blue", fabric="denim")
        kept, notes = guard([trend("Horseshoe jeans", **same), trend("Black wide jeans", **{**same, "color": "black"}),
                             trend("Black balloon jeans", **{**same, "color": "black"})], existing=existing)
        self.assertEqual([d.name for d in kept], ["Black wide jeans"])
        self.assertTrue(any("same category and features as 'Barrel jeans'" in n for n in notes))
        self.assertTrue(any("same category and features as 'Black wide jeans'" in n for n in notes))

    def test_cap(self):
        kept, notes = guard([trend(f"T{i}", category=c)
                             for i, c in enumerate(["jeans", "shoes", "bag", "skirt", "dress", "coat", "tee"])],
                            max_new=MAX_NEW_PER_RUN)
        self.assertEqual(len(kept), 5)
        self.assertTrue(any("cap" in n for n in notes))

    def test_old_sources_dropped_and_all_old_discards(self):
        kept, notes = guard([trend("A", ["S1", "S4"]), trend("B", ["S4", "S5"], category="shoes")])
        self.assertEqual([[s.url for s in d.sources] for d in kept], [["https://example.com/1"]])
        self.assertTrue(any("'A': dropped 1 source" in n for n in notes))
        self.assertTrue(any("'B': every source is older than 12 months" in n for n in notes))

    def test_unknown_date_is_kept(self):
        kept, _ = guard([trend("A", ["S2"])])
        self.assertEqual(len(kept), 1)

    def test_uncited_or_old_lifespan_is_dropped(self):
        kept, notes = guard([trend("A", lifespan_weeks_est=12),
                             trend("B", category="shoes", lifespan_bucket="short", lifespan_source_id="S7"),
                             trend("C", category="bag", lifespan_bucket="short", lifespan_source_id="S4")])
        self.assertEqual([(d.lifespan_weeks_est, d.lifespan_bucket) for d in kept], [(None, None)] * 3)
        self.assertTrue(any(n.startswith("'C'") and "older than 12 months" in n for n in notes))

    def test_cited_lifespan_kept_bucket_from_weeks(self):
        kept, _ = guard([trend("A", ["S1"], lifespan_weeks_est=12, lifespan_bucket="long", lifespan_source_id="[S2]")])
        doc = kept[0]
        self.assertEqual((doc.lifespan_weeks_est, doc.lifespan_bucket), (12, "short"))
        self.assertEqual(doc.lifespan_source_url, "https://example.com/2")
        self.assertIn("https://example.com/2", [s.url for s in doc.sources])

    def test_bucket_only_with_source(self):
        doc = guard([trend("A", lifespan_bucket="medium", lifespan_source_id="S3")])[0][0]
        self.assertEqual((doc.lifespan_weeks_est, doc.lifespan_bucket), (None, "medium"))

    def test_direction_conflict_nulls_lifespan(self):
        kept, notes = guard([trend("A", lifespan_weeks_est=12, lifespan_bucket="short", lifespan_source_id="S1",
                                   lifespan_direction_conflict=True)])
        doc = kept[0]
        self.assertEqual((doc.lifespan_weeks_est, doc.lifespan_bucket, doc.lifespan_source_url), (None, None, None))
        self.assertTrue(any("disagree" in n for n in notes))

    def test_alternative_links_only_from_recent_sources(self):
        alts = [StructuredAlternative(type="x", why_it_lasts="y", where_to_look="z", source_id=sid)
                for sid in ("S3", "S99", "S4")]
        doc = guard([trend("A", alternatives=alts)])[0][0]
        self.assertEqual([a.url for a in doc.alternatives], ["https://example.com/3", None, None])

    def test_agent_fields(self):
        doc = guard([trend("A")])[0][0]
        self.assertEqual(doc.origin, "agent")
        self.assertIsNone(doc.search_suggestions_html)
        self.assertIsNotNone(doc.first_seen)


class LabelTests(unittest.TestCase):
    def test_source_ids_removed(self):
        self.assertEqual(clean_label("Sources disagree: Who What Wear S10 says they are in, while Who What Wear (S11) "
                                     "lists them as dated [S12]."),
                         "Sources disagree: Who What Wear says they are in, while Who What Wear lists them as dated.")
        self.assertEqual(clean_label("Vogue calls it a one-season trend"), "Vogue calls it a one-season trend")


class DateTests(unittest.TestCase):
    def test_page_age_formats(self):
        cases = {"April 30, 2025": date(2025, 4, 30), "Apr 30, 2025": date(2025, 4, 30),
                 "30 April 2025": date(2025, 4, 30), "2025-04-30T10:00:00Z": date(2025, 4, 30),
                 "3 weeks ago": date(2026, 10, 24), "2 years ago": date(2024, 11, 14),
                 "yesterday": date(2026, 11, 13), "5 hours ago": TODAY, "sometime": None, None: None,
                 "February 30, 2025": None}
        for raw, expected in cases.items():
            self.assertEqual(parse_page_age(raw, TODAY), expected, raw)

    def test_url_dates(self):
        self.assertEqual(date_from_url("https://a.com/2025/05/15/post/"), date(2025, 5, 15))
        self.assertEqual(date_from_url("https://a.com/2026/08/post"), date(2026, 8, 1))
        self.assertIsNone(date_from_url("https://a.com/top-2026-trends"))


class AnnotateTests(unittest.TestCase):
    def test_markers_use_utf8_byte_offsets(self):
        text = "Café cardigans. Jeans."
        out = annotate(text, [{"end_index": len("Café cardigans.".encode()), "chunk_indices": [0, 2]},
                              {"end_index": len(text.encode()), "chunk_indices": [1]}])
        self.assertEqual(out, "Café cardigans. [S1][S3] Jeans. [S2]")


class SearchParseTests(unittest.TestCase):
    def cite(self, url, title):
        return NS(type="web_search_result_location", url=url, title=title)

    def test_cited_sources_become_s_ids_with_markers_and_page_age(self):
        content = [
            NS(type="text", text="Let me search.", citations=None),
            NS(type="server_tool_use", name="web_search", input={"query": "gen z trends"}),
            NS(type="web_search_tool_result", content=[NS(url="https://a.com", page_age="May 1, 2026"),
                                                       NS(url="https://b.com", page_age=None),
                                                       NS(url="https://c.com", page_age="1 day ago")]),
            NS(type="web_search_tool_result", content=NS(error_code="max_uses_exceeded")),
            NS(type="text", text=" Café cardigans are big.",
               citations=[self.cite("https://a.com", "A"), self.cite("https://a.com", "A")]),
            NS(type="text", text=" Jeans too.", citations=[self.cite("https://b.com", "B"), self.cite("https://a.com", "A")]),
        ]
        run = parse_search_content(content)
        self.assertEqual([(c["url"], c["page_age"]) for c in run["chunks"]],
                         [("https://a.com", "May 1, 2026"), ("https://b.com", None)])
        self.assertEqual(run["search_result_count"], 3)
        self.assertEqual(run["search_errors"], ["max_uses_exceeded"])
        self.assertEqual(run["web_search_queries"], ["gen z trends"])
        self.assertIsNone(run["search_entry_point_html"])
        self.assertEqual(annotate(run["text"], run["supports"]),
                         "Let me search. Café cardigans are big. [S1] Jeans too. [S2][S1]")


class SeedRuleTests(unittest.TestCase):
    base = {"name": "A", "lifespan_label": "x", "origin": "seed", "sources": [{"title": "t", "url": "https://a.com"}]}

    def test_lifespan_needs_cited_source(self):
        with self.assertRaises(ValidationError):
            TrendDoc(**self.base, lifespan_weeks_est=10)
        with self.assertRaises(ValidationError):
            TrendDoc(**self.base, lifespan_bucket="short")
        TrendDoc(**self.base, lifespan_bucket="short", lifespan_source_url="https://a.com")

    def test_bucket_filled_from_weeks_and_checked(self):
        doc = TrendDoc(**self.base, lifespan_weeks_est=20, lifespan_source_url="https://a.com")
        self.assertEqual(doc.lifespan_bucket, "medium")
        with self.assertRaises(ValidationError):
            TrendDoc(**self.base, lifespan_weeks_est=10, lifespan_bucket="long", lifespan_source_url="https://a.com")

    def test_needs_a_source(self):
        with self.assertRaises(ValidationError):
            TrendDoc(**{**self.base, "sources": []})

    def test_seed_has_no_search_suggestions(self):
        with self.assertRaises(ValidationError):
            TrendDoc(**self.base, search_suggestions_html="<div/>")


if __name__ == "__main__":
    unittest.main()
