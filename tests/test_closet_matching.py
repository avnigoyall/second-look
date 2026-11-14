"""Offline tests for closet matching (backend/services/closet.py): the has_description flag and the two-vector
search that compares like with like. Embeddings and Atlas are faked. No network, no database writes.

Run from the project root:  python -m unittest discover tests
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bson import ObjectId  # noqa: E402

from backend.services import closet as c  # noqa: E402
from backend.services import closet_ingest as ingest  # noqa: E402

USER = ObjectId()
ATTRS = {"category": "shirt", "color_family": "black", "fabric": "cotton", "cut": "fitted", "style": "basic"}
FULL = {**ATTRS, "description": "A black button-up shirt with a pointed collar."}
FULL_TEXT = "black fitted cotton shirt, basic style. A black button-up shirt with a pointed collar."
ATTR_TEXT = "black fitted cotton shirt, basic style."


def fake_embed(text):
    """A distinct fake vector per text, so tests can tell which text a search used."""
    return [float(len(text)), 1.0]


class FakeAtlas:
    """Stands in for closet_items.aggregate. Returns canned docs per has_description filter, records each call."""

    def __init__(self, described=(), undescribed=()):
        self.described, self.undescribed, self.calls = list(described), list(undescribed), []

    def aggregate(self, pipeline):
        stage = pipeline[0]["$vectorSearch"]
        self.calls.append(stage)
        flag = stage["filter"].get("has_description")
        docs = self.described if flag == {"$ne": False} else self.undescribed if flag is False else \
            self.described + self.undescribed
        return iter(docs[:stage["limit"]])


def doc(name, cosine):
    return {"_id": ObjectId(), "name": name, "attributes": {"category": "shirt", "color_family": "black"},
            "status": "owned", "score": (1 + cosine) / 2}


class FlagTest(unittest.TestCase):
    def test_has_description(self):
        self.assertTrue(c.has_description("A black shirt."))
        for empty in ("", "   ", None):
            self.assertFalse(c.has_description(empty))

    def test_new_closet_doc_sets_flag(self):
        with mock.patch.object(c, "embed_text", side_effect=fake_embed) as embed:
            hand = c.new_closet_doc(USER, name="Black shirt", attributes=ATTRS, source="quick_add")
            described = c.new_closet_doc(USER, name="Black shirt", attributes=ATTRS, source="ingest_text",
                                         description="A black button-up shirt with a pointed collar.")
        self.assertIs(hand["has_description"], False)
        self.assertIs(described["has_description"], True)
        self.assertEqual([call.args[0] for call in embed.call_args_list], [ATTR_TEXT, FULL_TEXT])

    def test_query_texts(self):
        self.assertEqual(c.query_texts(FULL), (FULL_TEXT, ATTR_TEXT))
        self.assertEqual(c.query_texts(ATTRS), (ATTR_TEXT, ATTR_TEXT))


class FindMatchesTest(unittest.TestCase):
    def run_find(self, atlas, attrs=FULL, k=5):
        with mock.patch.object(c, "embed_text", side_effect=fake_embed) as embed, \
                mock.patch.object(c, "closet_items", atlas):
            return c.find_matches(USER, attrs, k), embed

    def test_each_kind_gets_its_own_vector(self):
        atlas = FakeAtlas(described=[doc("Oxford", 0.77)], undescribed=[doc("Quick-added button-up", 1.0)])
        matches, embed = self.run_find(atlas)
        self.assertEqual(sorted(call.args[0] for call in embed.call_args_list), sorted([FULL_TEXT, ATTR_TEXT]))
        by_filter = {str(s["filter"]["has_description"]): s for s in atlas.calls}
        self.assertEqual(by_filter["{'$ne': False}"]["queryVector"], fake_embed(FULL_TEXT))
        self.assertEqual(by_filter["False"]["queryVector"], fake_embed(ATTR_TEXT))
        for stage in atlas.calls:
            self.assertEqual(stage["filter"]["user_id"], USER)
            self.assertEqual(stage["filter"]["attributes.category"], "shirt")
        self.assertEqual([(m["name"], m["score"]) for m in matches], [("Quick-added button-up", 1.0), ("Oxford", 0.77)])
        self.assertEqual(set(matches[0]), {"score", "id", "name", "attributes", "status"})

    def test_merge_sorts_and_keeps_k(self):
        atlas = FakeAtlas(described=[doc("d1", 0.9), doc("d2", 0.5)], undescribed=[doc("u1", 0.95), doc("u2", 0.6)])
        matches, _ = self.run_find(atlas, k=3)
        self.assertEqual([m["name"] for m in matches], ["u1", "d1", "u2"])
        self.assertTrue(all(s["limit"] == 3 for s in atlas.calls))

    def test_no_description_embeds_once(self):
        atlas = FakeAtlas(described=[doc("d", 0.8)], undescribed=[doc("u", 0.9)])
        matches, embed = self.run_find(atlas, attrs=ATTRS)
        self.assertEqual(embed.call_count, 1)
        self.assertEqual({tuple(s["queryVector"]) for s in atlas.calls}, {tuple(fake_embed(ATTR_TEXT))})
        self.assertEqual(len(matches), 2)

    def test_empty_attributes(self):
        atlas = FakeAtlas()
        matches, embed = self.run_find(atlas, attrs={"category": None})
        self.assertEqual(matches, [])
        self.assertEqual(embed.call_count, 0)
        self.assertEqual(atlas.calls, [])

    def test_vector_search_without_flag_filter(self):
        atlas = FakeAtlas(described=[doc("d", 0.8)])
        with mock.patch.object(c, "closet_items", atlas):
            c.vector_search(USER, [1.0], None, 1)
        self.assertEqual(atlas.calls[0]["filter"], {"user_id": USER})


class DuplicateFlagTest(unittest.TestCase):
    def test_ingest_duplicates_use_both_vectors(self):
        atlas = FakeAtlas(undescribed=[doc("Quick-added button-up", 0.97)])
        cand = {"attributes": ATTRS, "description": "A black button-up shirt with a pointed collar."}
        with mock.patch.object(ingest, "embed_many", side_effect=lambda ts: [fake_embed(t) for t in ts]), \
                mock.patch.object(c, "closet_items", atlas), \
                mock.patch.object(ingest, "thresholds", return_value={"duplicate_similarity": 0.93}):
            ingest.flag_closet_duplicates(USER, [cand])
        self.assertEqual(cand["likely_duplicate_of"]["name"], "Quick-added button-up")
        self.assertEqual(cand["likely_duplicate_of"]["score"], 0.97)
        undescribed = next(s for s in atlas.calls if s["filter"]["has_description"] is False)
        self.assertEqual(undescribed["queryVector"], fake_embed(ATTR_TEXT))


if __name__ == "__main__":
    unittest.main()
