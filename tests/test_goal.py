"""Offline tests for the savings goal (CONTRACT.md section 15) and scripts/seed_goal_demo.py.

Nessie is the in-memory fake from test_nessie.py behind httpx.MockTransport. MongoDB is replaced by small
in-memory collections. No network, no database reads or writes.

Run from the project root:  python -m unittest discover tests
"""
import copy
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent))
sys.path.insert(0, str(TESTS))

import httpx  # noqa: E402
from bson import ObjectId  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import main  # noqa: E402
from backend import models as m  # noqa: E402
from backend.auth import current_user  # noqa: E402
from backend.services import goal, money, nessie  # noqa: E402
from scripts import seed_goal_demo as seed  # noqa: E402
from test_nessie import FAKE_KEY, Background, FakeNessie  # noqa: E402

T0 = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)


# ---------- in-memory MongoDB ----------

def get_path(doc, path):
    for part in path.split("."):
        if not isinstance(doc, dict) or part not in doc:
            return None
        doc = doc[part]
    return doc


def matches(doc, query):
    return all(get_path(doc, k) == v for k, v in query.items())


class Cursor(list):
    def sort(self, key, direction=None):
        keys = [(key, direction)] if isinstance(key, str) else key
        for k, d in reversed(keys):
            super().sort(key=lambda doc: get_path(doc, k), reverse=d == -1)
        return self

    def limit(self, n):
        return Cursor(self[:n])


class FakeCollection:
    def __init__(self, docs=()):
        self.docs = [copy.deepcopy(d) for d in docs]
        self.writes = 0

    def find_one(self, query, projection=None):
        found = next((d for d in self.docs if matches(d, query)), None)
        return copy.deepcopy(found)

    def find(self, query, projection=None):
        return Cursor(copy.deepcopy(d) for d in self.docs if matches(d, query))

    def insert_one(self, doc):
        self.writes += 1
        doc.setdefault("_id", ObjectId())
        self.docs.append(copy.deepcopy(doc))
        return mock.Mock(inserted_id=doc["_id"])

    def update_one(self, query, update):
        self.writes += 1
        for d in self.docs:
            if matches(d, query):
                d.update(copy.deepcopy(update.get("$set", {})))
                for k in update.get("$unset", {}):
                    d.pop(k, None)
                return

    def aggregate(self, pipeline):
        """Only money_kept's pipeline: $match, then the sum of item.price."""
        docs = [d for d in self.docs if matches(d, pipeline[0]["$match"])]
        return [{"_id": None, "total": sum(get_path(d, "item.price") or 0 for d in docs)}] if docs else []


class ReadOnlyCollection(FakeCollection):
    def insert_one(self, doc):
        raise AssertionError("write in a dry run")

    def update_one(self, query, update):
        raise AssertionError("write in a dry run")


def skip_doc(user_id, created_at, price=10.0, transfer=None, image_url=None, action="skipped", name="Barrel jeans"):
    return {"_id": ObjectId(), "user_id": user_id, "action": action, "created_at": created_at, "transfer": transfer,
            "item": {"name": name, "store": "Zara", "price": price, "image_url": image_url, "url": None}}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


# ---------- API tests ----------

