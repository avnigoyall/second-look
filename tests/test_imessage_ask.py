"""Offline tests for iMessage account chat: backend/services/account_context.py, imessage_ask.py and
POST /imessage/ask. MongoDB is replaced by small in-memory collections and Claude is mocked. No network.

Run from the project root:  python -m unittest discover tests
"""
import logging
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bson import ObjectId  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import impact  # noqa: E402
from backend import main  # noqa: E402
from backend.routers import imessage as router  # noqa: E402
from backend.services import account_context as ac  # noqa: E402
from backend.services import imessage_ask  # noqa: E402

logging.getLogger("secondlook.imessage_ask").setLevel(logging.ERROR)
logging.getLogger("secondlook.account_context").setLevel(logging.ERROR)

SECRET = "test-agent-secret"
T0 = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


class Cursor(list):
    def sort(self, key, direction=1):
        return Cursor(sorted(self, key=lambda d: d.get(key) or T0, reverse=direction == -1))

    def limit(self, n):
        return Cursor(self[:n])


class FakeCollection:
    """Equality filters only, which is all these code paths use. Projections are ignored."""

    def __init__(self, docs=None):
        self.docs = list(docs or [])

    @staticmethod
    def matches(doc, filt):
        return all(doc.get(k) == v for k, v in filt.items())

    def find(self, filt=None, projection=None):
        return Cursor(d for d in self.docs if self.matches(d, filt or {}))

    def find_one(self, filt=None, *args, **kwargs):
        return next(iter(self.find(filt)), None)

    def insert_one(self, doc):
        doc.setdefault("_id", ObjectId())
        self.docs.append(doc)
        return mock.Mock(inserted_id=doc["_id"])


def make_world():
    alice = {"_id": ObjectId(), "email": "alice@secondlook.test", "phone": "+15550001111",
             "preference_note": "Prefers neutral colors", "created_at": T0 - timedelta(days=3),
             "settings": {"auto_add_purchases": False}}
    bob = {"_id": ObjectId(), "email": "bob@secondlook.test", "phone": "+15550002222", "created_at": T0}
    closet = FakeCollection([
        {"user_id": alice["_id"], "name": "Black slim jeans", "status": "owned",
         "attributes": {"category": "jeans", "color_family": "black"}, "created_at": T0},
        {"user_id": alice["_id"], "name": "White tee", "attributes": {"category": "tee", "color_family": "white"},
         "created_at": T0 + timedelta(minutes=1)},  # no status counts as owned
        {"user_id": alice["_id"], "name": "Blue wide-leg jeans", "status": "incoming",
         "attributes": {"category": "jeans", "color_family": "blue"}, "created_at": T0 + timedelta(minutes=2)},
        {"user_id": bob["_id"], "name": "Bob secret leather jacket", "status": "owned",
         "attributes": {"category": "jacket", "color_family": "brown"}, "created_at": T0},
    ])
    verdicts = FakeCollection([
        {"user_id": alice["_id"], "item": {"name": "Cargo jeans", "price": 49.9, "store": "Zara"},
         "attributes": {"category": "jeans"}, "verdict": "have_this_look", "reason": "You own black slim jeans.",
         "action": "skipped", "created_at": T0 + timedelta(hours=1)},
        {"user_id": alice["_id"], "item": {"name": ac.IMESSAGE_PHOTO_NAME, "price": None},
         "attributes": {"category": "dress", "color_family": "red"}, "verdict": "micro_trend",
         "reason": "Red bubble dresses are a short trend.", "action": "none", "channel": "imessage",
         "ownership": "no", "created_at": T0 + timedelta(hours=2)},
        {"user_id": bob["_id"], "item": {"name": "Bob secret boots", "price": 999.0}, "verdict": "good_buy",
         "reason": "Bob reason", "action": "skipped", "attributes": {"category": "boots"},
         "created_at": T0 + timedelta(hours=3)},
    ])
    messages = FakeCollection([
        {"user_id": alice["_id"], "direction": "in", "kind": "text", "text": "what jeans do I have?",
         "created_at": T0 + timedelta(hours=4)},
        {"user_id": alice["_id"], "direction": "out", "kind": "answer", "text": "Black slim jeans.",
         "created_at": T0 + timedelta(hours=4, seconds=2)},
        {"user_id": bob["_id"], "direction": "in", "kind": "text", "text": "Bob secret question",
         "created_at": T0 + timedelta(hours=5)},
    ])
    return alice, bob, closet, verdicts, messages


class WorldTest(unittest.TestCase):
    def setUp(self):
        self.alice, self.bob, self.closet, self.verdicts, self.messages = make_world()
        self.users = FakeCollection([self.alice, self.bob])
        for p in (mock.patch.object(ac, "closet_items", self.closet),
                  mock.patch.object(ac, "verdicts", self.verdicts),
                  mock.patch.object(ac, "imessage_messages", self.messages),
                  mock.patch.object(impact, "verdicts", self.verdicts),
                  mock.patch.object(router, "users", self.users),
                  mock.patch.object(router, "verdicts", self.verdicts),
                  mock.patch.object(router, "_indexes_ready", True),
                  mock.patch.dict(os.environ, {"IMESSAGE_AGENT_SECRET": SECRET, "NESSIE_API_KEY": ""})):
            p.start()
            self.addCleanup(p.stop)


