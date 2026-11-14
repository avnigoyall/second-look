"""Offline tests for /read-page retries (backend/page_reader.py). Gemini is faked; no network."""
import unittest
from types import SimpleNamespace
from unittest import mock

from backend import page_reader as pr


class FakeError(Exception):
    def __init__(self, code):
        super().__init__(f"fake {code}")
        self.code = code


def fake_client(outcomes):
    """A client whose generate_content returns or raises the given outcomes in order."""
    calls = iter(outcomes)

    def generate_content(**_):
        out = next(calls)
        if isinstance(out, Exception):
            raise out
        return out
    return SimpleNamespace(models=SimpleNamespace(generate_content=mock.Mock(side_effect=generate_content)))


OK = SimpleNamespace(parsed=pr.PageItem(name="Linen Blend Overshirt", price=59.0, image_url=None), text="")


class ReadPageRetryTest(unittest.TestCase):
    def run_with(self, outcomes):
        client = fake_client(outcomes)
        with mock.patch.dict("os.environ", {"GEMINI_MODEL": "test-model"}), \
                mock.patch.object(pr, "_get_client", return_value=client), \
                mock.patch.object(pr.time, "sleep") as sleep:
            try:
                return pr.read_page("https://shop.test/p", "Linen Blend Overshirt $59", None), client, sleep
            except pr.ReadPageError as e:
                return e, client, sleep

    def test_busy_429_then_success(self):
        item, client, sleep = self.run_with([FakeError(429), OK])
        self.assertEqual(item.name, "Linen Blend Overshirt")
        self.assertEqual(client.models.generate_content.call_count, 2)
        sleep.assert_called_once_with(2)

    def test_busy_503_twice_then_success(self):
        item, client, sleep = self.run_with([FakeError(503), FakeError(503), OK])
        self.assertEqual(item.price, 59.0)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [2, 4])

    def test_busy_every_time_gives_up(self):
        err, client, _ = self.run_with([FakeError(429)] * pr.BUSY_ATTEMPTS)
        self.assertIsInstance(err, pr.ReadPageError)
        self.assertIn("429", str(err))
        self.assertEqual(client.models.generate_content.call_count, pr.BUSY_ATTEMPTS)

    def test_other_error_is_not_retried(self):
        err, client, sleep = self.run_with([FakeError(400)])
        self.assertIsInstance(err, pr.ReadPageError)
        self.assertEqual(client.models.generate_content.call_count, 1)
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
