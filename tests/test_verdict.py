"""Offline tests for the Part 5 verdict engine (backend/services/verdict.py). Claude, Gemini, embeddings and
MongoDB are all faked. No network, no database writes.

Run from the project root:  python -m unittest discover tests
"""
import logging
import time
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bson import ObjectId  # noqa: E402
from pymongo.errors import PyMongoError  # noqa: E402

from backend import models as m  # noqa: E402
from backend.services import verdict as v  # noqa: E402
from backend.services.embeddings import EmbeddingError  # noqa: E402

logging.getLogger("secondlook.verdict").setLevel(logging.ERROR)  # expected failure logs stay quiet

USER = {"_id": ObjectId(), "email": "test_engine@secondlook.test", "preference_note": None}
JEANS = {"status": "ok", "is_apparel": True, "category": "jeans", "color_family": "black", "fabric": "denim",
         "fabric_confidence": "medium", "cut": "slim", "style": "basic", "description": "Black slim jeans.",
         "target_box_2d": None, "other_garments_visible": None, "target_crop": None}
ITEM = {"name": "Slim black jeans", "image_url": "https://shop.example/jeans.jpg", "price": 60.0}
ID_A, ID_B, ID_C = (str(ObjectId()) for _ in range(3))
PHOTO = "data:image/jpeg;base64,AAAA"


def match(id_, score, name="Black slim jeans", color="black", status="owned"):
    return {"id": id_, "score": score, "name": name, "status": status,
            "attributes": {"category": "jeans", "color_family": color}}


def closet_doc(id_, name="Black slim jeans"):
    return {"_id": ObjectId(id_), "user_id": USER["_id"], "source": "quick_add", "status": "owned", "name": name,
            "image_data": PHOTO, "attributes": {"category": "jeans", "color_family": "black"}, "description": "",
            "created_at": datetime.now(timezone.utc)}


SHORT_TREND = {"name": "Barrel-leg jeans", "category": "jeans", "features": {"cut": "wide", "color": None,
               "fabric": "denim"}, "lifespan_bucket": "short", "lifespan_label": "Vogue calls it one season",
               "source_titles": ["Vogue"]}
LONG_TREND = {**SHORT_TREND, "name": "Straight jeans", "lifespan_bucket": "long"}
TREND_DOC = {"_id": ObjectId(), "name": "Barrel-leg jeans", "category": "jeans", "origin": "seed",
             "lifespan_label": "Vogue calls it one season", "sources": [{"title": "Vogue", "url": "https://v.example"}]}


def reply(verdict, reason="Short reason naming your black slim jeans.", ids=(), trend=None, confidence=0.8):
    return {"verdict": verdict, "reason": reason, "matched_closet_ids": list(ids), "matched_trend_name": trend,
            "confidence": confidence}


