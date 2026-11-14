"""Offline tests for alternative links: provenance, link checks, search fallback, verdict mapping.
No network, no database, no Claude calls (HTTP goes through httpx.MockTransport).

Run from the project root:  python -m unittest discover tests
"""
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from backend import alternative_finder as finder  # noqa: E402
from backend.alternatives import (alternatives_for_trend, check_url, classify, is_home_page,  # noqa: E402
                                  is_search_page, page_price, registrable_domain, search_link,
                                  stored_verdict_alternative, trend_alternative_to_verdict)
from backend.models import Alternative, Verdict  # noqa: E402
from backend.trends import TrendDoc  # noqa: E402

PRODUCT = "https://www.uniqlo.com/us/en/products/E465000-000/00"
GAP_PRODUCT = "https://www.gap.com/browse/product.do?pid=123456"


def search_content(results, text, citations=()):
    """Claude content blocks: one search, its results, then a text block."""
    return [
        NS(type="server_tool_use", name="web_search", input={"query": "uniqlo straight jeans"}),
        NS(type="web_search_tool_result", content=[NS(url=u, title=t, page_age=None) for u, t in results]),
        NS(type="text", text=text, citations=[NS(type="web_search_result_location", url=u, cited_text=c)
                                              for u, c in citations]),
    ]


def mock_client(routes):
    """routes: url -> (status, headers, body). Redirects use a Location header."""
    def handler(request):
        status, headers, body = routes.get(str(request.url), (404, {}, ""))
        return httpx.Response(status, headers={"content-type": "text/html", **headers}, text=body)
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        text = ('Found one.\n```json\n{"products": ['
                f'{{"index": 0, "url": "{PRODUCT}/", "product_name": "Straight Jeans", "price": 49.9, "store": "Uniqlo"}},'
                '{"index": 1, "url": "https://www.uniqlo.com/us/en/products/MADE-UP/00", "product_name": "Tee", "price": 14.9},'
                '{"index": 0, "url": "https://www.uniqlo.com/us/en/search?q=jeans", "product_name": "x"}'
                ']}\n```')
        self.search = finder.parse_content(search_content(
            [(PRODUCT, "Straight Jeans | UNIQLO US"), ("https://www.uniqlo.com/us/en/search?q=jeans", "Search")],
            text, citations=[(PRODUCT, "Straight Jeans $49.90")]))

    def test_only_urls_from_search_results_survive(self):
        kept, notes = finder.screen_candidates(finder.parse_candidates(self.search.text), self.search, 2)
        self.assertEqual([c["url"] for c in kept], [PRODUCT])  # stored as the search result's own URL
        self.assertTrue(any("not a URL from the web search results" in n for n in notes))
        self.assertTrue(any("search, or error page" in n for n in notes))

    def test_non_retailer_and_bad_index_dropped(self):
        search = finder.parse_content(search_content([("https://www.shein.com/p/1.html", "x"), (PRODUCT, "y")], ""))
        kept, notes = finder.screen_candidates(
            [{"index": 0, "url": "https://www.shein.com/p/1.html"}, {"index": 5, "url": PRODUCT}], search, 1)
        self.assertEqual(kept, [])
        self.assertEqual(len(notes), 2)

    def test_search_error_object_is_recorded_not_raised(self):
        out = finder.parse_content([NS(type="web_search_tool_result", content=NS(error_code="max_uses_exceeded"))])
        self.assertEqual(out.errors, ["max_uses_exceeded"])
        self.assertEqual(out.result_urls, {})

    def test_price_only_from_cited_text(self):
        passages = self.search.cited_text[finder.normalize_url(PRODUCT)]
        self.assertEqual(finder.price_from_citations(49.9, passages), 49.9)
        self.assertIsNone(finder.price_from_citations(59.9, passages))
        self.assertIsNone(finder.price_from_citations("abc", passages))

    def test_malformed_json_gives_no_candidates(self):
        self.assertEqual(finder.parse_candidates("no json here"), [])
        self.assertEqual(finder.parse_candidates("```json\n{bad\n```"), [])


class UrlShapeTests(unittest.TestCase):
    def test_home_pages(self):
        for url in ("https://www.uniqlo.com/", "https://www.uniqlo.com/us/en/", "https://www2.hm.com/en_us/index.html",
                    "https://www.gap.com"):
            self.assertTrue(is_home_page(url), url)
        self.assertFalse(is_home_page(PRODUCT))

    def test_search_pages(self):
        for url in ("https://www.gap.com/browse/search.do?searchText=jeans", "https://www.target.com/s?searchTerm=tee",
                    "https://www.uniqlo.com/us/en/search?q=jeans", "https://www.levi.com/US/en_US/search/jeans"):
            self.assertTrue(is_search_page(url), url)
        self.assertFalse(is_search_page(GAP_PRODUCT))

    def test_registrable_domain(self):
        self.assertEqual(registrable_domain("https://oldnavy.gap.com/x"), "gap.com")
        self.assertEqual(registrable_domain("shop.example.co.uk"), "example.co.uk")