class GoalApiBase(unittest.TestCase):
    """TestClient on the real app. current_user reads the fake users collection, so writes are visible."""

    def setUp(self):
        self.fake = FakeNessie()
        self.users, self.verdicts = FakeCollection(), FakeCollection()
        self.clock = Clock()
        goal.clear_cache()
        patches = [
            mock.patch.dict(os.environ, {"NESSIE_API_KEY": FAKE_KEY}),
            mock.patch.object(nessie, "_transport", httpx.MockTransport(self.fake.handler)),
            mock.patch.object(nessie, "_sleep", lambda s: None),
            mock.patch.object(goal, "_clock", self.clock),
            mock.patch("backend.db.users", self.users),
            mock.patch("backend.db.verdicts", self.verdicts),
            mock.patch.object(main, "verdicts", self.verdicts),
            mock.patch.object(money, "_claim", self.claim),
            mock.patch.object(money, "_save_transfer", self.save),
            mock.patch.object(money, "_load_verdict", lambda vid: self.verdicts.find_one({"_id": vid})),
            mock.patch.object(money, "_load_user", lambda uid: self.users.find_one({"_id": uid})),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(goal.clear_cache)
        self.user_id = None
        main.app.dependency_overrides[current_user] = lambda: self.users.find_one({"_id": self.user_id})
        self.addCleanup(main.app.dependency_overrides.clear)
        self.client = TestClient(main.app)

    def claim(self, vid, cents):
        doc = next(d for d in self.verdicts.docs if d["_id"] == vid)
        before = copy.deepcopy(doc)
        if doc.get("transfer") and doc["transfer"]["status"] != "failed":
            return None, before
        doc["transfer"] = {"status": "pending", "id": None, "amount_cents": cents, "at": nessie.now()}
        return doc["transfer"], before

    def save(self, vid, record):
        self.verdicts.update_one({"_id": vid}, {"$set": {"transfer": record}})

    def local_user(self, **extra):
        doc = {"_id": ObjectId(), "email": "test_goal@secondlook.test", "profile_type": "real",
               "nessie_enabled": False, "created_at": T0, **extra}
        self.users.docs.append(doc)
        self.user_id = doc["_id"]
        return doc

    def nessie_user(self, checking_cents=150000, **extra):
        doc = {**self.fake.user_with_accounts(checking_cents), "profile_type": "real", "created_at": T0, **extra}
        doc["email"] = "test_goal@secondlook.test"
        self.users.docs.append(doc)
        self.user_id = doc["_id"]
        return doc

    def put(self, name="Japan trip", target_cents=200000):
        return self.client.put("/goal", json={"name": name, "target_cents": target_cents})


class GoalEndpointTest(GoalApiBase):
    def test_no_goal(self):
        self.local_user()
        self.assertEqual(self.client.get("/goal").json(), {"goal": None})

    def test_set_and_get_local(self):
        user = self.local_user()
        self.verdicts.docs += [skip_doc(user["_id"], T0, price=300.0), skip_doc(user["_id"], T0, price=40.0)]
        r = self.put()
        self.assertEqual(r.status_code, 200)
        expected = {"goal": {"name": "Japan trip", "target_cents": 200000, "saved_cents": 34000, "percent": 17,
                             "reached": False, "source": "local"}}
        self.assertEqual(r.json(), expected)
        self.assertEqual(self.client.get("/goal").json(), expected)
        stored = self.users.find_one({"_id": user["_id"]})["goal"]
        self.assertEqual(set(stored), {"name", "target_cents", "created_at", "updated_at"})
        self.assertNotIn("reached", stored)
        self.assertEqual(self.fake.calls, [])  # a local user never calls Nessie

    def test_update_keeps_created_at(self):
        user = self.local_user()
        self.put()
        first = self.users.find_one({"_id": user["_id"]})["goal"]
        r = self.put("Ski trip", 50000)
        self.assertEqual(r.json()["goal"]["name"], "Ski trip")
        self.assertEqual(r.json()["goal"]["target_cents"], 50000)
        second = self.users.find_one({"_id": user["_id"]})["goal"]
        self.assertEqual(second["created_at"], first["created_at"])
        self.assertGreaterEqual(second["updated_at"], first["updated_at"])

    def test_delete_is_idempotent_and_leaves_skips_alone(self):
        user = self.nessie_user()
        verdict = skip_doc(user["_id"], T0, price=25.0)
        self.verdicts.docs.append(verdict)
        self.client.post("/action", json={"verdict_id": str(verdict["_id"]), "action": "skipped"})
        self.put()
        verdicts_before = copy.deepcopy(self.verdicts.docs)
        savings_before = goal.read_nessie_savings(user, nessie.Deadline(3))
        posts_before = [c for c in self.fake.calls if c[0] == "POST"]
        for _ in range(2):
            r = self.client.delete("/goal")
            self.assertEqual((r.status_code, r.json()), (200, {"goal": None}))
        self.assertNotIn("goal", self.users.find_one({"_id": user["_id"]}))
        self.assertEqual(self.verdicts.docs, verdicts_before)
        self.assertEqual(goal.read_nessie_savings(user, nessie.Deadline(3)), savings_before)
        self.assertEqual([c for c in self.fake.calls if c[0] == "POST"], posts_before)
        self.assertEqual(self.client.get("/goal").json(), {"goal": None})

    def test_delete_without_goal(self):
        self.local_user()
        self.assertEqual(self.client.delete("/goal").json(), {"goal": None})

    def test_validation(self):
        self.local_user()
        bad = [{"name": "", "target_cents": 1000}, {"name": "   ", "target_cents": 1000},
               {"name": "x" * 41, "target_cents": 1000}, {"name": "Trip", "target_cents": 499},
               {"name": "Trip", "target_cents": 10000001}, {"name": "Trip", "target_cents": 1500.5},
               {"name": "Trip", "target_cents": 1500.0}, {"name": "Trip", "target_cents": "1500"},
               {"name": "Trip", "target_cents": True}, {"name": 5, "target_cents": 1500},
               {"name": "Trip"}, {"target_cents": 1500}]
        for body in bad:
            self.assertEqual(self.client.put("/goal", json=body).status_code, 422, body)
        self.assertEqual(self.client.get("/goal").json(), {"goal": None})
        for name, target in (("x" * 40, 500), ("  Japan trip  ", 10000000), (" " + "y" * 40 + " ", 600)):
            r = self.put(name, target)
            self.assertEqual(r.status_code, 200, (name, target))
            self.assertEqual(r.json()["goal"]["name"], name.strip())
            self.assertEqual(r.json()["goal"]["target_cents"], target)

    def test_reached_and_percent(self):
        user = self.local_user()
        cases = [(1999.99, 99, False), (2000.0, 100, True), (2500.0, 100, True), (0.0, 0, False)]
        for price, percent, reached in cases:
            self.verdicts.docs = [skip_doc(user["_id"], T0, price=price)]
            g = self.put("Japan trip", 200000).json()["goal"]
            self.assertEqual((g["saved_cents"], g["percent"], g["reached"]),
                             (round(price * 100), percent, reached), price)

    def test_requires_auth(self):
        main.app.dependency_overrides.clear()
        for method in ("get", "delete"):
            self.assertEqual(getattr(self.client, method)("/goal").status_code, 401)
        self.assertEqual(self.client.get("/goal/items").status_code, 401)
        self.assertEqual(self.put().status_code, 401)


class GoalNessieTest(GoalApiBase):
    def test_source_nessie_is_projected_savings(self):
        user = self.nessie_user()
        nessie.request("POST", f"/accounts/{user['nessie_checking_id']}/transfers",
                       {"payee_id": user["nessie_savings_id"], "amount_cents": 12345, "description": "x"})
        self.verdicts.docs.append(skip_doc(user["_id"], T0, price=999.0))  # local number differs on purpose
        g = self.put().json()["goal"]
        self.assertEqual((g["saved_cents"], g["source"]), (12345, "nessie"))  # pending incoming credit counted
        self.assertEqual(g["saved_cents"], money.savings_summary(user)["savings_cents"])  # same number as /savings

    def test_nessie_down_falls_back_to_local(self):
        user = self.nessie_user()
        self.verdicts.docs.append(skip_doc(user["_id"], T0, price=12.5))
        self.fake.down = True
        g = self.put().json()["goal"]
        self.assertEqual((g["saved_cents"], g["source"]), (1250, "local"))

    def test_nessie_off_when_key_missing_or_not_enabled(self):
        user = self.nessie_user(nessie_enabled=False)
        self.verdicts.docs.append(skip_doc(user["_id"], T0, price=5.0))
        self.assertEqual(self.put().json()["goal"]["source"], "local")
        with mock.patch.dict(os.environ, {"NESSIE_API_KEY": ""}):
            self.users.update_one({"_id": user["_id"]}, {"$set": {"nessie_enabled": True}})
            self.assertEqual(self.client.get("/goal").json()["goal"]["source"], "local")
        self.assertEqual(self.fake.calls, [])

    def test_cache_hit_within_15_s_and_refresh_after(self):
        user = self.nessie_user()
        self.put()
        calls = len(self.fake.calls)
        self.assertEqual(calls, 2)  # one account read and one transactions read
        self.clock.t += 14.9
        self.client.get("/goal")
        self.client.get("/goal/items")
        self.assertEqual(len(self.fake.calls), calls)  # served from the cache
        nessie.request("POST", f"/accounts/{user['nessie_checking_id']}/transfers",
                       {"payee_id": user["nessie_savings_id"], "amount_cents": 700, "description": "x"})
        calls = len(self.fake.calls)
        self.assertEqual(self.client.get("/goal").json()["goal"]["saved_cents"], 0)  # still cached
        self.clock.t += 0.2
        self.assertEqual(self.client.get("/goal").json()["goal"]["saved_cents"], 700)  # refreshed after 15 s
        self.assertEqual(len(self.fake.calls), calls + 2)

    def test_cache_is_per_user_and_per_account(self):
        a = self.nessie_user()
        goal.remember_savings(a, 111)
        self.assertEqual(goal.cached_savings(a), 111)
        self.assertIsNone(goal.cached_savings({**a, "nessie_savings_id": "acct_new"}))
        self.assertIsNone(goal.cached_savings({**a, "_id": ObjectId()}))
        goal.forget_savings(a["_id"])
        self.assertIsNone(goal.cached_savings(a))

    def test_background_retry_forgets_the_cache(self):
        user = self.nessie_user()
        verdict = skip_doc(user["_id"], T0, price=20.0, transfer={"status": "pending", "id": None,
                                                                   "amount_cents": 2000, "at": T0})
        self.verdicts.docs.append(verdict)
        goal.remember_savings(user, 0)
        money.retry_skip(verdict["_id"])
        self.assertEqual(self.verdicts.find_one({"_id": verdict["_id"]})["transfer"]["status"], "done")
        self.assertIsNone(goal.cached_savings(user))


class ActionGoalTest(GoalApiBase):
    def action(self, verdict, kind="skipped"):
        r = self.client.post("/action", json={"verdict_id": str(verdict["_id"]), "action": kind})
        self.assertEqual(r.status_code, 200)
        return r.json()

    def test_nessie_skip_with_goal_counts_this_transfer_and_fills_the_cache(self):
        user = self.nessie_user()
        self.put("Japan trip", 10000)  # caches savings 0
        verdict = skip_doc(user["_id"], T0, price=100.0)
        self.verdicts.docs.append(verdict)
        out = self.action(verdict)
        self.assertEqual(out["transfer"], {"status": "done", "amount_cents": 10000})
        self.assertEqual(out["goal"], {"name": "Japan trip", "target_cents": 10000, "saved_cents": 10000,
                                       "reached": True})
        self.assertEqual(out["balances"]["savings_cents"], 10000)
        calls = len(self.fake.calls)
        self.assertEqual(self.client.get("/goal").json()["goal"]["saved_cents"], 10000)
        self.assertEqual(len(self.fake.calls), calls)  # /action refreshed the cache

    def test_done_transfer_without_balances_rereads(self):
        user = self.nessie_user()
        self.put("Japan trip", 10000)
        verdict = skip_doc(user["_id"], T0, price=30.0)
        self.verdicts.docs.append(verdict)
        with mock.patch.object(nessie, "get_balances", side_effect=nessie.NessieError("down")):
            out = self.action(verdict)
        self.assertEqual(out["transfer"]["status"], "done")
        self.assertTrue(out["balances"]["placeholder"])
        self.assertEqual(out["goal"]["saved_cents"], 3000)  # cache dropped, fresh read includes the transfer

    def test_skip_without_goal_has_no_goal_field(self):
        for make in (self.local_user, self.nessie_user):
            user = make()
            verdict = skip_doc(user["_id"], T0)
            self.verdicts.docs.append(verdict)
            out = self.action(verdict)
            self.assertNotIn("goal", out)
            self.assertEqual(set(out), {"ok", "balances", "transfer"})

    def test_local_skip_with_goal(self):
        user = self.local_user()
        self.put("Japan trip", 5000)
        self.verdicts.docs.append(skip_doc(user["_id"], T0, price=20.0))
        verdict = skip_doc(user["_id"], T0, price=30.0, action="none")
        self.verdicts.docs.append(verdict)
        out = self.action(verdict)
        self.assertEqual(out, {"ok": True, "transfer": None,
                               "balances": {"checking_cents": None, "savings_cents": None, "placeholder": True},
                               "goal": {"name": "Japan trip", "target_cents": 5000, "saved_cents": 5000,
                                        "reached": True}})
        self.assertEqual(self.fake.calls, [])

    def test_bought_has_no_goal_and_makes_no_nessie_call(self):
        user = self.nessie_user(settings={"auto_add_purchases": False})
        self.put()
        calls = len(self.fake.calls)
        verdict = skip_doc(user["_id"], T0, action="none")
        self.verdicts.docs.append(verdict)
        out = self.action(verdict, "bought")
        self.assertNotIn("goal", out)
        self.assertIsNone(out["transfer"])
        self.assertEqual(len(self.fake.calls), calls)

    def test_goal_error_never_fails_the_skip(self):
        user = self.local_user()
        self.put()
        verdict = skip_doc(user["_id"], T0, action="none")
        self.verdicts.docs.append(verdict)
        with mock.patch.object(main, "action_goal", side_effect=RuntimeError("boom")), \
                self.assertLogs("uvicorn.error", "WARNING"):
            out = self.action(verdict)
        self.assertTrue(out["ok"])
        self.assertNotIn("goal", out)


class GoalItemsTest(GoalApiBase):
    def test_items_newest_first_with_fields(self):
        user = self.local_user()
        other = ObjectId()
        old = skip_doc(user["_id"], T0 - timedelta(days=3), price=19.99,
                       transfer={"status": "done", "id": "txn_1", "amount_cents": 1999, "at": T0},
                       image_url="https://img.example/a.jpg", name="Old tee")
        new = skip_doc(user["_id"], T0 - timedelta(days=1), price=None, name="New jeans")
        pending = skip_doc(user["_id"], T0 - timedelta(days=2), price=50.0,
                           transfer={"status": "pending", "id": None, "amount_cents": 5000, "at": T0})
        self.verdicts.docs += [old, new, pending, skip_doc(user["_id"], T0, action="bought"),
                               skip_doc(user["_id"], T0, action="none"), skip_doc(other, T0)]
        items = self.client.get("/goal/items").json()["items"]  # works without a goal
        self.assertEqual([i["verdict_id"] for i in items], [str(new["_id"]), str(pending["_id"]), str(old["_id"])])
        self.assertEqual(items[0], {"verdict_id": str(new["_id"]), "name": "New jeans", "store": "Zara",
                                    "price_cents": None, "image_url": None, "skipped_at": "2026-11-13T12:00:00Z",
                                    "moved_cents": None})
        self.assertIsNone(items[1]["moved_cents"])  # pending transfer: nothing moved yet
        self.assertEqual(items[2]["moved_cents"], 1999)
        self.assertEqual(items[2]["price_cents"], 1999)
        self.assertEqual(items[2]["image_url"], "https://img.example/a.jpg")
        self.assertTrue(all(i["skipped_at"].endswith("Z") for i in items))

    def test_at_most_100(self):
        user = self.local_user()
        self.verdicts.docs += [skip_doc(user["_id"], T0 - timedelta(minutes=i)) for i in range(130)]
        items = self.client.get("/goal/items").json()["items"]
        self.assertEqual(len(items), 100)
        self.assertEqual(items[0]["skipped_at"], "2026-11-14T12:00:00Z")

    def test_items_make_no_nessie_call(self):
        user = self.nessie_user()
        self.verdicts.docs.append(skip_doc(user["_id"], T0))
        self.client.get("/goal/items")
        self.assertEqual(self.fake.calls, [])


class DatetimeTest(unittest.TestCase):
    def test_goal_item_and_dashboard_datetimes_end_with_z(self):
        naive = datetime(2026, 11, 14, 18, 57, 21)
        item = m.GoalItem(verdict_id="v", name="n", skipped_at=naive)
        self.assertIn('"skipped_at":"2026-11-14T18:57:21Z"', item.model_dump_json())
        verdict = m.Verdict(id="v", item=m.Item(name="n"), verdict="good_buy", reason="r", action="skipped",
                            feedback="none", created_at=naive)
        dashboard = m.DashboardResponse(money_kept=1.0, verdicts=[verdict])
        self.assertIn('"created_at":"2026-11-14T18:57:21Z"', dashboard.model_dump_json())


# ---------- seeding script ----------

ITEMS = [{"name": "Wool coat", "store": "Zara", "price_cents": 29900, "image_url": None, "days_ago": 10},
         {"name": "Down parka", "store": "Uniqlo", "price_cents": 9990, "image_url": "https://img.example/p.jpg",
          "days_ago": 5, "verdict": "micro_trend", "reason": "Short trend."},
         {"name": "Wide leg jeans", "store": "Hollister", "price_cents": 6995, "image_url": None, "days_ago": 1}]
TOTAL = 29900 + 9990 + 6995


class SeedTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeNessie()
        for p in (mock.patch.dict(os.environ, {"NESSIE_API_KEY": FAKE_KEY}),
                  mock.patch.object(nessie, "_transport", httpx.MockTransport(self.fake.handler)),
                  mock.patch.object(nessie, "_sleep", lambda s: None)):
            p.start()
            self.addCleanup(p.stop)
        self.user = {**self.fake.user_with_accounts(150000), "email": "test_goal@secondlook.test"}
        self.old_savings = self.user["nessie_savings_id"]
        self.users, self.verdicts = FakeCollection([self.user]), FakeCollection()
        self.sleeps, self.lines = [], []

    def seeder(self, users=None, verdicts=None):
        return seed.GoalSeeder(users or self.users, verdicts or self.verdicts, sleep=self.sleeps.append,
                               out=self.lines.append, now=lambda: T0)

    def run_seed(self, items=ITEMS):
        s = self.seeder()
        return s.run(s.plan("test_goal@secondlook.test", items))

    def test_items_file_is_valid(self):
        items = seed.load_items()
        self.assertGreaterEqual(len(items), 12)
        self.assertTrue(185000 <= sum(i["price_cents"] for i in items) <= 195000)
        self.assertEqual({i["store"] for i in items}, {"H&M", "Uniqlo", "Zara", "Hollister", "Pacsun"})
        self.assertEqual(len({seed.seed_key(i) for i in items}), len(items))
        for i in items:
            self.assertLessEqual(len(nessie.skip_description(ObjectId(), i["name"])), nessie.DESCRIPTION_MAX)

    def test_dry_run_makes_no_nessie_call_and_no_write(self):
        users, verdicts = ReadOnlyCollection([self.user]), ReadOnlyCollection()
        s = self.seeder(users, verdicts)
        plan = s.plan("test_goal@secondlook.test", seed.load_items())
        s.print_plan(plan, dry_run=True)
        self.assertEqual(self.fake.calls, [])
        text = "\n".join(self.lines)
        self.assertIn("DRY RUN", text)
        self.assertIn("create a new Savings account", text)
        self.assertIn('create "Japan trip"', text)
        self.assertNotIn(FAKE_KEY, text)
        self.assertNotIn(chr(0x2014), text)  # no em dash in printed text

    def test_run_creates_fresh_savings_verdicts_transfers_and_goal(self):
        result = self.run_seed()
        user = self.users.find_one({"_id": self.user["_id"]})
        new_savings = user["nessie_savings_id"]
        self.assertNotEqual(new_savings, self.old_savings)
        self.assertEqual(user["goal_demo_savings_id"], new_savings)
        self.assertEqual(user["goal_demo_previous_savings_id"], self.old_savings)
        account = self.fake.accounts[new_savings]
        self.assertEqual((account["type"], account["nickname"]), ("Savings", nessie.SAVINGS_NICKNAME))
        self.assertEqual(self.fake.accounts[self.old_savings]["balance_cents"], 0)  # old account untouched
        self.assertEqual(result["savings_cents"], TOTAL)
        transfers = self.fake.of("transfer")
        self.assertEqual(len(transfers), 3)
        docs = self.verdicts.docs
        self.assertEqual(len(docs), 3)
        for doc, tx in zip(docs, transfers):
            self.assertTrue(doc["seeded"])
            self.assertEqual(doc["action"], "skipped")
            self.assertLess(doc["created_at"], T0)
            self.assertEqual(tx["description"], f"Second Look skip {doc['_id']}")
            self.assertTrue(nessie.is_skip_for(tx["description"], doc["_id"]))
            self.assertEqual(tx["payee_id"], new_savings)
            self.assertEqual(doc["transfer"]["status"], "done")
            self.assertEqual(doc["transfer"]["id"], tx["id"])
            self.assertEqual(round(doc["item"]["price"] * 100), tx["amount_cents"])
        self.assertEqual(docs[1]["item"]["image_url"], "https://img.example/p.jpg")
        self.assertIsNone(docs[0]["item"]["image_url"])
        self.assertEqual(docs[1]["verdict"], "micro_trend")
        self.assertEqual(user["goal"]["name"], "Japan trip")
        self.assertEqual(user["goal"]["target_cents"], 200000)
        self.assertEqual(self.sleeps, [seed.PACE_S, seed.PACE_S])  # paced between items
        self.assertEqual(self.fake.of("deposit"), [])  # 1500.00 covered 468.85
        # the shape /goal/items returns for seeded verdicts
        items = [m.GoalItem(**goal.item_out(d)) for d in docs]
        self.assertEqual([i.moved_cents for i in items], [29900, 9990, 6995])

    def test_rerun_moves_nothing_and_creates_nothing(self):
        self.run_seed()
        state = (len(self.fake.accounts), len(self.fake.txs), copy.deepcopy(self.verdicts.docs),
                 self.users.find_one({"_id": self.user["_id"]}))
        self.fake.sweep()
        result = self.run_seed()
        self.assertEqual(len(self.fake.accounts), state[0])
        self.assertEqual(len(self.fake.txs), state[1])
        self.assertEqual(self.verdicts.docs, state[2])
        self.assertEqual(self.users.find_one({"_id": self.user["_id"]}), state[3])
        self.assertEqual(result["moved"], 3)
        self.assertEqual(result["savings_cents"], TOTAL)
        plan = self.seeder().plan("test_goal@secondlook.test", ITEMS)
        self.assertEqual(plan["savings"]["action"], "reuse")
        self.assertEqual(plan["goal"]["action"], "keep")

    def test_rerun_after_a_crash_finishes_without_doubles(self):
        real_request = nessie.request
        transfers = []

        def crash_on_second_transfer(method, path, body=None, **kw):
            if path.endswith("/transfers"):
                transfers.append(path)
                if len(transfers) == 2:
                    raise SystemExit("crash")
            return real_request(method, path, body, **kw)

        with mock.patch.object(nessie, "request", crash_on_second_transfer), self.assertRaises(SystemExit):
            self.run_seed()
        self.assertEqual(len(self.fake.of("transfer")), 1)
        # the stored record says pending, but Nessie has the first transfer: the rerun must find it
        first = self.verdicts.docs[0]
        self.verdicts.update_one({"_id": first["_id"]}, {"$set": {"transfer": {**first["transfer"], "status": "pending"}}})
        result = self.run_seed()
        self.assertEqual(len(self.fake.of("transfer")), 3)
        self.assertEqual(len(self.verdicts.docs), 3)
        self.assertEqual(len([a for a in self.fake.accounts.values() if a["type"] == "Savings"]), 2)
        self.assertEqual(result["savings_cents"], TOTAL)
        self.assertTrue(all(d["transfer"]["status"] == "done" for d in self.verdicts.docs))

    def test_crash_after_creating_the_account_reuses_the_empty_orphan(self):
        orphan = self.fake.add_account(self.user["nessie_customer_id"], "Savings", nessie.SAVINGS_NICKNAME, 0)
        self.run_seed()
        user = self.users.find_one({"_id": self.user["_id"]})
        self.assertEqual(user["nessie_savings_id"], orphan["id"])
        self.assertEqual(len([a for a in self.fake.accounts.values() if a["type"] == "Savings"]), 2)

    def test_tops_up_checking_when_short(self):
        self.fake.accounts[self.user["nessie_checking_id"]].update(balance_cents=10000, available_cents=10000)
        result = self.run_seed()
        [deposit] = self.fake.of("deposit")
        self.assertEqual(deposit["amount_cents"], TOTAL - 10000 + nessie.TOPUP_CENTS)
        self.assertTrue(deposit["description"].startswith(seed.TOPUP_PREFIX))
        self.assertEqual(len(self.fake.of("transfer")), 3)
        self.assertEqual(result["savings_cents"], TOTAL)

    def test_keeps_an_existing_goal(self):
        self.users.update_one({"_id": self.user["_id"]}, {"$set": {"goal": {"name": "Car", "target_cents": 900000}}})
        self.run_seed()
        self.assertEqual(self.users.find_one({"_id": self.user["_id"]})["goal"]["name"], "Car")

    def test_blocked_for_a_local_user(self):
        self.users.update_one({"_id": self.user["_id"]}, {"$set": {"nessie_enabled": False}})
        with self.assertRaises(SystemExit):
            self.run_seed()
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(self.verdicts.docs, [])


if __name__ == "__main__":
    unittest.main()
