"""Offline tests for the Full analysis step (backend/services/analysis_details.py) and the composition parser.
Claude, embeddings and MongoDB are faked. No network, no database writes.

Run from the project root:  python -m unittest tests.test_analysis_details
"""
import logging
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bson import ObjectId  # noqa: E402

from backend.services import analysis_details as d  # noqa: E402
from backend.services.composition import fibers_from_fabric, parse_composition  # noqa: E402

logging.getLogger("secondlook.details").setLevel(logging.ERROR)

USER = {"_id": ObjectId(), "email": "test_details@secondlook.test"}
TANK, JEANS, SKIRT, COAT = (ObjectId() for _ in range(4))
CLOSET = {
    TANK: {"name": "White tank top", "attributes": {"category": "tee", "color_family": "white", "style": "basic"}},
    JEANS: {"name": "Black slim jeans", "attributes": {"category": "jeans", "color_family": "black", "style": "basic"}},
    SKIRT: {"name": "Denim mini skirt", "attributes": {"category": "skirt", "color_family": "blue", "style": "y2k"}},
    COAT: {"name": "Camel wool coat", "attributes": {"category": "coat", "color_family": "beige", "style": "formal"}},
}
THRESHOLDS = {"cost_per_wear_good": 1.0, "cost_per_wear_poor": 2.5}


def closet_doc(oid):
    return {"_id": oid, "user_id": USER["_id"], "source": "quick_add", "status": "owned", "name": CLOSET[oid]["name"],
            "attributes": CLOSET[oid]["attributes"], "description": "", "image_data": None,
            "created_at": datetime(2026, 11, 1, tzinfo=timezone.utc)}


class FakeCloset:
    def __init__(self, size):
        self.size = size

    def count_documents(self, query, limit=None):
        return min(self.size, limit or self.size)

    def find(self, query, projection=None):
        ids = query.get("_id", {}).get("$in", [])
        return [closet_doc(i) for i in ids if i in CLOSET]


def verdict_doc(verdict="good_buy", price=30.0, materials="Shell: 95% Polyester, 5% Elastane. Lining: 100% Nylon",
                lookalikes=(), description="Fitted tube top."):
    return {"_id": ObjectId(), "user_id": USER["_id"], "verdict": verdict, "reason": "r",
            "item": {"name": "White tube top", "price": price, "store": "Shop", "description": description,
                     "materials": materials},
            "attributes": {"category": "tee", "color_family": "white", "fabric": "polyester", "cut": "fitted",
                           "style": "y2k", "description": "White fitted tube top."},
            "matched_closet_item_ids": list(lookalikes), "matched_trend_id": None}


def reply(**over):
    base = {
        "durability": {"pilling_risk": "high", "note": "Polyester knit will pill.", "expected_wears": 15,
                       "expected_months": 6},
        "trend_note": None,
        "similar_ids": [str(TANK)],
        "outfits": [
            {"name": "Easy weekend", "closet_item_ids": [str(JEANS)], "uses_new_item": True},
            {"name": "Going out", "closet_item_ids": [str(SKIRT), "not-a-real-id"], "uses_new_item": True},
            {"name": "Layered", "closet_item_ids": [str(COAT), str(JEANS)], "uses_new_item": True},
        ],
        "alternatives": [{"name": "Plain cotton tank", "search_query": "white cotton tank top"}],
    }
    base.update(over)
    return base