class LinkCheckTests(unittest.TestCase):
    def test_classify_ok_and_redirects(self):
        self.assertTrue(classify(PRODUCT, 200, PRODUCT, True).verified)
        self.assertFalse(classify(PRODUCT, 200, "https://www.uniqlo.com/us/en/", True).ok)
        self.assertIn("search", classify(PRODUCT, 200, "https://www.uniqlo.com/us/en/search?q=x", True).reason)
        self.assertIn("another site", classify(PRODUCT, 200, "https://www.example.com/p/1", True).reason)
        self.assertFalse(classify(PRODUCT, 404, PRODUCT, True).ok)
        self.assertFalse(classify(PRODUCT, None, None, True).ok)

    def test_403_rule(self):
        blocked = classify(PRODUCT, 403, PRODUCT, from_search_result=True)
        self.assertTrue(blocked.ok)
        self.assertFalse(blocked.verified)  # so verified_at stays null
        self.assertFalse(classify(PRODUCT, 403, PRODUCT, from_search_result=False).ok)

    def test_check_url_follows_redirect_to_home(self):
        with mock_client({GAP_PRODUCT: (301, {"location": "https://www.gap.com/"}, ""),
                          "https://www.gap.com/": (200, {}, "<title>Gap</title>")}) as http:
            result = check_url(GAP_PRODUCT, True, client=http)
        self.assertFalse(result.ok)
        self.assertIn("home page", result.reason)

    def test_check_url_reads_price_and_title(self):
        html = ('<meta property="og:title" content="Men&#39;s Straight Jeans | Gap">'
                '<script type="application/ld+json">{"offers": {"price": "59.95"}}</script>')
        with mock_client({GAP_PRODUCT: (200, {}, html)}) as http:
            result = check_url(GAP_PRODUCT, True, client=http)
        self.assertTrue(result.verified)
        self.assertEqual(result.page_price, 59.95)
        self.assertEqual(result.page_title, "Men's Straight Jeans")
        self.assertEqual(page_price("<p>no price</p>"), None)


class FindLinksTests(unittest.TestCase):
    """find_links end to end with a fake Claude client and a mock HTTP client."""

    def fake_claude(self, content):
        create = lambda **kw: NS(content=content, stop_reason="end_turn")  # noqa: E731
        return NS(messages=NS(create=create))

    def trend(self):
        return {"name": "Barrel jeans", "category": "jeans", "alternatives": [
            {"type": "Straight-leg jeans", "why_it_lasts": "Classic", "where_to_look": "Denim"},
            {"type": "Plain white tee", "why_it_lasts": "Goes with all", "where_to_look": "Basics"}]}

    def test_blocked_product_kept_unverified_and_other_falls_back(self):
        text = f'```json\n{{"products": [{{"index": 0, "url": "{PRODUCT}", "product_name": "S", "price": 49.9}}]}}\n```'
        content = search_content([(PRODUCT, "Straight Jeans | UNIQLO US")], text, [(PRODUCT, "only $49.90")])
        os.environ.setdefault("ANTHROPIC_API_KEY", "test")
        os.environ.setdefault("CLAUDE_MODEL", "test")
        with mock_client({PRODUCT: (403, {}, "")}) as http:
            out, audit = finder.find_links(self.trend(), client=self.fake_claude(content), http=http, log=lambda *_: None)
        a, b = out["alternatives"]
        self.assertEqual((a["link_kind"], a["product_url"], a["verified_at"], a["price"], a["store"]),
                         ("product", PRODUCT, None, 49.9, "Uniqlo"))
        self.assertEqual(a["product_name"], "Straight Jeans")
        self.assertEqual(b["link_kind"], "search")
        self.assertTrue(b["product_url"].startswith("https://www.gap.com/browse/search.do?searchText=plain+white+tee"))
        TrendDoc.model_validate({**out, "lifespan_label": "x", "sources": [{"title": "s", "url": "https://e.com"}],
                                 "origin": "seed"})