class EngineTest(unittest.TestCase):
    """Runs decide() with every outside call patched. Set the fakes before calling run()."""

    def setUp(self):
        self.attrs = dict(JEANS)
        self.matches = []
        self.trends = []
        self.size = 20
        self.replies = []  # each entry: a dict reply, or an exception to raise
        self.calls = []
        self.alternatives = [m.Alternative(name="Straight jeans", url="https://store.example/s", reason="Lasts")]
        patches = [
            mock.patch.object(v.verdict_deps, "understand_for_verdict", side_effect=self.fake_understand),
            mock.patch.object(v, "find_matches", side_effect=self.fake_matches),
            mock.patch.object(v, "get_trends_for_prompt", side_effect=lambda: self.trends),
            mock.patch.object(v, "closet_size", side_effect=lambda _uid: self.size),
            mock.patch.object(v, "fetch_image", return_value=b"\xff\xd8jpeg"),
            mock.patch.object(v, "closet_docs", side_effect=lambda ids: {i: closet_doc(i) for i in ids}),
            mock.patch.object(v, "find_trend_doc", side_effect=lambda name: TREND_DOC if name == TREND_DOC["name"]
                              else None),
            mock.patch.object(v.verdict_deps, "alternatives_for_trend", side_effect=lambda t: self.alternatives),
            mock.patch.object(v, "call_claude", side_effect=self.fake_claude),
            mock.patch.object(v, "claude_model", return_value="claude-test"),
            mock.patch.object(v, "thresholds", return_value={"look_similarity": 0.80, "gray_margin": 0.04,
                                                              "require_color_match": True}),
            mock.patch.object(v.verdicts, "insert_one", side_effect=AssertionError("save=False must not write")),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def fake_understand(self, item, image_bytes=None):
        self.understand_images = getattr(self, "understand_images", []) + [image_bytes]
        if isinstance(self.attrs, Exception):
            raise self.attrs
        return self.attrs

    def fake_matches(self, user_id, attrs, k):
        if isinstance(self.matches, Exception):
            raise self.matches
        return self.matches

    def fake_claude(self, payload, images, timeout_s):
        self.calls.append({"payload": payload, "images": images, "timeout": timeout_s})
        result = self.replies.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def run_engine(self, user=USER, item=ITEM):
        return v.decide(user, item, save=False)


class ZoneTests(EngineTest):
    def test_forced_zone_allows_only_have_this_look(self):
        self.matches = [match(ID_A, 0.90), match(ID_B, 0.70)]
        self.replies = [reply("have_this_look", ids=[ID_A])]
        resp, rec = self.run_engine()
        self.assertEqual(rec["signals"]["zone"], "forced")
        self.assertEqual(rec["signals"]["allowed"], ["have_this_look"])
        self.assertEqual(resp.verdict, "have_this_look")
        self.assertEqual(resp.decided_by, "code")  # one allowed verdict: code decided, Claude wrote the reason
        self.assertEqual(resp.matched_closet_item.id, ID_A)
        self.assertEqual(resp.matched_closet_items[0].image_data, PHOTO)
        self.assertEqual(self.calls[0]["images"], [])  # text only outside the gray band
        self.assertIsNone(resp.expected_wears)
        self.assertEqual(resp.zone, "forced")

    def test_gray_zone_sends_images_and_lets_claude_decide(self):
        self.matches = [match(ID_A, 0.80), match(ID_B, 0.78), match(ID_C, 0.77)]
        self.replies = [reply("good_buy", reason="Close to your black slim jeans, but the wash reads different.")]
        resp, rec = self.run_engine()
        self.assertEqual(rec["signals"]["zone"], "gray")
        self.assertEqual(rec["signals"]["allowed"], ["have_this_look", "good_buy"])
        self.assertEqual(resp.verdict, "good_buy")
        self.assertEqual(resp.decided_by, "claude")
        images = self.calls[0]["images"]
        self.assertEqual(len(images), 3)  # the item photo plus the top 2 closet photos
        self.assertEqual(self.calls[0]["payload"]["image_labels"][0], "item being added")
        self.assertEqual(resp.matched_closet_items, [])
        self.assertEqual(resp.expected_wears, 60)
        self.assertEqual(resp.cost_per_wear, 1.0)
        self.assertTrue(resp.wears_estimated)

    def test_gray_zone_prefers_target_crop_over_item_photo(self):
        self.attrs = {**JEANS, "target_crop": "data:image/jpeg;base64,CROP"}
        self.matches = [match(ID_A, 0.81)]
        self.replies = [reply("have_this_look", ids=[ID_A])]
        resp, rec = self.run_engine()
        self.assertEqual(self.calls[0]["images"][0]["source"]["data"], "CROP")
        self.assertEqual(rec["target_crop"], "data:image/jpeg;base64,CROP")
        self.assertEqual(resp.verdict, "have_this_look")
        self.assertEqual(resp.decided_by, "claude")

    def test_none_zone_forbids_have_this_look(self):
        self.matches = [match(ID_A, 0.60)]
        self.replies = [reply("good_buy", reason="Nothing in your closet looks quite like these.")]
        resp, rec = self.run_engine()
        self.assertEqual(rec["signals"]["zone"], "none")
        self.assertEqual(rec["signals"]["allowed"], ["good_buy"])
        self.assertEqual(resp.verdict, "good_buy")

    def test_color_mismatch_does_not_count_as_best_match(self):
        self.matches = [match(ID_A, 0.95, color="blue"), match(ID_B, 0.70)]
        self.replies = [reply("good_buy", reason="Nothing in your closet looks quite like these.")]
        _, rec = self.run_engine()
        self.assertEqual(rec["signals"]["best"]["id"], ID_B)
        self.assertEqual(rec["signals"]["zone"], "none")

    def test_unknown_color_is_not_a_mismatch(self):
        self.matches = [match(ID_A, 0.95, color=None)]
        self.replies = [reply("have_this_look", ids=[ID_A])]
        _, rec = self.run_engine()
        self.assertEqual(rec["signals"]["zone"], "forced")

    def test_threshold_is_per_user_category_and_clamped(self):
        self.assertEqual(v.category_threshold({"look_thresholds": {"jeans": 0.85}}, "jeans", {"look_similarity": 0.8}),
                         0.85)
        self.assertEqual(v.category_threshold({"look_thresholds": {"jeans": 0.5}}, "jeans", {"look_similarity": 0.8}),
                         0.70)
        self.assertEqual(v.category_threshold({"look_thresholds": {"jeans": 0.99}}, "jeans", {"look_similarity": 0.8}),
                         0.92)
        self.assertEqual(v.category_threshold({}, "tee", {"look_similarity": 0.8}), 0.80)

    def test_zone_boundaries(self):
        self.assertEqual(v.zone_for(0.84, 0.80, 0.04), "forced")
        self.assertEqual(v.zone_for(0.8399, 0.80, 0.04), "gray")
        self.assertEqual(v.zone_for(0.76, 0.80, 0.04), "gray")
        self.assertEqual(v.zone_for(0.7599, 0.80, 0.04), "none")
        self.assertEqual(v.zone_for(None, 0.80, 0.04), "none")

    def test_micro_trend_allowed_only_with_a_short_trend(self):
        self.trends = [LONG_TREND]
        self.replies = [reply("good_buy", reason="Nothing in your closet looks quite like these.")]
        _, rec = self.run_engine()
        self.assertNotIn("micro_trend", rec["signals"]["allowed"])

        self.trends = [SHORT_TREND, LONG_TREND, {**SHORT_TREND, "name": "Jelly sandals", "category": "shoes"}]
        self.replies = [reply("micro_trend", reason="These fit the barrel-leg jeans trend, likely short-lived.",
                              trend="barrel-leg JEANS")]
        resp, rec = self.run_engine()
        self.assertEqual(rec["signals"]["allowed"], ["micro_trend", "good_buy"])
        self.assertEqual([t["name"] for t in self.calls[-1]["payload"]["trends"]], ["Barrel-leg jeans",
                                                                                     "Straight jeans"])
        self.assertEqual(resp.verdict, "micro_trend")
        self.assertEqual(resp.matched_trend.name, "Barrel-leg jeans")
        self.assertEqual([a.url for a in resp.alternatives], ["https://store.example/s"])
        self.assertEqual(rec["matched_trend_id"], TREND_DOC["_id"])
        self.assertIsNone(resp.expected_wears)


class GuardrailTests(EngineTest):
    def test_verdict_outside_allowed_set_downgrades_with_template_reason(self):
        self.matches = [match(ID_A, 0.60)]
        self.trends = [SHORT_TREND]
        self.replies = [reply("have_this_look", reason="You own this.", ids=[ID_A])]
        resp, rec = self.run_engine()
        self.assertEqual(resp.verdict, "good_buy")  # micro_trend has no valid trend name, so on to good_buy
        self.assertEqual(rec["override"], {"rule": "verdict_not_allowed", "code_said": "micro_trend",
                                           "claude_said": "have_this_look"})
        self.assertEqual(rec["overrides"][1]["rule"], "trend_not_in_list")
        self.assertNotEqual(resp.reason, "You own this.")
        self.assertEqual(resp.decided_by, "code")

    def test_forced_zone_good_buy_is_overridden_to_have_this_look(self):
        self.matches = [match(ID_A, 0.93)]
        self.replies = [reply("good_buy")]
        resp, rec = self.run_engine()
        self.assertEqual(resp.verdict, "have_this_look")
        self.assertEqual(resp.matched_closet_item.id, ID_A)
        self.assertEqual(rec["override"]["code_said"], "have_this_look")
        self.assertEqual(rec["override"]["claude_said"], "good_buy")
        self.assertIn("Black slim jeans", resp.reason)

    def test_unknown_matched_ids_are_dropped(self):
        self.matches = [match(ID_A, 0.82), match(ID_B, 0.79)]
        self.replies = [reply("have_this_look", ids=["not-an-id", ID_B, ID_B])]
        resp, rec = self.run_engine()
        self.assertEqual([i.id for i in resp.matched_closet_items], [ID_B])
        self.assertEqual(rec["overrides"], [])

    def test_have_this_look_without_valid_ids_downgrades_in_gray(self):
        self.matches = [match(ID_A, 0.80)]
        self.replies = [reply("have_this_look", ids=["made-up"])]
        resp, rec = self.run_engine()
        self.assertEqual(resp.verdict, "good_buy")
        self.assertEqual(rec["override"]["rule"], "no_valid_closet_ids")
        self.assertEqual(resp.matched_closet_items, [])

    def test_have_this_look_without_ids_in_forced_zone_uses_best_match(self):
        self.matches = [match(ID_A, 0.90)]
        self.replies = [reply("have_this_look", ids=[])]
        resp, rec = self.run_engine()
        self.assertEqual(resp.verdict, "have_this_look")
        self.assertEqual(resp.matched_closet_item.id, ID_A)
        self.assertEqual(rec["override"]["rule"], "no_valid_closet_ids")

    def test_micro_trend_needs_a_listed_short_trend(self):
        self.trends = [SHORT_TREND, LONG_TREND]
        self.replies = [reply("micro_trend", trend="Straight jeans")]  # listed, but not short
        resp, rec = self.run_engine()
        self.assertEqual(resp.verdict, "good_buy")
        self.assertEqual(rec["override"]["rule"], "trend_not_in_list")
        self.assertIsNone(resp.matched_trend)
        self.assertEqual(resp.alternatives, [])

    def test_reason_over_30_words_gets_a_template(self):
        self.matches = [match(ID_A, 0.60)]
        self.trends = [SHORT_TREND]  # two allowed verdicts, so Claude's choice counts
        self.replies = [reply("good_buy", reason=" ".join(["word"] * 31))]
        resp, rec = self.run_engine()
        self.assertEqual(resp.verdict, "good_buy")
        self.assertLessEqual(len(resp.reason.split()), 30)
        self.assertEqual(rec["override"]["rule"], "reason_too_long")
        self.assertEqual(resp.decided_by, "claude")  # the verdict stood, only the reason was replaced

    def test_small_closet_without_match_must_say_i_dont_see_this(self):
        self.size, self.matches = 4, []
        self.replies = [reply("good_buy", reason="You have no jeans like this.")]
        resp, rec = self.run_engine()
        self.assertTrue(resp.closet_small)
        self.assertEqual(resp.zone, "none")
        self.assertTrue(resp.reason.startswith("I don't see this in your closet"))
        self.assertEqual(rec["override"]["rule"], "missing_not_seen_phrase")
        self.assertIn("I don't see this in your closet", self.calls[0]["payload"]["note"])

    def test_small_closet_reason_with_the_phrase_stands(self):
        self.size, self.matches = 4, []
        self.replies = [reply("good_buy", reason="I don’t see this in your closet, and it works with basics.")]
        resp, rec = self.run_engine()
        self.assertEqual(rec["overrides"], [])
        self.assertIn("works with basics", resp.reason)

    def test_word_cap_is_30(self):
        self.assertEqual(v.REASON_MAX_WORDS, 30)
        long_name = {**match(ID_A, 0.6, name=" ".join(["Very"] * 20) + " jeans"),
                     "attributes": {"category": "jeans", "color_family": "black", "cut": "regular",
                                    "fabric": "cotton", "style": "basic"}}
        item = {**JEANS, "color_family": "blue", "cut": "wide", "fabric": "denim", "style": "streetwear"}
        for verdict in v.PRIORITY:
            for named in (match(ID_A, 0.6), long_name, None):
                for small in (True, False):
                    text = v.template_reason(verdict, match=named, attrs=item, trend_name="Barrel-leg jeans",
                                             closet_small=small, has_matches=named is not None, zone="none")
                    self.assertLessEqual(len(text.split()), 30, text)


JEANS_BLUE_WIDE = {**JEANS, "color_family": "blue", "cut": "wide", "description": "Blue wide leg jeans."}


def black_regular_jeans(id_, score):
    return {**match(id_, score, name="Black jeans"),
            "attributes": {"category": "jeans", "color_family": "black", "cut": "regular", "fabric": "denim",
                           "style": "basic"}}


class WordingTests(EngineTest):
    """The reason names the closest closet item and how it differs; it never says "I don't see this" when
    there is a candidate match, and a have_this_look reason commits."""

    def test_small_closet_below_threshold_match_names_it_without_not_seen(self):
        # The real case from Nov 14: 9-item closet, Black jeans at 0.613 with a different color.
        self.attrs, self.size = dict(JEANS_BLUE_WIDE), 9
        self.matches = [black_regular_jeans(ID_A, 0.613)]
        self.replies = [reply("good_buy", reason="Closest in your closet: your black jeans. These are blue and wide "
                                                 "leg, so they add something new.")]
        resp, rec = self.run_engine()
        self.assertEqual((resp.verdict, resp.zone), ("good_buy", "none"))
        self.assertEqual(rec["overrides"], [])
        self.assertNotIn("don't see", resp.reason)
        closest = self.calls[0]["payload"]["closest_match"]
        self.assertEqual(closest["name"], "Black jeans")
        self.assertFalse(closest["same_color_family"])
        self.assertEqual([d["field"] for d in closest["differences"]], ["color_family", "cut"])
        self.assertNotIn("note", self.calls[0]["payload"])

    def test_not_seen_while_naming_a_closet_item_is_replaced(self):
        self.attrs, self.size = dict(JEANS_BLUE_WIDE), 9
        self.matches = [black_regular_jeans(ID_A, 0.613)]
        self.replies = [reply("good_buy", reason="I don't see this in your closet. Your black jeans are a different "
                                                 "color and cut, so these add something new to your wardrobe.")]
        resp, rec = self.run_engine()
        self.assertEqual(rec["override"], {"rule": "not_seen_with_match", "code_said": "good_buy",
                                           "claude_said": "good_buy"})
        self.assertEqual(resp.reason, "Closest in your closet: Black jeans. These are blue and wide leg, "
                                      "so they add something new.")
        self.assertEqual(resp.verdict, "good_buy")

    def test_good_buy_that_does_not_name_the_closest_item_gets_a_template(self):
        self.attrs, self.size = dict(JEANS_BLUE_WIDE), 9
        self.matches = [black_regular_jeans(ID_A, 0.613)]
        self.replies = [reply("good_buy", reason="A versatile pair of jeans that pairs with most tops.")]
        resp, rec = self.run_engine()
        self.assertEqual(rec["override"]["rule"], "closest_item_not_named")
        self.assertTrue(resp.reason.startswith("Closest in your closet: Black jeans."))

    def test_truly_empty_category_says_i_dont_see_this(self):
        self.attrs, self.size, self.matches = dict(JEANS_BLUE_WIDE), 9, []
        self.replies = [reply("good_buy", reason="These blue wide leg jeans are an easy everyday pick.")]
        resp, rec = self.run_engine()
        self.assertIsNone(self.calls[0]["payload"]["closest_match"])
        self.assertEqual(rec["override"]["rule"], "missing_not_seen_phrase")
        self.assertTrue(resp.reason.startswith("I don't see this in your closet"))

    def test_hedged_have_this_look_is_replaced_with_a_committed_reason(self):
        # The real gray-band case: Claude picked have_this_look but wrote "it may be a lookalike".
        tee = {**JEANS, "category": "tee", "color_family": "black", "cut": "regular", "fabric": "cotton",
               "style": "streetwear"}
        self.attrs = tee
        black_tee = {**match(ID_A, 0.77, name="Black tee"),
                     "attributes": {"category": "tee", "color_family": "black", "cut": "regular",
                                    "fabric": "cotton", "style": "basic"}}
        self.matches = [black_tee]
        self.replies = [reply("have_this_look", ids=[ID_A], reason="You already have a plain black crew neck tee. "
                              "This one differs mainly by its bold front graphic, so it may be a lookalike.")]
        resp, rec = self.run_engine()
        self.assertEqual(resp.verdict, "have_this_look")
        self.assertEqual(rec["override"], {"rule": "hedged_have_this_look", "code_said": "have_this_look",
                                           "claude_said": "have_this_look"})
        self.assertEqual(resp.reason, "This gives the same look as your Black tee. "
                                      "The streetwear style is the only difference.")
        self.assertIsNone(v.HEDGE_WORDS.search(resp.reason))
        self.assertEqual(resp.decided_by, "claude")  # the verdict stood, only the reason was replaced

    def test_committed_have_this_look_stands(self):
        self.matches = [match(ID_A, 0.81)]
        self.replies = [reply("have_this_look", ids=[ID_A], reason="Same look as your black slim jeans; only the "
                                                                   "wash is a touch lighter.")]
        resp, rec = self.run_engine()
        self.assertEqual(rec["overrides"], [])
        self.assertIn("only the wash", resp.reason)

    def test_have_this_look_must_name_the_matched_item(self):
        self.matches = [match(ID_A, 0.81)]
        self.replies = [reply("have_this_look", ids=[ID_A], reason="You already own this look.")]
        resp, rec = self.run_engine()
        self.assertEqual(rec["override"]["rule"], "matched_item_not_named")
        self.assertIn("Black slim jeans", resp.reason)

    def test_fallback_good_buy_names_the_closest_item(self):
        self.attrs = dict(JEANS_BLUE_WIDE)
        self.matches = [black_regular_jeans(ID_A, 0.61)]
        self.replies = [RuntimeError("down"), RuntimeError("down")]
        resp, _ = self.run_engine()
        self.assertEqual(resp.decided_by, "fallback")
        self.assertTrue(resp.reason.startswith("Closest in your closet: Black jeans."))

    def test_names_item_is_loose_about_wording(self):
        self.assertTrue(v.names_item("Same look as your black crew neck tee.", "Uniqlo crew neck short sleeve tee"))
        self.assertTrue(v.names_item("Your white tees already cover this.", "White tee"))
        self.assertFalse(v.names_item("A versatile pick.", "Black jeans"))


class FallbackTests(EngineTest):
    def test_timeout_then_retry_succeeds(self):
        self.matches = [match(ID_A, 0.60)]
        self.replies = [TimeoutError("slow"), reply("good_buy", reason="Nothing in your closet looks like these.")]
        resp, rec = self.run_engine()
        self.assertEqual([c["timeout"] for c in self.calls], [6.0, 4.0])
        self.assertEqual(resp.decided_by, "code")
        self.assertEqual(len(rec["signals"]["claude_errors"]), 1)
        self.assertEqual(rec["model"], "claude-test")

    def test_timeout_twice_gives_code_only_fallback(self):
        self.matches = [match(ID_A, 0.90)]
        self.replies = [TimeoutError("slow"), TimeoutError("slow again")]
        resp, rec = self.run_engine()
        self.assertEqual(resp.decided_by, "fallback")
        self.assertEqual(resp.verdict, "have_this_look")
        self.assertEqual(resp.matched_closet_item.id, ID_A)
        self.assertIn("Black slim jeans", resp.reason)
        self.assertEqual(len(rec["signals"]["claude_errors"]), 2)

    def test_claude_timeouts_are_cut_to_the_deadline(self):
        self.matches = [match(ID_A, 0.60)]
        self.replies = [TimeoutError("slow"), TimeoutError("slow again")]
        with mock.patch.object(v, "DEADLINE_S", 3.0):
            resp, rec = self.run_engine()
        self.assertEqual([c["timeout"] for c in self.calls], [3.0, 3.0])
        self.assertEqual(resp.decided_by, "fallback")

    def test_no_claude_attempt_without_time_left(self):
        self.matches = [match(ID_A, 0.60)]
        with mock.patch.object(v, "DEADLINE_S", 1.0):
            resp, rec = self.run_engine()
        self.assertEqual(self.calls, [])
        self.assertEqual(resp.decided_by, "fallback")
        self.assertEqual(resp.verdict, "good_buy")
        self.assertIn("skipped", rec["signals"]["claude_errors"][0])

    def test_slow_attributes_end_in_code_only_verdict(self):
        def slow_understand(item, image_bytes=None):
            time.sleep(0.3)
            return JEANS
        with mock.patch.object(v, "ATTRIBUTES_DEADLINE_S", 0.05), \
                mock.patch.object(v.verdict_deps, "understand_for_verdict", side_effect=slow_understand):
            resp, rec = self.run_engine()
        self.assertEqual(rec["signals"]["path"], "attributes_failed")
        self.assertEqual(resp.verdict, "good_buy")
        self.assertEqual(resp.decided_by, "code")
        self.assertEqual(self.calls, [])

    def test_slow_photo_is_skipped_for_text_only_attributes(self):
        def slow_fetch(url):
            time.sleep(0.3)
            return b"\xff\xd8jpeg"
        self.replies = [reply("good_buy", reason="Nothing in your closet looks quite like these.")]
        with mock.patch.object(v, "IMAGE_WAIT_S", 0.05), mock.patch.object(v, "fetch_image", side_effect=slow_fetch):
            resp, rec = self.run_engine()
        self.assertEqual(self.understand_images, [None])
        self.assertEqual(rec["signals"]["path"], "matched")
        self.assertEqual(resp.verdict, "good_buy")

    def test_photo_is_fetched_once_and_reused(self):
        self.matches = [match(ID_A, 0.80)]  # gray band without a target crop: Claude gets the item photo
        self.replies = [reply("good_buy", reason="Close to your black slim jeans, but the wash reads different.")]
        resp, _ = self.run_engine()
        self.assertEqual(v.fetch_image.call_count, 1)
        self.assertEqual(self.understand_images, [b"\xff\xd8jpeg"])
        self.assertEqual(self.calls[0]["images"][0]["source"]["media_type"], "image/jpeg")

    def test_fallback_in_gray_band_is_conservative(self):
        self.matches = [match(ID_A, 0.77)]  # gray, but below t_c
        self.replies = [RuntimeError("down"), RuntimeError("down")]
        resp, _ = self.run_engine()
        self.assertEqual(resp.decided_by, "fallback")
        self.assertEqual(resp.verdict, "good_buy")

    def test_fallback_micro_trend_needs_matching_features(self):
        self.attrs = {**JEANS, "cut": "wide"}
        self.trends = [SHORT_TREND]
        self.replies = [RuntimeError("down"), RuntimeError("down")]
        resp, _ = self.run_engine()
        self.assertEqual(resp.verdict, "micro_trend")
        self.assertIn("likely short-lived", resp.reason)
        self.assertEqual(resp.matched_trend.name, "Barrel-leg jeans")

    def test_attributes_failed_is_code_only(self):
        self.attrs = {"status": "attributes_failed", "is_apparel": None, "target_crop": None}
        resp, rec = self.run_engine()
        self.assertEqual(self.calls, [])
        self.assertEqual((resp.verdict, resp.decided_by, resp.zone), ("good_buy", "code", None))
        self.assertIsNone(rec["attributes"])
        self.assertIsNone(resp.expected_wears)

    def test_attributes_exception_is_code_only(self):
        self.attrs = RuntimeError("Gemini down")
        resp, _ = self.run_engine()
        self.assertEqual((resp.verdict, resp.decided_by), ("good_buy", "code"))

    def test_non_apparel(self):
        self.attrs = {"status": "ok", "is_apparel": False, "category": None, "target_crop": None}
        resp, _ = self.run_engine()
        self.assertEqual(self.calls, [])
        self.assertEqual(resp.verdict, "good_buy")
        self.assertEqual(resp.reason, "This isn't clothing, so there's nothing to compare it with.")
        self.assertEqual(resp.decided_by, "code")
        self.assertIsNone(resp.expected_wears)

    def test_embedding_error_means_no_match_and_no_ownership_talk(self):
        self.matches = EmbeddingError("embedding down")
        self.replies = [reply("good_buy", reason="Nothing about this stands out against current trends.")]
        resp, rec = self.run_engine()
        self.assertIsNone(resp.zone)
        self.assertEqual(rec["signals"]["allowed"], ["good_buy"])
        self.assertIn("Say nothing about what the shopper owns", self.calls[0]["payload"]["note"])
        self.assertEqual(rec["signals"]["errors"], ["matches: EmbeddingError"])

    def test_atlas_error_means_no_match(self):
        self.matches = PyMongoError("atlas down")
        self.size = None
        self.replies = [RuntimeError("down"), RuntimeError("down")]
        resp, rec = self.run_engine()
        self.assertEqual((resp.verdict, resp.decided_by, resp.zone), ("good_buy", "fallback", None))
        self.assertFalse(resp.closet_small)
        self.assertNotIn("closet", resp.reason)
        self.assertEqual(rec["signals"]["errors"], ["matches: PyMongoError"])


class SaveTests(EngineTest):
    def test_save_inserts_the_verdict_doc(self):
        self.matches = [match(ID_A, 0.90)]
        self.replies = [reply("have_this_look", ids=[ID_A])]
        inserted = ObjectId()
        with mock.patch.object(v.verdicts, "insert_one", return_value=mock.Mock(inserted_id=inserted)) as ins:
            resp, rec = v.decide(USER, ITEM, save=True)
        self.assertEqual(resp.verdict_id, str(inserted))
        doc = ins.call_args.args[0]
        for key in ("attributes", "signals", "decided_by", "override", "model", "latency_ms", "target_crop"):
            self.assertIn(key, doc)
        self.assertEqual(doc["matched_closet_item_id"], ObjectId(ID_A))
        self.assertEqual(doc["signals"]["t_c"], 0.80)
        self.assertEqual(doc["signals"]["margin"], 0.04)

    def test_unsaved_response_validates(self):
        self.replies = [reply("good_buy", reason="Nothing in your closet looks quite like these.")]
        resp, _ = self.run_engine(item=m.Item(**ITEM))
        self.assertEqual(resp.verdict_id, "unsaved")


if __name__ == "__main__":
    unittest.main()
