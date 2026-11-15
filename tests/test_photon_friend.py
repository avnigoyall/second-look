"""Offline tests for the Photon friend reply layer: backend/services/friend_reply.py and POST /photon/friend-reply.
Claude and the spend ledger are mocked. No network.

Run from the project root:  python -m unittest tests.test_photon_friend
"""
import io
import logging
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from backend import main
from backend.services import friend_reply as fr

SECRET = "test-agent-secret"
FAKE_KEY = "sk-ant-test-SECRET-should-never-be-logged"
ENV_ON = {fr.FLAG: "1", "ANTHROPIC_API_KEY": FAKE_KEY, "CLAUDE_MODEL": "claude-haiku-5-5",
          "IMESSAGE_AGENT_SECRET": SECRET}
EXISTING = "You already have this look. From your closet: Black slim jeans."


def req(**kw):
    base = {"sender_id": "+15555550100", "text": "thoughts?", "has_image": True,
            "verdict": {"verdict": "have_this_look", "reason": "You already own black slim jeans."},
            "radar": None, "materials_line": None, "closet_context": {"total": 3}, "history": None,
            "existing_reply": EXISTING}
    base.update(kw)
    return base


class FriendReplyTests(unittest.TestCase):
    def setUp(self):
        fr.reset_history()
        self.env = mock.patch.dict(os.environ, ENV_ON)
        self.env.start()
        self.ledger = mock.patch.object(fr, "_ledger", return_value=None)
        self.ledger.start()

    def tearDown(self):
        self.ledger.stop()
        self.env.stop()

    def claude(self, out=None, side_effect=None):
        return mock.patch.object(fr, "call_claude", return_value=out, side_effect=side_effect)

    def test_flag_off_returns_existing_and_skips_claude(self):
        with mock.patch.dict(os.environ, {fr.FLAG: "0"}), self.claude({"lean": "skip", "reply": "x"}) as c:
            out = fr.compose(req())
        self.assertEqual((out["reply"], out["used_fallback"]), (EXISTING, True))
        c.assert_not_called()
        self.assertEqual(fr.history_for("+15555550100"), [])

    def test_error_falls_back(self):
        with self.claude(side_effect=RuntimeError("boom")):
            out = fr.compose(req())
        self.assertEqual((out["reply"], out["used_fallback"]), (EXISTING, True))

    def test_missing_key_falls_back_without_network(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}), \
                mock.patch.object(fr, "claude_client") as client:
            out = fr.compose(req())
        self.assertTrue(out["used_fallback"])
        self.assertEqual(out["reason"], "model_or_key_missing")
        client.assert_not_called()

    def test_timeout_falls_back_within_budget(self):
        def slow(_payload):
            time.sleep(fr.TIMEOUT_S + 1)
            return {"lean": "skip", "reply": "late"}
        with mock.patch.object(fr, "TIMEOUT_S", 0.2), self.claude(side_effect=slow):
            t = time.monotonic()
            out = fr.compose(req())
            elapsed = time.monotonic() - t
        self.assertEqual((out["reply"], out["used_fallback"], out["reason"]), (EXISTING, True, "timeout"))
        self.assertLess(elapsed, 1.0)

    def test_lean_outside_allowed_set_falls_back(self):
        # have_this_look allows skip or try_on_first, never buy
        with self.claude({"lean": "buy", "reply": "Totally get it, go for it."}):
            out = fr.compose(req())
        self.assertTrue(out["used_fallback"])
        self.assertEqual(out["reason"], "lean_not_allowed")

    def test_allowed_leans_follow_the_verdict(self):
        self.assertEqual(fr.allowed_leans({"verdict": "good_buy"}), ("buy", "try_on_first"))
        self.assertNotIn("buy", fr.allowed_leans({"verdict": "micro_trend"}))
        self.assertEqual(fr.allowed_leans({"verdict": "mystery"}), ("try_on_first",))

    def test_verdict_label_is_never_changed(self):
        r = req()
        with self.claude({"lean": "skip", "reply": "Honestly skip it, your black slim jeans do the same job."}) as c:
            out = fr.compose(r)
        self.assertFalse(out["used_fallback"])
        self.assertEqual(r["verdict"]["verdict"], "have_this_look")
        payload = c.call_args.args[0]
        self.assertEqual(payload["verdict"]["verdict"], "have_this_look")
        self.assertEqual(payload["allowed_leans"], ["skip", "try_on_first"])

    def test_radar_line_only_for_known_labels_with_style(self):
        for label in ("micro_trend", "still_active", "established"):
            line = fr.radar_line({"label": label, "matched_style": "barrel jeans"})
            self.assertIn("Bluesky", line)
            self.assertIn("barrel jeans", line)
        self.assertIsNone(fr.radar_line({"label": "unknown", "matched_style": "barrel jeans"}))
        self.assertIsNone(fr.radar_line({"label": "micro_trend", "matched_style": None}))
        self.assertIsNone(fr.radar_line(None))
        with self.claude({"lean": "skip", "reply": "Skip it."}):
            out = fr.compose(req(radar={"label": "unknown", "matched_style": "barrel jeans"}))
        self.assertEqual(out["reply"], "Skip it.")
        with self.claude({"lean": "skip", "reply": "Skip it."}) as c:
            out = fr.compose(req(radar={"label": "micro_trend", "matched_style": "barrel jeans"}))
        self.assertEqual(out["reply"].count("Bluesky"), 1)
        self.assertIn("for a few weeks", out["reply"])
        self.assertEqual(c.call_args.args[0]["max_sentences"], 3)

    def test_reply_trimmed_to_max_sentences(self):
        with self.claude({"lean": "skip", "reply": "One. Two. Three. Four. Five."}):
            self.assertEqual(fr.compose(req())["reply"], "One. Two. Three. Four.")
        with self.claude({"lean": "skip", "reply": "One. Two. Three. Four."}):
            out = fr.compose(req(radar={"label": "micro_trend", "matched_style": "barrel jeans"},
                                 materials_line="Looks like cotton, an estimate."))
        self.assertTrue(out["reply"].startswith("One. Two. Fwiw"))
        self.assertTrue(out["reply"].endswith("Looks like cotton, an estimate."))

    def test_claude_citing_bluesky_itself_falls_back(self):
        with self.claude({"lean": "skip", "reply": "Skip it, Bluesky says it is over."}):
            self.assertTrue(fr.compose(req())["used_fallback"])

    def test_materials_line_included_verbatim_once(self):
        line = "From the photo it looks like mostly cotton, but that is an estimate, not the tag."
        with self.claude({"lean": "skip", "reply": "I'd skip it, you have similar jeans already."}) as c:
            out = fr.compose(req(materials_line=line))
        self.assertFalse(out["used_fallback"])
        self.assertEqual(out["reply"].count(line), 1)
        self.assertTrue(out["reply"].endswith(line))
        self.assertNotIn(line, str(c.call_args.args[0]))  # Claude never sees it, so it cannot reword it
        with self.claude({"lean": "skip", "reply": f"Skip it. {line}"}):
            self.assertEqual(fr.compose(req(materials_line=line))["reply"].count(line), 1)
        with self.claude({"lean": "skip", "reply": "Skip it."}):
            self.assertEqual(fr.compose(req())["reply"], "Skip it.")

    def test_more_than_one_question_falls_back(self):
        with self.claude({"lean": "try_on_first", "reply": "What size? And what occasion?"}):
            self.assertTrue(fr.compose(req())["used_fallback"])

    def test_markdown_is_stripped(self):
        with self.claude({"lean": "skip", "reply": "**Skip it.**\n- you own one"}):
            self.assertEqual(fr.compose(req())["reply"], "Skip it. you own one")

    def test_history_capped_at_six_turns(self):
        with self.claude({"lean": "skip", "reply": "Skip it."}) as c:
            for i in range(5):
                fr.compose(req(text=f"msg {i}"))
        self.assertEqual(len(fr.history_for("+15555550100")), fr.HISTORY_MAX)
        self.assertLessEqual(len(c.call_args.args[0]["history"]), fr.HISTORY_MAX)
        self.assertEqual(fr.history_for("+15555550100")[-2]["text"], "msg 4")

    def test_sender_count_capped(self):
        with mock.patch.object(fr, "SENDERS_MAX", 3), self.claude({"lean": "skip", "reply": "Skip."}):
            for i in range(5):
                fr.compose(req(sender_id=f"+1555555010{i}"))
        self.assertEqual(len(fr._history), 3)

    def test_no_secrets_or_phone_in_logs(self):
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        log = logging.getLogger("secondlook.friend_reply")
        log.addHandler(handler)
        log.setLevel(logging.DEBUG)
        try:
            with self.claude({"lean": "skip", "reply": "Skip it."}):
                fr.compose(req())
            with self.claude(side_effect=RuntimeError("boom")):
                fr.compose(req())
        finally:
            log.removeHandler(handler)
        text = buf.getvalue()
        self.assertIn("friend reply", text)
        self.assertNotIn(FAKE_KEY, text)
        self.assertNotIn("sk-ant", text)
        self.assertNotIn("5555550100", text)

    def test_budget_refusal_falls_back(self):
        class Ledger:
            class BudgetExceeded(Exception):
                pass

            def check_budget(self, provider):
                raise Ledger.BudgetExceeded("cap")
        with mock.patch.object(fr, "_ledger", return_value=Ledger()), \
                mock.patch.object(fr, "claude_client") as client:
            out = fr.compose(req())
        self.assertTrue(out["used_fallback"])
        client.assert_not_called()


