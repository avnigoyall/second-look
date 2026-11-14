"""Offline tests for Part 6: Skip it moves money through Nessie (backend/services/nessie.py and money.py).

Nessie is an in-memory fake behind httpx.MockTransport that behaves like the hosted API: everything is
created pending, settle only moves settle_at, a sweep applies the money, and a pending deposit is not
available to spend. MongoDB is replaced by dicts. No network, no database writes.

Run from the project root:  python -m unittest discover tests
"""
import contextlib
import itertools
import json
import logging
import os
import sys
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
from bson import ObjectId  # noqa: E402

from backend import main  # noqa: E402
from backend import models as m  # noqa: E402
from backend.services import money, nessie  # noqa: E402

logging.getLogger("secondlook.money").setLevel(logging.ERROR)
logging.getLogger("secondlook.nessie").setLevel(logging.ERROR)

FAKE_KEY = "nsk_test_do_not_leak_123"


class FakeNessie:
    """Just enough of Nessie v2 for these tests. Knobs: down, timeout_transfer ("landed" or "lost")."""

    def __init__(self):
        self.ids = itertools.count(1)
        self.customers, self.accounts, self.txs = {}, {}, []
        self.down = False
        self.timeout_transfer = None
        self.calls = []

    def new_id(self, prefix):
        return f"{prefix}_{next(self.ids)}"

    def add_account(self, customer_id, kind, nickname, cents):
        acct = {"id": self.new_id("acct"), "customer_id": customer_id, "type": kind, "nickname": nickname,
                "balance_cents": cents, "pending_debits_cents": 0, "available_cents": cents,
                "account_number": "1", "rewards": 0, "created_at": "t", "updated_at": "t"}
        self.accounts[acct["id"]] = acct
        return acct

    def user_with_accounts(self, checking_cents=150000):
        cust = self.new_id("cust")
        self.customers[cust] = {"id": cust}
        checking = self.add_account(cust, "Checking", nessie.CHECKING_NICKNAME, checking_cents)
        savings = self.add_account(cust, "Savings", nessie.SAVINGS_NICKNAME, 0)
        return {"_id": ObjectId(), "email": "test_nessie@secondlook.test", "nessie_enabled": True,
                "nessie_customer_id": cust, "nessie_checking_id": checking["id"], "nessie_savings_id": savings["id"]}

    def sweep(self):
        """The hosted settlement sweep: every pending transaction completes."""
        for tx in self.txs:
            if tx["status"] != "pending":
                continue
            tx["status"] = "completed"
            payer = self.accounts[tx["payer_id"]]
            if tx["kind"] == "deposit":
                payer["balance_cents"] += tx["amount_cents"]
            else:
                payer["balance_cents"] -= tx["amount_cents"]
                payer["pending_debits_cents"] -= tx["amount_cents"]
                self.accounts[tx["payee_id"]]["balance_cents"] += tx["amount_cents"]
            for a in self.accounts.values():
                a["available_cents"] = a["balance_cents"] - a["pending_debits_cents"]

    def of(self, kind):
        return [t for t in self.txs if t["kind"] == kind]

    def problem(self, status, kind, detail):
        return httpx.Response(status, json={"type": f"https://nessieisreal.com/problems/{kind}", "title": "x",
                                            "status": status, "detail": detail})

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {FAKE_KEY}"
        assert FAKE_KEY not in str(request.url)
        method, parts = request.method, request.url.path.strip("/").split("/")
        self.calls.append((method, request.url.path))
        if self.down:
            raise httpx.ConnectError("connection refused", request=request)
        body = json.loads(request.content) if request.content else {}
        if parts[0] == "customers" and len(parts) == 1 and method == "POST":
            cust = {"id": self.new_id("cust"), **body}
            self.customers[cust["id"]] = cust
            return httpx.Response(200, json=cust)
        if parts[0] == "customers" and parts[2:] == ["accounts"]:
            if method == "POST":
                return httpx.Response(200, json=self.add_account(parts[1], body["type"], body["nickname"],
                                                                 body["balance_cents"]))
            return httpx.Response(200, json={"items": [a for a in self.accounts.values()
                                                       if a["customer_id"] == parts[1]]})
        if parts[0] == "accounts":
            acct = self.accounts.get(parts[1])
            if acct is None:
                return self.problem(404, "account-not-found", f"Account {parts[1]} does not exist. key {FAKE_KEY}")
            if len(parts) == 2:
                return httpx.Response(200, json=acct)
            if parts[2] == "transactions":
                mine = [t for t in self.txs if acct["id"] in (t["payer_id"], t.get("payee_id"))]
                return httpx.Response(200, json={"items": list(reversed(mine))})
            tx = {"id": self.new_id("txn"), "status": "pending", "medium": "balance", "payer_id": acct["id"],
                  "created_at": datetime.now(timezone.utc).isoformat(), "settle_at": "t", "updated_at": "t",
                  "amount_cents": body["amount_cents"], "description": body.get("description", "")}
            if parts[2] == "deposits":
                self.txs.append({**tx, "kind": "deposit"})
                return httpx.Response(200, json=self.txs[-1])
            if parts[2] == "transfers":
                if acct["available_cents"] < body["amount_cents"]:
                    return self.problem(422, "insufficient_funds", "not enough")
                if self.timeout_transfer == "lost":
                    self.timeout_transfer = None
                    raise httpx.ReadTimeout("timed out", request=request)
                acct["pending_debits_cents"] += body["amount_cents"]
                acct["available_cents"] -= body["amount_cents"]
                self.txs.append({**tx, "kind": "transfer", "payee_id": body["payee_id"]})
                if self.timeout_transfer == "landed":
                    self.timeout_transfer = None
                    raise httpx.ReadTimeout("timed out", request=request)
                return httpx.Response(200, json=self.txs[-1])
        if parts[0] == "transactions":
            tx = next((t for t in self.txs if t["id"] == parts[1]), None)
            if tx is None:
                return self.problem(404, "transaction-not-found", "no")
            if parts[2:] == ["settle"]:
                return httpx.Response(200, json=tx)
            self.sweep()  # polling a transaction: let time pass
            return httpx.Response(200, json=tx)
        return httpx.Response(404, json={"type": "not-found", "title": "x", "status": 404})


