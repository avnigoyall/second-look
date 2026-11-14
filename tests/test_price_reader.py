"""Offline tests for the Claude price tier (backend/price_reader.py, POST /read-price). Claude is mocked."""
import time
import unittest
from types import SimpleNamespace
from unittest import mock

import anthropic
import httpx2
from pydantic import ValidationError

from backend import models as m
from backend import price_reader as pr

WINDOWS = [
    "Relaxed Linen Shirt Size S M L Add to cart",
    "You may also like Linen Shorts $29.90 Linen Pants $49.90",
    "Relaxed Linen Shirt Was $59.90 Now $39.90 Member price $35.90 Free shipping over $50",
]


def tool_reply(price_text, reason="sale price next to the title"):
    block = SimpleNamespace(type="tool_use", name="report_price", input={"price_text": price_text, "reason": reason})
    return SimpleNamespace(content=[block], stop_reason="tool_use")


def fake_client(create):
    """A client whose with_options(...).messages.create runs `create`."""
    messages = SimpleNamespace(create=mock.Mock(side_effect=create))
    client = SimpleNamespace(messages=messages)
    client.with_options = mock.Mock(return_value=client)
    return client


class ReadPriceTest(unittest.TestCase):
    def run_with(self, create, windows=WINDOWS):
        client = fake_client(create)
        with mock.patch.dict("os.environ", {"CLAUDE_MODEL": "test-model"}), \
                mock.patch.object(pr, "claude_client", return_value=client):
            started = time.monotonic()
            result = pr.read_price("https://shop.test/p/linen-shirt", "Relaxed Linen Shirt", windows)
            return result, time.monotonic() - started, client

    def test_found_verbatim(self):
        (price, text), _, client = self.run_with(lambda **_: tool_reply("$39.90"))
        self.assertEqual((price, text), (39.9, "$39.90"))
        kwargs = client.messages.create.call_args.kwargs
        self.assertEqual(kwargs["tool_choice"], {"type": "tool", "name": "report_price"})
        self.assertNotIn("temperature", kwargs)
        self.assertIn("[window 3]", kwargs["messages"][0]["content"])
        self.assertIn("Relaxed Linen Shirt", kwargs["messages"][0]["content"])
        client.with_options.assert_called_once_with(timeout=pr.TIMEOUT_S)

    def test_number_not_in_text(self):
        (price, text), _, _ = self.run_with(lambda **_: tool_reply("$42.00"))
        self.assertEqual((price, text), (None, None))

    def test_reformatted_number_is_rejected(self):
        # The page says "$39.90". "39.9" and "$ 39.90" are not verbatim, so they are not trusted.
        for said in ("39.9", "$ 39.90", "USD 39.90"):
            (price, _), _, _ = self.run_with(lambda said=said, **_: tool_reply(said))
            self.assertIsNone(price, said)

    def test_part_of_a_longer_number_is_rejected(self):
        # "9.90" occurs only inside "$39.90", "$29.90" and so on, never as its own amount.
        (price, _), _, _ = self.run_with(lambda **_: tool_reply("9.90"))
        self.assertIsNone(price)

    def test_null_reply(self):
        (price, text), _, _ = self.run_with(lambda **_: tool_reply(None, "no clear price"))
        self.assertEqual((price, text), (None, None))

    def test_text_without_digits_is_rejected(self):
        (price, _), _, _ = self.run_with(lambda **_: tool_reply("Free"))
        self.assertIsNone(price)

    def test_sdk_timeout_returns_null(self):
        def create(**_):
            raise anthropic.APITimeoutError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
        (price, text), elapsed, client = self.run_with(create)
        self.assertEqual((price, text), (None, None))
        self.assertEqual(client.messages.create.call_count, 1)  # no retry
        self.assertLess(elapsed, 1)

    def test_slow_call_gives_up_at_the_deadline(self):
        def create(**_):
            time.sleep(1.0)
            return tool_reply("$39.90")
        with mock.patch.object(pr, "TIMEOUT_S", 0.2):
            (price, text), elapsed, _ = self.run_with(create)
        self.assertEqual((price, text), (None, None))
        self.assertLess(elapsed, 0.8)

    def test_api_error_and_missing_tool_return_null(self):
        def boom(**_):
            raise RuntimeError("500 from the API")
        self.assertEqual(self.run_with(boom)[0], (None, None))
        self.assertEqual(self.run_with(lambda **_: SimpleNamespace(content=[], stop_reason="end_turn"))[0], (None, None))

    def test_no_model_configured_returns_null(self):
        with mock.patch.dict("os.environ", {"CLAUDE_MODEL": ""}):
            self.assertEqual(pr.read_price("https://shop.test/p", "Shirt", WINDOWS), (None, None))

    def test_empty_windows_skip_the_call(self):
        (result, _, client) = self.run_with(lambda **_: tool_reply("$1"), windows=["", "  "])
        self.assertEqual(result, (None, None))
        client.messages.create.assert_not_called()


class ParsePriceTest(unittest.TestCase):
    def test_same_rules_as_price_js(self):
        cases = [("$29.90", 29.9), ("$1,299.99", 1299.99), ("1.299,99 €", 1299.99), ("€39,90", 39.9),
                 ("1,299", 1299), ("1.299", 1299), ("$20 - $30", 20), ("USD 49", 49), ("£ 12.5", 12.5),
                 ("1 299,00 kr", 1299), ("Free", None), ("", None), (None, None), ("0", 0)]
        for text, want in cases:
            self.assertEqual(pr.parse_price(text), want, text)


class ReadPriceRequestTest(unittest.TestCase):
    def test_valid(self):
        body = m.ReadPriceRequest(url="https://shop.test/p", name="Shirt", windows=["$39.90"])
        self.assertEqual(body.windows, ["$39.90"])

    def test_limits(self):
        bad = [
            {"windows": []},
            {"windows": ["   "]},
            {"windows": ["x" * 2001]},
            {"windows": ["$1"] * 25},
            {"windows": ["x" * 1900] * 5},  # 9500 characters in total
            {"windows": ["$1"], "name": "n" * 301},
        ]
        for extra in bad:
            with self.assertRaises(ValidationError, msg=str(extra)[:60]):
                m.ReadPriceRequest(url="https://shop.test/p", **extra)


if __name__ == "__main__":
    unittest.main()