class DetailsTest(unittest.TestCase):
    def run_details(self, doc, *, closet_size=4, claude=None, claude_error=None):
        candidates = [{"_id": oid, "name": CLOSET[oid]["name"], "attributes": CLOSET[oid]["attributes"], "score": 0.8}
                      for oid in CLOSET]
        calls = {"claude": []}

        def fake_claude(payload, timeout_s):
            calls["claude"].append(payload)
            if claude_error:
                raise claude_error
            return claude if claude is not None else reply()

        patches = [
            mock.patch.object(d, "closet_items", FakeCloset(closet_size)),
            mock.patch.object(d, "vector_search", return_value=candidates),
            mock.patch.object(d, "embed_text", return_value=[0.1] * 768),
            mock.patch.object(d, "call_claude", side_effect=fake_claude),
            mock.patch.object(d, "thresholds", return_value=THRESHOLDS),
            mock.patch.object(d.trends, "find_one", return_value=None),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return d.build_details(USER, doc), calls

    # ---------- materials and health ----------

    def test_page_composition_and_sourced_health_notes(self):
        out, _ = self.run_details(verdict_doc())
        self.assertEqual(out.materials_source, "page")
        self.assertEqual([(f.fiber, f.pct) for f in out.materials], [("polyester", 95.0), ("spandex", 5.0)])
        subjects = [h.subject for h in out.health_notes]
        self.assertEqual(subjects, ["polyester", "spandex"])  # lining nylon is not the main fabric
        self.assertTrue(all(h.url.startswith("https://") and h.source for h in out.health_notes))

    def test_fabric_attribute_fallback_and_finish_note(self):
        doc = verdict_doc(materials=None, description="Wrinkle-free oxford shirt.")
        out, _ = self.run_details(doc)
        self.assertEqual(out.materials_source, "attributes")
        self.assertEqual([(f.fiber, f.pct) for f in out.materials], [("polyester", None)])
        self.assertIn("wrinkle-free finish", [h.subject for h in out.health_notes])

    def test_cotton_has_no_health_note(self):
        out, _ = self.run_details(verdict_doc(materials="100% cotton"))
        self.assertEqual(out.health_notes, [])

    # ---------- cost per wear ----------

    def test_poor_cost_per_wear_adds_search_alternatives(self):
        out, _ = self.run_details(verdict_doc(price=60.0))  # 60 / 15 wears = 4.00
        self.assertEqual((out.cost.cost_per_wear, out.cost.rating), (4.0, "poor"))
        self.assertEqual(len(out.alternatives), 1)
        self.assertEqual(out.alternatives[0].link_kind, "search")
        self.assertTrue(out.alternatives[0].url.startswith("https://"))

    def test_good_cost_per_wear_has_no_alternatives(self):
        out, _ = self.run_details(verdict_doc(price=12.0))  # 12 / 15 = 0.80
        self.assertEqual(out.cost.rating, "good")
        self.assertEqual(out.alternatives, [])

    def test_no_price_means_no_rating(self):
        out, _ = self.run_details(verdict_doc(price=None))
        self.assertIsNone(out.cost.cost_per_wear)
        self.assertIsNone(out.cost.rating)

    # ---------- closet sentiment and outfits ----------

    def test_good_buy_outfits_use_new_item_and_drop_unknown_ids(self):
        out, _ = self.run_details(verdict_doc("good_buy"))
        self.assertEqual(len(out.outfits), 3)
        self.assertTrue(all(o.uses_new_item for o in out.outfits))
        self.assertEqual(out.outfits[1].closet_item_ids, [str(SKIRT)])  # the invented id is gone
        self.assertEqual(out.outfits[1].closet_item_names, ["Denim mini skirt"])
        self.assertEqual([c.name for c in out.similar_closet_items], ["White tank top"])

    def test_not_recommended_outfits_are_built_around_the_lookalike(self):
        claude = reply(outfits=[
            {"name": "Tank instead", "closet_item_ids": [str(TANK), str(JEANS)], "uses_new_item": False},
            {"name": "No anchor", "closet_item_ids": [str(JEANS)], "uses_new_item": False},
        ])
        out, _ = self.run_details(verdict_doc("have_this_look", lookalikes=[TANK]), claude=claude)
        self.assertEqual([o.name for o in out.outfits], ["Tank instead"])  # must contain the lookalike
        self.assertFalse(out.outfits[0].uses_new_item)

    def test_empty_closet_skips_closet_parts(self):
        out, calls = self.run_details(verdict_doc(), closet_size=0)
        self.assertFalse(out.closet_checked)
        self.assertEqual(out.outfits, [])
        self.assertEqual(out.similar_closet_items, [])
        self.assertEqual(calls["claude"][0]["closet_candidates"], [])

    # ---------- failures ----------

    def test_claude_failure_falls_back_to_code(self):
        out, _ = self.run_details(verdict_doc(), claude_error=TimeoutError("slow"))
        self.assertEqual(out.decided_by, "code")
        self.assertEqual(out.durability.expected_wears, d.expected_wears_for("tee"))  # wears.json fallback
        self.assertEqual(out.outfits, [])
        self.assertTrue(out.errors)
        self.assertEqual(out.materials_source, "page")  # code parts still work

    def test_out_of_range_estimates_are_clamped(self):
        claude = reply(durability={"pilling_risk": "extreme", "note": "x", "expected_wears": 99999,
                                   "expected_months": 0})
        out, _ = self.run_details(verdict_doc(), claude=claude)
        self.assertIsNone(out.durability.pilling_risk)
        self.assertEqual((out.durability.expected_wears, out.durability.expected_months), (1000, 1))


class CompositionTest(unittest.TestCase):
    def test_cases(self):
        cases = {
            "60% Cotton, 40% Polyester": [("cotton", 60.0), ("polyester", 40.0)],
            "Cotton 52%, Viscose 48%": [("cotton", 52.0), ("rayon", 48.0)],
            "Main: 70% wool 30% polyamide; Rib: 98% cotton 2% elastane": [("wool", 70.0), ("nylon", 30.0)],
            "Body: 100% cotton. Pocketing: 65% polyester 35% cotton": [("cotton", 100.0)],
            "50,5% cotton 49,5% recycled polyester": [("cotton", 50.5), ("polyester", 49.5)],
            "Super soft. 100% comfort guaranteed.": [],
            "": [],
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual([(f["fiber"], f["pct"]) for f in parse_composition(text)], want)

    def test_fabric_fallback(self):
        self.assertEqual(fibers_from_fabric("denim"), [{"fiber": "cotton", "pct": None}])
        self.assertEqual(fibers_from_fabric("blend"), [])
        self.assertEqual(fibers_from_fabric(None), [])


if __name__ == "__main__":
    unittest.main()