class Background:
    def __init__(self):
        self.tasks = []

    def add_task(self, fn, *args):
        self.tasks.append((fn, args))

    def run(self):
        tasks, self.tasks = self.tasks, []
        for fn, args in tasks:
            fn(*args)


class NessieTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeNessie()
        self.verdicts, self.users = {}, {}
        patches = [
            mock.patch.dict(os.environ, {"NESSIE_API_KEY": FAKE_KEY}),
            mock.patch.object(nessie, "_transport", httpx.MockTransport(self.fake.handler)),
            mock.patch.object(nessie, "_sleep", lambda s: None),
            mock.patch.object(money, "_claim", self.claim),
            mock.patch.object(money, "_save_transfer", self.save),
            mock.patch.object(money, "_load_verdict", lambda vid: self.verdicts.get(vid)),
            mock.patch.object(money, "_load_user", lambda uid: self.users.get(uid)),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.background = Background()

    def claim(self, vid, cents):
        doc = self.verdicts[vid]
        before = {**doc}
        if doc.get("transfer") and doc["transfer"]["status"] != "failed":
            return None, before
        doc["transfer"] = {"status": "pending", "id": None, "amount_cents": cents, "at": nessie.now()}
        return doc["transfer"], before

    def save(self, vid, record):
        self.verdicts[vid]["transfer"] = record

    def make(self, price=59.99, checking_cents=150000, enabled=True):
        user = self.fake.user_with_accounts(checking_cents)
        user["nessie_enabled"] = enabled
        self.users[user["_id"]] = user
        verdict = {"_id": ObjectId(), "user_id": user["_id"], "item": {"name": "Barrel jeans", "price": price},
                   "action": "skipped"}
        self.verdicts[verdict["_id"]] = verdict
        return user, verdict

    def balances(self, user):
        return nessie.get_balances(user)

    def test_success_moves_price_and_returns_projected_balances(self):
        user, verdict = self.make(price=59.99)
        transfer, balances = money.on_skip(user, verdict, self.background)
        self.assertEqual(transfer, {"status": "done", "amount_cents": 5999})
        self.assertEqual(balances, {"checking_cents": 150000 - 5999, "savings_cents": 5999})
        [tx] = self.fake.of("transfer")
        self.assertEqual(tx["description"], f"Second Look skip {verdict['_id']}")
        self.assertEqual((tx["payer_id"], tx["payee_id"]), (user["nessie_checking_id"], user["nessie_savings_id"]))
        self.assertEqual(self.verdicts[verdict["_id"]]["transfer"]["id"], tx["id"])
        self.assertEqual(self.background.tasks, [])
        self.fake.sweep()  # after settlement the numbers stay the same
        self.assertEqual(self.balances(user), balances)
        self.assertEqual(nessie.list_transfers(user)[0]["amount_cents"], 5999)

    def test_second_click_does_not_transfer_again(self):
        user, verdict = self.make()
        money.on_skip(user, verdict, self.background)
        transfer, _ = money.on_skip(user, verdict, self.background)
        self.assertEqual(transfer["status"], "done")
        self.assertEqual(len(self.fake.of("transfer")), 1)

    def test_timeout_that_landed_is_pending_then_reconciled_without_duplicate(self):
        user, verdict = self.make(price=20)
        self.fake.timeout_transfer = "landed"
        transfer, _ = money.on_skip(user, verdict, self.background)
        self.assertEqual(transfer, {"status": "pending", "amount_cents": 2000})
        self.assertEqual(len(self.background.tasks), 1)
        self.background.run()
        [tx] = self.fake.of("transfer")
        self.assertEqual(self.verdicts[verdict["_id"]]["transfer"]["status"], "done")
        self.assertEqual(self.verdicts[verdict["_id"]]["transfer"]["id"], tx["id"])

    def test_timeout_that_was_lost_is_retried_once(self):
        user, verdict = self.make(price=20)
        self.fake.timeout_transfer = "lost"
        transfer, _ = money.on_skip(user, verdict, self.background)
        self.assertEqual(transfer["status"], "pending")
        self.assertEqual(self.fake.of("transfer"), [])
        self.background.run()
        self.assertEqual(len(self.fake.of("transfer")), 1)
        self.assertEqual(self.verdicts[verdict["_id"]]["transfer"]["status"], "done")

    def test_insufficient_funds_tops_up_then_transfers(self):
        user, verdict = self.make(price=50, checking_cents=1000)
        transfer, balances = money.on_skip(user, verdict, self.background)
        self.assertEqual(transfer["status"], "pending")
        [deposit] = self.fake.of("deposit")
        self.assertEqual(deposit["amount_cents"], 5000 - 1000 + nessie.TOPUP_CENTS)
        self.assertEqual(deposit["description"], f"Second Look top-up {verdict['_id']}")
        self.assertEqual(balances["checking_cents"], 1000 + deposit["amount_cents"])  # pending deposit counted
        self.background.run()
        self.assertEqual(len(self.fake.of("deposit")), 1)
        self.assertEqual(len(self.fake.of("transfer")), 1)
        self.assertEqual(self.verdicts[verdict["_id"]]["transfer"]["status"], "done")
        self.fake.sweep()
        self.assertEqual(self.balances(user), {"checking_cents": 1000 + deposit["amount_cents"] - 5000,
                                               "savings_cents": 5000})

    def test_no_price_moves_nothing(self):
        user, verdict = self.make(price=None)
        transfer, balances = money.on_skip(user, verdict, self.background)
        self.assertEqual(transfer, {"status": "no_price", "amount_cents": None})
        self.assertEqual(balances, {"checking_cents": 150000, "savings_cents": 0})
        self.assertFalse([c for c in self.fake.calls if c[0] == "POST"])
        self.assertEqual(self.background.tasks, [])

    def test_nessie_down_answers_pending_then_failed_and_never_raises(self):
        user, verdict = self.make()
        self.fake.down = True
        transfer, balances = money.on_skip(user, verdict, self.background)
        self.assertEqual(transfer["status"], "pending")
        self.assertIsNone(balances)
        self.background.run()
        record = self.verdicts[verdict["_id"]]["transfer"]
        self.assertEqual(record["status"], "failed")
        self.fake.down = False  # a later click retries a failed transfer, checking for a landed one first
        transfer, _ = money.on_skip(user, verdict, self.background)
        self.assertEqual(transfer["status"], "done")
        self.assertEqual(len(self.fake.of("transfer")), 1)

    def test_local_user_makes_no_nessie_call(self):
        user, verdict = self.make(enabled=False)
        self.assertEqual(money.on_skip(user, verdict, self.background), (None, None))
        self.assertEqual(self.fake.calls, [])
        with mock.patch.dict(os.environ, {"NESSIE_API_KEY": ""}):
            user["nessie_enabled"] = True
            self.assertEqual(money.on_skip(user, verdict, self.background), (None, None))
        self.assertEqual(self.fake.calls, [])

    def test_unprovisioned_user_is_provisioned_in_background_not_on_the_click(self):
        user, verdict = self.make()
        for field in ("nessie_customer_id", "nessie_checking_id", "nessie_savings_id"):
            user[field] = None
        accounts = {**user, "nessie_customer_id": "cust_x", "nessie_checking_id": next(
            a["id"] for a in self.fake.accounts.values() if a["type"] == "Checking"),
            "nessie_savings_id": next(a["id"] for a in self.fake.accounts.values() if a["type"] == "Savings")}
        transfer, balances = money.on_skip(user, verdict, self.background)
        self.assertEqual((transfer["status"], balances), ("pending", None))
        self.assertEqual(self.fake.calls, [])
        with mock.patch.object(nessie, "ensure_accounts", return_value=accounts) as ensure:
            self.background.run()
        ensure.assert_called_once()
        self.assertEqual(self.verdicts[verdict["_id"]]["transfer"]["status"], "done")

    def test_key_is_scrubbed_from_errors(self):
        with self.assertRaises(nessie.NessieError) as ctx:
            nessie.get_account("acct_missing")
        self.assertNotIn(FAKE_KEY, str(ctx.exception))
        self.assertEqual(ctx.exception.problem, "account-not-found")

    def test_action_response_shape(self):
        user, verdict = self.make(price=10)
        with mock.patch.object(main, "own_verdict", return_value=verdict), mock.patch.object(main, "verdicts"):
            skipped = m.ActionResponse(**main.action(m.ActionRequest(verdict_id=str(verdict["_id"]),
                                                                     action="skipped"), self.background, user))
            bought = m.ActionResponse(**main.action(m.ActionRequest(verdict_id=str(verdict["_id"]),
                                                                    action="bought"), self.background, user))
        self.assertEqual(skipped.transfer.model_dump(), {"status": "done", "amount_cents": 1000})
        self.assertEqual(skipped.balances.model_dump(), {"checking_cents": 149000, "savings_cents": 1000,
                                                         "placeholder": False})
        self.assertIsNone(bought.transfer)
        self.assertTrue(bought.balances.placeholder)
        self.assertEqual(len(self.fake.of("transfer")), 1)  # bought made no Nessie call


class FakeUsers:
    """The three Mongo calls ensure_accounts makes, with an atomic claim."""

    def __init__(self, doc):
        self.doc, self.lock = doc, threading.Lock()

    def find_one(self, query):
        with self.lock:
            return dict(self.doc)

    def find_one_and_update(self, query, update):
        with self.lock:
            lock = self.doc.get("nessie_lock")
            if lock is not None and lock >= query["$or"][1]["nessie_lock"]["$lt"]:
                return None
            before = dict(self.doc)
            self.doc.update(update["$set"])
            return before

    def update_one(self, query, update):
        with self.lock:
            self.doc.update(update.get("$set", {}))
            for k in update.get("$unset", {}):
                self.doc.pop(k, None)


class EnsureAccountsTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeNessie()
        for p in (mock.patch.dict(os.environ, {"NESSIE_API_KEY": FAKE_KEY}),
                  mock.patch.object(nessie, "_transport", httpx.MockTransport(self.fake.handler))):
            p.start()
            self.addCleanup(p.stop)

    def test_concurrent_calls_create_one_customer_and_two_accounts(self):
        self.race()

    def test_mongo_claim_alone_is_race_safe(self):
        """Two backend processes do not share the in-process lock: the Mongo claim must hold on its own."""
        with mock.patch.object(nessie, "_local_lock", contextlib.nullcontext()):
            self.race()

    def race(self):
        users = FakeUsers({"_id": ObjectId(), "email": "test_nessie@secondlook.test", "nessie_customer_id": None,
                           "nessie_checking_id": None, "nessie_savings_id": None})
        results = []
        with mock.patch("backend.db.users", users):
            threads = [threading.Thread(target=lambda: results.append(nessie.ensure_accounts(users.doc)))
                       for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            again = nessie.ensure_accounts(users.doc)
        self.assertEqual(len(self.fake.customers), 1)
        self.assertEqual(sorted(a["nickname"] for a in self.fake.accounts.values()),
                         ["Second Look Checking", "Second Look Savings"])
        self.assertEqual({r["nessie_checking_id"] for r in results + [again]}, {users.doc["nessie_checking_id"]})
        self.assertNotIn("nessie_lock", users.doc)
        checking = self.fake.accounts[users.doc["nessie_checking_id"]]
        self.assertEqual(checking["balance_cents"], 150000)

    def test_resumes_after_a_crash_without_duplicating(self):
        cust = "cust_existing"
        self.fake.customers[cust] = {"id": cust}
        existing = self.fake.add_account(cust, "Checking", nessie.CHECKING_NICKNAME, 150000)
        users = FakeUsers({"_id": ObjectId(), "email": "x@y", "nessie_customer_id": cust,
                           "nessie_checking_id": None, "nessie_savings_id": None})
        with mock.patch("backend.db.users", users):
            user = nessie.ensure_accounts(users.doc)
        self.assertEqual(user["nessie_checking_id"], existing["id"])
        self.assertEqual(len(self.fake.customers), 1)
        self.assertEqual(len(self.fake.accounts), 2)


class LocalSavingsTest(unittest.TestCase):
    def test_local_summary_uses_money_kept(self):
        user = {"_id": ObjectId()}
        vid = ObjectId()
        doc = {"_id": vid, "item": {"price": 19.99}, "created_at": datetime(2026, 11, 14, tzinfo=timezone.utc)}
        cursor = mock.MagicMock()
        cursor.sort.return_value.limit.return_value = [doc]
        with mock.patch("backend.db.verdicts") as verdicts:
            verdicts.find.return_value = cursor
            verdicts.aggregate.return_value = [{"total": 19.99}]
            out = m.SavingsResponse(**money.savings_summary(user))
        self.assertEqual(out.source, "local")
        self.assertEqual(out.savings_cents, 1999)
        self.assertIsNone(out.checking_cents)
        self.assertEqual(out.transfers[0].description, f"Second Look skip {vid}")


if __name__ == "__main__":
    unittest.main()
