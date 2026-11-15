"""Offline tests for "bought later": an earlier skip of the same item is superseded and, for Nessie users, its money
moves back from Savings to Checking (backend/services/money.py on_buy / reverse_skip, nessie.undo_transfer).
Reuses the in-memory Nessie fake from tests/test_nessie.py. No network, no database writes.

Run from the project root:  python -m unittest tests.test_nessie_reversal
"""
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bson import ObjectId  # noqa: E402

from backend.services import money, nessie  # noqa: E402
from tests.test_nessie import NessieTest  # noqa: E402


class ReversalTest(NessieTest):
    def setUp(self):
        super().setUp()
        patches = [
            mock.patch.object(money, "_recent_skips", self.recent_skips),
            mock.patch.object(money, "_supersede", self.supersede),
            mock.patch.object(money, "_save_reversal", self.save_reversal),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    # fake database helpers
    def recent_skips(self, user_id, exclude_id, since):
        docs = [v for v in self.verdicts.values() if v["user_id"] == user_id and v["_id"] != exclude_id
                and v.get("action") == "skipped" and "superseded_by" not in v and v.get("created_at", nessie.now()) >= since]
        return sorted(docs, key=lambda v: v.get("created_at", nessie.now()), reverse=True)

    def supersede(self, vid, by_id):
        doc = self.verdicts[vid]
        if "superseded_by" in doc:
            return False
        doc["superseded_by"] = by_id
        return True

    def save_reversal(self, vid, record):
        self.verdicts[vid]["reversal"] = record

    # helpers
    def skipped_and_settled(self, price=38.0, url="https://www.pacsun.com/p/tube-top.html?color=010"):
        user, skipped = self.make(price=price)
        skipped["item"].update({"url": url, "store": "Pacsun", "name": "Ribbed Tube Top"})
        skipped["created_at"] = nessie.now() - timedelta(days=3)
        transfer, _ = money.on_skip(user, skipped, self.background)
        self.assertEqual(transfer["status"], "done")
        self.fake.sweep()  # the skip transfer settles into Savings
        return user, skipped

    def new_purchase(self, user, url="https://pacsun.com/p/tube-top.html", name="Ribbed Tube Top", store="Pacsun"):
        bought = {"_id": ObjectId(), "user_id": user["_id"], "action": "none", "created_at": nessie.now(),
                  "item": {"name": name, "price": 38.0, "url": url, "store": store}}
        self.verdicts[bought["_id"]] = bought
        return bought

    # tests
    def test_buying_the_same_page_later_moves_the_money_back(self):
        user, skipped = self.skipped_and_settled()
        self.assertEqual(self.balances(user)["savings_cents"], 3800)
        bought = self.new_purchase(user)  # same page: host case, www and query string differ
        self.assertEqual(money.on_buy(user, bought, self.background), 3800)
        self.assertEqual(skipped["superseded_by"], bought["_id"])
        self.background.run()
        self.fake.sweep()
        self.assertEqual(skipped["reversal"]["status"], "done")
        b = self.balances(user)
        self.assertEqual((b["savings_cents"], b["checking_cents"]), (0, 150000))
        undo = self.fake.of("transfer")[-1]
        self.assertTrue(nessie.is_undo_for(undo["description"], skipped["_id"]))

    def test_matches_by_name_and_store_without_urls(self):
        user, skipped = self.skipped_and_settled(url=None)
        skipped["item"]["url"] = None
        bought = self.new_purchase(user, url=None, name="ribbed tube top ", store="PACSUN")
        self.assertEqual(money.on_buy(user, bought, self.background), 3800)

    def test_different_item_is_left_alone(self):
        user, skipped = self.skipped_and_settled()
        bought = self.new_purchase(user, url="https://pacsun.com/p/other-top.html")
        self.assertIsNone(money.on_buy(user, bought, self.background))
        self.assertNotIn("superseded_by", skipped)
        self.assertEqual(self.background.tasks, [])

    def test_double_buy_moves_money_back_once(self):
        user, skipped = self.skipped_and_settled()
        first, second = self.new_purchase(user), self.new_purchase(user)
        self.assertEqual(money.on_buy(user, first, self.background), 3800)
        self.assertIsNone(money.on_buy(user, second, self.background))  # already superseded
        self.background.run()
        undos = [t for t in self.fake.of("transfer") if nessie.is_undo_for(t["description"], skipped["_id"])]
        self.assertEqual(len(undos), 1)

    def test_retry_does_not_repeat_an_undo_that_landed(self):
        user, skipped = self.skipped_and_settled()
        money.on_buy(user, self.new_purchase(user), self.background)
        self.fake.timeout_transfer = "landed"  # the undo lands but the response times out
        self.background.run()
        self.assertEqual(skipped["reversal"]["status"], "failed")
        skipped["reversal"]["status"] = "pending"  # a later retry
        money.reverse_skip(skipped["_id"])
        self.assertEqual(skipped["reversal"]["status"], "done")
        undos = [t for t in self.fake.of("transfer") if nessie.is_undo_for(t["description"], skipped["_id"])]
        self.assertEqual(len(undos), 1)

    def test_waits_for_the_skip_to_settle_before_moving_it_back(self):
        user, skipped = self.make(price=38.0)
        skipped["item"].update({"url": "https://pacsun.com/p/tube-top.html", "store": "Pacsun"})
        skipped["created_at"] = nessie.now()
        money.on_skip(user, skipped, self.background)  # still pending: Savings cannot spend it yet
        money.on_buy(user, self.new_purchase(user), self.background)
        self.background.run()  # undo hits insufficient_funds, waits for the skip to settle, then succeeds
        self.assertEqual(skipped["reversal"]["status"], "done")

    def test_no_transfer_or_local_user_only_supersedes(self):
        user, skipped = self.make(price=38.0, enabled=False)
        skipped["item"].update({"url": "https://pacsun.com/p/tube-top.html", "store": "Pacsun"})
        skipped["created_at"] = nessie.now()
        bought = self.new_purchase(user)
        self.assertIsNone(money.on_buy(user, bought, self.background))
        self.assertEqual(skipped["superseded_by"], bought["_id"])  # leaves money_kept and the goal's local sum
        self.assertEqual(self.background.tasks, [])

    def test_same_verdict_skipped_then_bought(self):
        user, skipped = self.skipped_and_settled()
        self.assertEqual(money.on_buy(user, dict(skipped), self.background), 3800)  # doc read before the update
        self.background.run()
        self.assertEqual(skipped["reversal"]["status"], "done")

    def test_never_raises_into_the_purchase(self):
        user, skipped = self.skipped_and_settled()
        with mock.patch.object(money, "_recent_skips", side_effect=RuntimeError("db down")):
            self.assertIsNone(money.on_buy(user, self.new_purchase(user), self.background))


class SameItemTest(unittest.TestCase):
    def test_url_normalization(self):
        self.assertEqual(money.normalize_url("https://WWW.Pacsun.com/p/a.html?x=1#y"), "pacsun.com/p/a.html")
        self.assertEqual(money.normalize_url("https://pacsun.com/p/a/"), "pacsun.com/p/a")
        self.assertIsNone(money.normalize_url(None))
        self.assertIsNone(money.normalize_url("not a url"))


def load_tests(loader, standard_tests, pattern):
    """Only this file's tests: ReversalTest inherits NessieTest's setup, not its tests (they run in test_nessie)."""
    suite = unittest.TestSuite()
    own = [name for name in ReversalTest.__dict__ if name.startswith("test_")]
    suite.addTests(ReversalTest(name) for name in own)
    suite.addTests(loader.loadTestsFromTestCase(SameItemTest))
    return suite


if __name__ == "__main__":
    unittest.main()