class FallbackAndMappingTests(unittest.TestCase):
    def test_search_link_builder(self):
        link = search_link("Straight-leg, dark wash jeans.")
        self.assertEqual(link["store"], "Gap")
        self.assertEqual(link["url"], "https://www.gap.com/browse/search.do?searchText=straight-leg+dark+wash+jeans")
        self.assertEqual(search_link("Wool coat", category="coat")["store"], "L.L.Bean")

    def test_trend_alternative_maps_to_verdict_with_url(self):
        product = trend_alternative_to_verdict({"type": "Straight jeans", "why_it_lasts": "Lasts", "where_to_look": "x",
                                                "product_name": "Gap 1969 Straight Jeans", "product_url": GAP_PRODUCT,
                                                "price": 69.95, "store": "Gap"})
        self.assertEqual(product, {"name": "Gap 1969 Straight Jeans", "url": GAP_PRODUCT, "price": 69.95,
                                   "reason": "Lasts", "store": "Gap", "link_kind": "product"})
        old = trend_alternative_to_verdict({"type": "Crew-neck tee", "why_it_lasts": "Lasts", "where_to_look": "x"})
        self.assertEqual((old["name"], old["link_kind"], old["store"]), ("Crew-neck tee", "search", "Gap"))
        self.assertIn("searchText=crew-neck+tee", old["url"])

    def test_trend_without_alternatives_still_links(self):
        alts = alternatives_for_trend({"name": "PLACEHOLDER", "alternatives": []}, fallback_query="classic straight-leg jeans")
        self.assertEqual(len(alts), 1)
        self.assertTrue(alts[0]["url"])
        self.assertEqual(alternatives_for_trend({"category": "shoes"})[0]["store"], "L.L.Bean")
        self.assertEqual(alternatives_for_trend(None), [])

    def test_verdict_alternative_url_required(self):
        with self.assertRaises(ValidationError):
            Alternative(name="x", url=None)
        with self.assertRaises(ValidationError):
            Alternative(name="x")
        self.assertEqual(Alternative(name="x", url="https://a.com/p").link_kind, "product")

    def test_old_stored_verdict_with_null_url_reads(self):
        fixed = stored_verdict_alternative({"name": "Classic straight-leg version", "url": None, "price": 48.0, "reason": "r"})
        self.assertEqual(fixed["link_kind"], "search")
        v = Verdict(id="1", item={"name": "i"}, verdict="micro_trend", reason="r", alternatives=[fixed],
                    action="none", feedback="none", created_at="2026-11-14T00:00:00Z")
        self.assertTrue(v.alternatives[0].url.startswith("https://"))


class VerifyScriptTests(unittest.TestCase):
    def test_statuses_and_updates(self):
        from scripts.verify_alternative_links import verify_trend
        dead_url = "https://www.gap.com/browse/product.do?pid=999"
        search = "https://www.gap.com/browse/search.do?searchText=tee"
        doc = {"name": "T", "alternatives": [
            {"type": "a", "product_url": GAP_PRODUCT, "link_kind": "product"},
            {"type": "b", "product_url": PRODUCT, "link_kind": "product"},
            {"type": "c", "product_url": dead_url, "link_kind": "product"},
            {"type": "d", "product_url": search, "link_kind": "search"},
            {"type": "e"}]}
        with mock_client({GAP_PRODUCT: (200, {}, "<title>Tee | Gap</title>"), PRODUCT: (403, {}, ""),
                          dead_url: (302, {"location": "https://www.gap.com/"}, ""),
                          "https://www.gap.com/": (200, {}, ""), search: (200, {}, "")}) as http:
            rows, updates = verify_trend(doc, http)
        self.assertEqual([r[0] for r in rows], ["ok", "blocked", "dead", "search", "no link"])
        self.assertIsNotNone(updates["alternatives.0.verified_at"])
        self.assertIsNone(updates["alternatives.1.verified_at"])
        self.assertNotIn("alternatives.2.verified_at", updates)


class TrendModelTests(unittest.TestCase):
    BASE = {"name": "T", "lifespan_label": "x", "sources": [{"title": "s", "url": "https://e.com/a"}], "origin": "seed"}

    def test_old_alternative_shape_still_validates(self):
        doc = TrendDoc.model_validate({**self.BASE, "alternatives": [
            {"type": "t", "why_it_lasts": "w", "where_to_look": "l", "url": "https://e.com/a"}]})
        self.assertIsNone(doc.alternatives[0].link_kind)

    def test_source_rule_still_applies_to_url_not_product_url(self):
        with self.assertRaises(ValidationError):
            TrendDoc.model_validate({**self.BASE, "alternatives": [
                {"type": "t", "why_it_lasts": "w", "where_to_look": "l", "url": "https://other.com"}]})
        doc = TrendDoc.model_validate({**self.BASE, "alternatives": [
            {"type": "t", "why_it_lasts": "w", "where_to_look": "l", "product_url": GAP_PRODUCT}]})
        self.assertEqual(doc.alternatives[0].link_kind, "product")

    def test_product_url_must_be_http(self):
        with self.assertRaises(ValidationError):
            TrendDoc.model_validate({**self.BASE, "alternatives": [
                {"type": "t", "why_it_lasts": "w", "where_to_look": "l", "product_url": "javascript:alert(1)"}]})


if __name__ == "__main__":
    unittest.main()