class ContextTest(WorldTest):
    def test_who_and_closet(self):
        ctx = ac.build_context(self.alice)
        self.assertEqual(ctx["who"]["email"], "alice@secondlook.test")
        self.assertEqual(ctx["who"]["preference_note"], "Prefers neutral colors")
        self.assertEqual(ctx["who"]["member_since"], "2026-11-07")
        self.assertFalse(ctx["who"]["auto_add_purchases"])
        self.assertFalse(ctx["who"]["bank_savings_linked"])
        self.assertEqual(ctx["who"]["closet_size"], 3)
        c = ctx["closet"]
        self.assertEqual((c["total"], c["owned"], c["incoming"]), (3, 2, 1))
        self.assertEqual(c["by_category"], {"jeans": 2, "tee": 1})
        self.assertEqual([i["name"] for i in c["items"]], ["Black slim jeans", "White tee", "Blue wide-leg jeans"])
        self.assertEqual(c["items"][2]["status"], "incoming")
        self.assertEqual(c["items"][1]["status"], "owned")
        self.assertNotIn("password_hash", str(ctx))

    def test_closet_list_is_capped(self):
        many = [{"user_id": self.alice["_id"], "name": f"Tee {n}", "attributes": {"category": "tee"},
                 "created_at": T0 + timedelta(days=1, minutes=n)} for n in range(ac.CLOSET_LIST_MAX + 5)]
        self.closet.docs.extend(many)
        c = ac.build_context(self.alice)["closet"]
        self.assertEqual(c["total"], ac.CLOSET_LIST_MAX + 8)
        self.assertEqual(len(c["items"]), ac.CLOSET_LIST_MAX)
        self.assertEqual(c["items"][-1]["name"], f"Tee {ac.CLOSET_LIST_MAX + 4}")  # newest kept
        self.assertIsNotNone(c["note"])

    def test_savings_local(self):
        s = ac.build_context(self.alice)["savings"]
        self.assertEqual(s["money_kept"], "$49.90")
        self.assertEqual(s["items_skipped"], 1)
        self.assertEqual(s["co2e_avoided_kg"], 33.4)  # jeans, sourced figure in config/impact.json
        self.assertIsNone(s["bank"])

    def test_savings_nessie(self):
        self.alice["nessie_enabled"] = True
        summary = {"checking_cents": 145010, "savings_cents": 4990, "source": "nessie",
                   "transfers": [{"amount_cents": 4990, "description": "Second Look skip x",
                                  "at": "2026-11-14T10:00:00Z"}]}
        with mock.patch.object(ac, "nessie_active", return_value=True), \
                mock.patch.object(ac, "savings_summary", return_value=summary):
            ctx = ac.build_context(self.alice)
        bank = ctx["savings"]["bank"]
        self.assertEqual(bank["checking_balance"], "$1,450.10")
        self.assertEqual(bank["savings_balance"], "$49.90")
        self.assertEqual(bank["recent_transfers_to_savings"], [{"amount": "$49.90", "date": "2026-11-14"}])
        self.assertTrue(ctx["who"]["bank_savings_linked"])

    def test_savings_nessie_down_says_unavailable(self):
        local = {"checking_cents": None, "savings_cents": 4990, "transfers": [], "source": "local"}
        with mock.patch.object(ac, "nessie_active", return_value=True), \
                mock.patch.object(ac, "savings_summary", return_value=local):
            s = ac.build_context(self.alice)["savings"]
        self.assertEqual(s["bank"], {"unavailable": True})
        self.assertEqual(s["money_kept"], "$49.90")

    def test_recent_verdicts(self):
        vs = ac.build_context(self.alice)["recent_verdicts"]
        self.assertEqual(len(vs), 2)
        self.assertEqual(vs[0]["item"], "Red dress (photo sent over iMessage)")  # newest first, named from attributes
        self.assertEqual(vs[0]["channel"], "imessage")
        self.assertIs(vs[0]["already_owned"], False)
        self.assertIsNone(vs[0]["price"])
        self.assertEqual(vs[1], {"item": "Cargo jeans", "price": "$49.90", "store": "Zara", "verdict": "have_this_look",
                                 "reason": "You own black slim jeans.", "action": "skipped", "channel": "extension",
                                 "date": "2026-11-10"})

    def test_history_oldest_first_and_capped(self):
        for n in range(12):
            self.messages.insert_one({"user_id": self.alice["_id"], "direction": "in", "kind": "text",
                                      "text": f"msg {n}", "created_at": T0 + timedelta(days=1, minutes=n)})
        turns = ac.build_context(self.alice)["recent_conversation"]
        self.assertEqual(len(turns), ac.TURNS_MAX)
        self.assertEqual(turns[0]["text"], "msg 4")
        self.assertEqual(turns[-1]["text"], "msg 11")
        self.assertEqual(turns[-1]["from"], "shopper")

    def test_never_includes_another_users_data(self):
        text = str(ac.build_context(self.alice))
        for secret in ("Bob", "bob@", "jacket", "boots", "999", "+15550002222"):
            self.assertNotIn(secret, text)
        self.assertIn("Bob secret", str(ac.build_context(self.bob)))

    def test_broken_section_degrades(self):
        with mock.patch.object(ac, "recent_verdicts", side_effect=RuntimeError("atlas down")):
            ctx = ac.build_context(self.alice)
        self.assertEqual(ctx["recent_verdicts"], {"unavailable": True})
        self.assertEqual(ctx["closet"]["total"], 3)

    def test_record_turn_caps_text_and_never_raises(self):
        ac.record_turn(self.alice, "+15550001111", "in", "text", "x" * 5000)
        self.assertEqual(len(self.messages.docs[-1]["text"]), ac.TEXT_MAX)
        with mock.patch.object(self.messages, "insert_one", side_effect=RuntimeError("down")):
            ac.record_turn(self.alice, "+15550001111", "out", "answer", "hi")  # no exception


