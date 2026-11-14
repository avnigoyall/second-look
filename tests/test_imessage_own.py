"""Offline tests for the iMessage ownership answer (POST /imessage/own) and the ask_ownership flag that
POST /imessage/analyze stores. A yes or no may only answer the question just asked: the latest iMessage
verdict, when it asked and is still unanswered. MongoDB, the verdict engine and embeddings are faked.

Run from the project root:  python -m unittest discover tests
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bson import ObjectId  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import main  # noqa: E402
from backend.routers import imessage as router  # noqa: E402

SECRET = "test-agent-secret"
PHONE = "+15550003333"
ATTRS = {"category": "tee", "color_family": "black", "fabric": "cotton", "cut": "regular", "style": "basic",
         "fabric_confidence": "medium"}


def _match(doc, filt):
    for key, want in filt.items():
        have = doc.get(key)
        if isinstance(want, dict) and "$gte" in want:
            if have is None or have < want["$gte"]:
                return False
        elif isinstance(want, dict) and "$exists" in want:
            if (key in doc) != want["$exists"]:
                return False
        elif have != want:
            return False
    return True


class FakeCollection:
    """The filters, sort and updates the ownership paths use: equality, $gte, $exists, $set, $unset."""

    def __init__(self, docs=None):
        self.docs = list(docs or [])

    def find_one(self, filt=None, projection=None, sort=None, **kwargs):
        found = [d for d in self.docs if _match(d, filt or {})]
        for key, direction in reversed(sort or []):
            found.sort(key=lambda d: d.get(key), reverse=direction < 0)
        return found[0] if found else None

    def insert_one(self, doc):
        doc.setdefault("_id", ObjectId())
        self.docs.append(doc)
        return SimpleNamespace(inserted_id=doc["_id"])

    def update_one(self, filt, update, **kwargs):
        doc = self.find_one(filt)
        if doc is not None:
            for key, value in update.get("$set", {}).items():
                target = doc
                *parents, leaf = key.split(".")  # dotted paths like item.image_url
                for part in parents:
                    target = target.setdefault(part, {})
                target[leaf] = value
            for key in update.get("$unset", {}):
                doc.pop(key, None)
        return SimpleNamespace(modified_count=int(doc is not None))

    def create_index(self, *args, **kwargs):
        return None


class OwnTest(unittest.TestCase):
    def setUp(self):
        self.user = {"_id": ObjectId(), "email": "test_own@secondlook.test", "phone": PHONE}
        self.users = FakeCollection([self.user])
        self.verdicts = FakeCollection()
        self.closet = FakeCollection()
        self.now = datetime.now(timezone.utc)
        fake_closet_doc = lambda user_id, **kw: {"user_id": user_id, "name": kw["name"], "source": kw["source"]}  # noqa: E731
        for p in (mock.patch.object(router, "users", self.users),
                  mock.patch.object(router, "verdicts", self.verdicts),
                  mock.patch.object(router, "closet_items", self.closet),
                  mock.patch.object(router, "_indexes_ready", True),
                  mock.patch.object(router, "record_turn", lambda *a, **k: None),
                  mock.patch.object(router, "new_closet_doc", side_effect=fake_closet_doc),
                  mock.patch.dict(os.environ, {"IMESSAGE_AGENT_SECRET": SECRET})):
            p.start()
            self.addCleanup(p.stop)
        self.client = TestClient(main.app)

    def verdict(self, minutes_ago, **fields):
        doc = {"_id": ObjectId(), "user_id": self.user["_id"], "channel": "imessage", "attributes": ATTRS,
               "verdict": "good_buy", "created_at": self.now - timedelta(minutes=minutes_ago), **fields}
        self.verdicts.docs.append(doc)
        return doc

    def own(self, owns=True):
        r = self.client.post("/imessage/own", json={"phone": PHONE, "owns": owns}, headers={"X-Agent-Secret": SECRET})
        self.assertEqual(r.status_code, 200)
        return r.json()

    # ---------- the bug: a yes must not answer a verdict that never asked ----------

    def test_yes_after_have_this_look_adds_nothing(self):
        doc = self.verdict(1, verdict="have_this_look", ask_ownership=False)
        out = self.own(True)
        self.assertFalse(out["ok"])
        self.assertIn("Send me a photo", out["message"])
        self.assertEqual(self.closet.docs, [])
        self.assertNotIn("ownership", doc)

    def test_yes_never_reaches_back_past_a_newer_photo(self):
        older = self.verdict(10, ask_ownership=True)  # asked, never answered
        self.verdict(1, verdict="have_this_look", ask_ownership=False)  # newer photo replaces the question
        out = self.own(True)
        self.assertFalse(out["ok"])
        self.assertEqual(self.closet.docs, [])
        self.assertNotIn("ownership", older)

    def test_second_yes_does_not_add_a_duplicate(self):
        self.verdict(5, ask_ownership=True)
        self.assertTrue(self.own(True)["ok"])
        self.assertEqual(len(self.closet.docs), 1)
        out = self.own(True)
        self.assertFalse(out["ok"])
        self.assertEqual(len(self.closet.docs), 1)

    def test_legacy_verdict_without_flag_is_not_answerable(self):
        self.verdict(2)  # stored before the flag existed
        self.assertFalse(self.own(True)["ok"])
        self.assertEqual(self.closet.docs, [])

    # ---------- the normal paths still work ----------

    def test_yes_to_the_question_just_asked_adds_the_item(self):
        doc = self.verdict(2, ask_ownership=True)
        out = self.own(True)
        self.assertTrue(out["ok"])
        self.assertEqual(doc["ownership"], "yes")
        self.assertEqual([(d["name"], d["source"]) for d in self.closet.docs], [("Black tee", "ownership_answer")])

    def test_no_records_the_answer_and_adds_nothing(self):
        doc = self.verdict(2, ask_ownership=True)
        out = self.own(False)
        self.assertTrue(out["ok"])
        self.assertEqual(doc["ownership"], "no")
        self.assertEqual(self.closet.docs, [])

    def test_question_older_than_the_window_is_not_answerable(self):
        self.verdict(25 * 60, ask_ownership=True)
        self.assertFalse(self.own(True)["ok"])

    def test_other_users_question_is_never_answered(self):
        other = {"_id": ObjectId(), "user_id": ObjectId(), "channel": "imessage", "attributes": ATTRS,
                 "verdict": "good_buy", "ask_ownership": True, "created_at": self.now}
        self.verdicts.docs.append(other)
        self.assertFalse(self.own(True)["ok"])
        self.assertNotIn("ownership", other)

    # ---------- /imessage/analyze stores the flag it returns ----------

    def analyze(self, verdict, attributes):
        vid = ObjectId()
        self.tick = getattr(self, "tick", 0) + 1  # each simulated photo is a little newer than the last
        # decide(save=True) stores the verdict with its attributes; the router then adds channel and the flag.
        self.verdicts.docs.append({"_id": vid, "user_id": self.user["_id"], "item": {"image_url": "data:x"},
                                   "attributes": attributes, "verdict": verdict,
                                   "created_at": self.now + timedelta(seconds=self.tick)})
        response = SimpleNamespace(verdict_id=str(vid), verdict=verdict, reason="r", matched_closet_items=[],
                                   matched_closet_item=None, matched_trend=None, alternatives=[], expected_wears=None)
        with mock.patch.object(router, "decide", return_value=(response, {"attributes": attributes})), \
                mock.patch.object(router, "verdict_message", return_value="msg"):
            r = self.client.post("/imessage/analyze", json={"phone": PHONE, "image_base64": "abc"},
                                 headers={"X-Agent-Secret": SECRET})
        self.assertEqual(r.status_code, 200)
        return r.json(), self.verdicts.find_one({"_id": vid})

    def test_analyze_stores_the_flag_it_returns(self):
        for verdict, attributes, expected in (("good_buy", ATTRS, True), ("have_this_look", ATTRS, False),
                                              ("good_buy", None, False)):
            out, doc = self.analyze(verdict, attributes)
            self.assertEqual(out["ask_ownership"], expected)
            self.assertEqual(doc["ask_ownership"], expected)
            self.assertEqual(doc["channel"], "imessage")
            self.assertIsNone(doc["item"]["image_url"])

    def test_analyze_then_yes_end_to_end(self):
        self.analyze("have_this_look", ATTRS)
        self.assertFalse(self.own(True)["ok"])
        self.analyze("good_buy", ATTRS)
        self.assertTrue(self.own(True)["ok"])
        self.assertEqual(len(self.closet.docs), 1)


if __name__ == "__main__":
    unittest.main()