class EndpointTests(unittest.TestCase):
    def setUp(self):
        fr.reset_history()
        self.client = TestClient(main.app)

    def post(self, body, secret=SECRET):
        return self.client.post("/photon/friend-reply", json=body, headers={"x-agent-secret": secret})

    def test_requires_agent_secret(self):
        with mock.patch.dict(os.environ, ENV_ON):
            self.assertEqual(self.post(req(), secret="wrong").status_code, 401)

    def test_flag_off_returns_existing(self):
        with mock.patch.dict(os.environ, {**ENV_ON, fr.FLAG: "0"}):
            r = self.post(req())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"reply": EXISTING, "used_fallback": True})

    def test_flag_on_returns_friend_reply(self):
        with mock.patch.dict(os.environ, ENV_ON), mock.patch.object(fr, "_ledger", return_value=None), \
                mock.patch.object(fr, "call_claude", return_value={"lean": "skip", "reply": "Skip it, you own it."}):
            r = self.post(req())
        self.assertEqual(r.json(), {"reply": "Skip it, you own it.", "used_fallback": False})

    def test_model_error_is_not_a_5xx(self):
        with mock.patch.dict(os.environ, ENV_ON), mock.patch.object(fr, "_ledger", return_value=None), \
                mock.patch.object(fr, "call_claude", side_effect=ValueError("bad")):
            r = self.post(req())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"reply": EXISTING, "used_fallback": True})


if __name__ == "__main__":
    unittest.main()