class CleanTest(unittest.TestCase):
    def test_strips_markdown_and_caps(self):
        self.assertEqual(imessage_ask.clean("**Black jeans** and `tee`\n- one\n# Head"),
                         "Black jeans and tee\none\nHead")
        self.assertEqual(imessage_ask.clean("Jeans — two pairs"), "Jeans - two pairs")
        long = "This is a sentence. " * 60
        out = imessage_ask.clean(long)
        self.assertLessEqual(len(out), imessage_ask.ANSWER_MAX)
        self.assertTrue(out.endswith("."))


class AskEndpointTest(WorldTest):
    def setUp(self):
        super().setUp()
        self.client = TestClient(main.app)

    def post(self, body, secret=SECRET):
        return self.client.post("/imessage/ask", json=body, headers={"X-Agent-Secret": secret})

    def test_answer_path_records_both_turns(self):
        seen = {}

        def fake_claude(context, question):
            seen["context"], seen["question"] = context, question
            return "You have **Black slim jeans** and Blue wide-leg jeans (incoming)."

        before = len(self.messages.docs)
        with mock.patch.object(imessage_ask, "call_claude", side_effect=fake_claude):
            r = self.post({"phone": "(555) 000-1111", "text": "  what about jeans?  "})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"message": "You have Black slim jeans and Blue wide-leg jeans (incoming)."})
        self.assertEqual(seen["question"], "what about jeans?")
        self.assertEqual(seen["context"]["who"]["email"], "alice@secondlook.test")
        self.assertEqual(seen["context"]["recent_conversation"][-1]["text"], "Black slim jeans.")  # not this turn
        new = self.messages.docs[before:]
        self.assertEqual([(d["direction"], d["kind"]) for d in new], [("in", "text"), ("out", "answer")])
        self.assertTrue(all(d["user_id"] == self.alice["_id"] and d["phone"] == "+15550001111" for d in new))

    def test_claude_failure_returns_fallback(self):
        for error in (TimeoutError("slow"), RuntimeError("500")):
            with mock.patch.object(imessage_ask, "call_claude", side_effect=error):
                r = self.post({"phone": "+15550001111", "text": "how much have I saved?"})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["message"], imessage_ask.FALLBACK)
        with mock.patch.object(imessage_ask, "call_claude", return_value="   "):
            r = self.post({"phone": "+15550001111", "text": "hi"})
            self.assertEqual(r.json()["message"], imessage_ask.FALLBACK)

    def test_unlinked_phone_404(self):
        r = self.post({"phone": "+15550009999", "text": "hi"})
        self.assertEqual((r.status_code, r.json()["detail"]), (404, "phone_not_linked"))

    def test_bad_secret_401(self):
        with mock.patch.object(imessage_ask, "call_claude") as claude:
            r = self.post({"phone": "+15550001111", "text": "hi"}, secret="wrong")
        self.assertEqual(r.status_code, 401)
        claude.assert_not_called()

    def test_claude_request_shape(self):
        fake = mock.Mock()
        fake.with_options.return_value.messages.create.return_value = mock.Mock(
            content=[mock.Mock(type="text", text="Hi.")], stop_reason="end_turn")
        with mock.patch.object(imessage_ask, "claude_client", return_value=fake), \
                mock.patch.dict(os.environ, {"CLAUDE_MODEL": "claude-haiku-5-5"}):
            self.assertEqual(imessage_ask.call_claude({"who": {}}, "hello"), "Hi.")
        kwargs = fake.with_options.return_value.messages.create.call_args.kwargs
        self.assertEqual(kwargs["thinking"], {"type": "disabled"})
        self.assertEqual(kwargs["output_config"], {"effort": "low"})
        self.assertNotIn("temperature", kwargs)
        self.assertEqual(fake.with_options.call_args.kwargs["timeout"], imessage_ask.TIMEOUT_S)


if __name__ == "__main__":
    unittest.main()
