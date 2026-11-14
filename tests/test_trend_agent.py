"""Offline tests for the trend agent guards and the seed document rules. No network, no database.

Run from the project root:  python -m unittest discover tests
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import ValidationError  # noqa: E402

from backend.trends import TrendDoc, name_key  # noqa: E402
from types import SimpleNamespace as NS  # noqa: E402

from scripts.run_trend_agent import (MAX_NEW_PER_RUN, StructuredAlternative, StructuredTrend,  # noqa: E402
                                     annotate, apply_guards, parse_search_content)

RUN = {
    "chunks": [{"title": f"Source {i}", "url": f"https://example.com/{i}"} for i in range(1, 4)],
    "search_entry_point_html": "<div>chips</div>",
}


def trend(name, source_ids=("S1",), **kw):
    return StructuredTrend(name=name, source_ids=list(source_ids), lifespan_label="label", **kw)


class GuardTests(unittest.TestCase):
    def test_no_source_is_discarded(self):
        kept, notes = apply_guards([trend("A", []), trend("B", ["S9"]), trend("C", ["nonsense"])], RUN, set(), 5)
        self.assertEqual(kept, [])
        self.assertEqual(len(notes), 3)

    def test_dedupe_case_insensitive_against_existing_and_batch(self):
        kept, _ = apply_guards([trend("Barrel  JEANS"), trend("Ballet flats"), trend("ballet Flats")],
                               RUN, {name_key("barrel jeans")}, 5)
        self.assertEqual([d.name for d in kept], ["Ballet flats"])

    def test_cap(self):
        kept, notes = apply_guards([trend(f"T{i}") for i in range(8)], RUN, set(), 99)
        self.assertEqual(len(kept), 8)  # apply_guards trusts its max_new; main() clamps it
        kept, notes = apply_guards([trend(f"T{i}") for i in range(8)], RUN, set(), MAX_NEW_PER_RUN)
        self.assertEqual(len(kept), 5)
        self.assertTrue(any("cap" in n for n in notes))

    def test_uncited_lifespan_is_dropped(self):
        kept, notes = apply_guards([trend("A", lifespan_weeks_est=12), trend("B", lifespan_weeks_est=8,
                                    lifespan_source_id="S7")], RUN, set(), 5)
        self.assertEqual([d.lifespan_weeks_est for d in kept], [None, None])
        self.assertEqual(sum("lifespan" in n for n in notes), 2)

    def test_cited_lifespan_kept_and_source_attached(self):
        kept, _ = apply_guards([trend("A", ["S1"], lifespan_weeks_est=12, lifespan_source_id="[S2]")], RUN, set(), 5)
        doc = kept[0]
        self.assertEqual(doc.lifespan_weeks_est, 12)
        self.assertEqual(doc.lifespan_source_url, "https://example.com/2")
        self.assertIn("https://example.com/2", [s.url for s in doc.sources])

    def test_alternative_links_only_from_sources(self):
        alts = [StructuredAlternative(type="x", why_it_lasts="y", where_to_look="z", source_id="S3"),
                StructuredAlternative(type="x", why_it_lasts="y", where_to_look="z", source_id="S99")]
        doc = apply_guards([trend("A", alternatives=alts)], RUN, set(), 5)[0][0]
        self.assertEqual([a.url for a in doc.alternatives], ["https://example.com/3", None])

    def test_agent_fields(self):
        doc = apply_guards([trend("A")], RUN, set(), 5)[0][0]
        self.assertEqual(doc.origin, "agent")
        self.assertEqual(doc.search_suggestions_html, "<div>chips</div>")
        self.assertIsNotNone(doc.first_seen)


class AnnotateTests(unittest.TestCase):
    def test_markers_use_utf8_byte_offsets(self):
        text = "Café cardigans. Jeans."
        out = annotate(text, [{"end_index": len("Café cardigans.".encode()), "chunk_indices": [0, 2]},
                              {"end_index": len(text.encode()), "chunk_indices": [1]}])
        self.assertEqual(out, "Café cardigans. [S1][S3] Jeans. [S2]")


class SearchParseTests(unittest.TestCase):
    def cite(self, url, title):
        return NS(type="web_search_result_location", url=url, title=title)

    def test_cited_sources_become_s_ids_with_markers(self):
        content = [
            NS(type="text", text="Let me search.", citations=None),
            NS(type="server_tool_use", name="web_search", input={"query": "gen z trends"}),
            NS(type="web_search_tool_result", content=[NS(url="https://a.com"), NS(url="https://b.com"), NS(url="https://c.com")]),
            NS(type="web_search_tool_result", content=NS(error_code="max_uses_exceeded")),
            NS(type="text", text=" Café cardigans are big.", citations=[self.cite("https://a.com", "A"), self.cite("https://a.com", "A")]),
            NS(type="text", text=" Jeans too.", citations=[self.cite("https://b.com", "B"), self.cite("https://a.com", "A")]),
        ]
        run = parse_search_content(content)
        self.assertEqual([c["url"] for c in run["chunks"]], ["https://a.com", "https://b.com"])
        self.assertEqual(run["search_result_count"], 3)
        self.assertEqual(run["search_errors"], ["max_uses_exceeded"])
        self.assertEqual(run["web_search_queries"], ["gen z trends"])
        self.assertIsNone(run["search_entry_point_html"])
        self.assertEqual(annotate(run["text"], run["supports"]),
                         "Let me search. Café cardigans are big. [S1] Jeans too. [S2][S1]")

    def test_agent_trends_from_claude_have_no_search_html(self):
        run = {**RUN, "search_entry_point_html": None}
        self.assertIsNone(apply_guards([trend("A")], run, set(), 5)[0][0].search_suggestions_html)


class SeedRuleTests(unittest.TestCase):
    base = {"name": "A", "lifespan_label": "x", "origin": "seed", "sources": [{"title": "t", "url": "https://a.com"}]}

    def test_lifespan_number_needs_cited_source(self):
        with self.assertRaises(ValidationError):
            TrendDoc(**self.base, lifespan_weeks_est=10)
        TrendDoc(**self.base, lifespan_weeks_est=10, lifespan_source_url="https://a.com")

    def test_needs_a_source(self):
        with self.assertRaises(ValidationError):
            TrendDoc(**{**self.base, "sources": []})

    def test_seed_has_no_search_suggestions(self):
        with self.assertRaises(ValidationError):
            TrendDoc(**self.base, search_suggestions_html="<div/>")


if __name__ == "__main__":
    unittest.main()
