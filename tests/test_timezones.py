"""API datetimes carry a UTC offset, so the dashboard shows the viewer's local time.

Offline: no database calls (backend.db only builds the client), no network.

Run from the project root:  python -m unittest discover tests
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bson  # noqa: E402

from backend import db  # noqa: E402
from backend import models as m  # noqa: E402
from backend.services import closet_ingest  # noqa: E402
from backend.timeutil import as_utc  # noqa: E402

NAIVE = datetime(2026, 11, 14, 18, 57, 21)
UTC = NAIVE.replace(tzinfo=timezone.utc)
EST = timezone(timedelta(hours=-5))


class MongoClientTest(unittest.TestCase):
    def test_client_returns_aware_utc_datetimes(self):
        options = db.client.codec_options
        self.assertTrue(options.tz_aware)
        stored = bson.encode({"created_at": NAIVE})  # what is in Atlas: UTC milliseconds, no zone
        doc = bson.decode(stored, codec_options=options)
        self.assertEqual(doc["created_at"], UTC)
        self.assertEqual(doc["created_at"].utcoffset(), timedelta(0))


class ResponseModelTest(unittest.TestCase):
    def test_as_utc(self):
        self.assertEqual(as_utc(NAIVE), UTC)
        self.assertEqual(as_utc(datetime(2026, 11, 14, 13, 57, 21, tzinfo=EST)), UTC)

    def verdict(self, created_at):
        return m.Verdict(id="v", item=m.Item(name="Jeans"), verdict="good_buy", reason="r", action="none",
                         feedback="none", created_at=created_at)

    def test_naive_and_aware_values_serialize_with_z(self):
        for value in (NAIVE, UTC, datetime(2026, 11, 14, 13, 57, 21, tzinfo=EST),
                      "2026-11-14T18:57:21", "2026-11-14T18:57:21+00:00", "2026-11-14T18:57:21.123Z"):
            out = self.verdict(value).model_dump(mode="json")["created_at"]
            self.assertTrue(out.startswith("2026-11-14T18:57:21") and out.endswith("Z"), (value, out))

    def test_every_response_datetime_field(self):
        closet = m.ClosetItem(id="c", user_id="u", source="snap", name="Tee", created_at=NAIVE)
        user = m.User(id="u", email="test_polish@secondlook.test", profile_type="new", created_at=NAIVE)
        transfer = m.SavingsTransfer(amount_cents=1, description="d", at=NAIVE)
        trend = m.TrendSummary(id="t", name="Barrel jeans", origin="seed", first_seen=NAIVE)
        for model, field in ((closet, "created_at"), (user, "created_at"), (transfer, "at"), (trend, "first_seen")):
            self.assertIn(f'"{field}":"2026-11-14T18:57:21Z"', model.model_dump_json())


class ArithmeticTest(unittest.TestCase):
    """Code that compares stored datetimes with now() works with the aware values Mongo now returns."""

    def test_ingest_stale_check_with_aware_and_naive(self):
        old = datetime.now(timezone.utc) - timedelta(hours=1)
        for started in (old, old.replace(tzinfo=None)):
            self.assertTrue(closet_ingest.is_stale({"status": "running", "created_at": started}))
        self.assertFalse(closet_ingest.is_stale({"status": "running", "created_at": datetime.now(timezone.utc)}))


if __name__ == "__main__":
    unittest.main()
