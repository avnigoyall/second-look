"""Seed the savings goal demo: a fresh Nessie Savings account filled by about 15 earlier skips (CONTRACT.md section 15).

Run from the project root, dry run first (no Nessie calls, no database writes, prints the full plan):
    python scripts/seed_goal_demo.py --dry-run
    python scripts/seed_goal_demo.py                      # demo@secondlook.test, moves real Nessie money
    python scripts/seed_goal_demo.py --email test_x@secondlook.test

What a real run does, in order (idempotent, safe to rerun after a crash):
1. Savings: creates a new "Second Look Savings" account (balance 0) for the user's Nessie customer and points
   the user doc at it (`nessie_savings_id`, plus `goal_demo_savings_id` and `goal_demo_previous_savings_id`).
   The old Savings and Checking accounts are left as they are. A rerun reuses the account it made.
2. Verdicts: one skip verdict per item in seed/goal_demo_items.json, with `seeded: true`, a stable `seed_key`
   and a past `created_at`. A rerun finds them by `seed_key`.
3. Money: reads the Savings and Checking transactions once and skips every item whose transfer is already
   there (description `Second Look skip <verdict_id>`, matched by nessie.is_skip_for). Tops Checking up if it
   cannot cover the rest, then moves each item Checking to Savings, about one item every 6 s
   (2 Nessie requests per item, far under 100 per minute).
4. Goal: "Japan trip", 200000 cents, unless the user already has a goal.

Only Hiresh runs this for real. Never prints the key.
"""
import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.services import nessie  # noqa: E402
from backend.services.nessie import Deadline, NessieError, NessieTimeout  # noqa: E402

ITEMS_FILE = ROOT / "seed" / "goal_demo_items.json"
DEFAULT_EMAIL = "demo@secondlook.test"
GOAL_NAME = "Japan trip"
GOAL_TARGET_CENTS = 200000
PACE_S = 6.0  # seconds between items: 2 requests each, so about 20 Nessie requests per minute
TOPUP_PREFIX = "Second Look goal seed top-up"
SEED_KEY_PREFIX = "goal_demo:"


def seed_description(verdict_id) -> str:
    """The format the user asked for. nessie.is_skip_for recognizes it (the older skip format)."""
    return f"Second Look skip {verdict_id}"


def dollars(cents: int | None) -> str:
    return "n/a" if cents is None else f"${cents / 100:,.2f}"


def load_items(path: Path = ITEMS_FILE) -> list[dict]:
    items = json.loads(path.read_text())
    for i, item in enumerate(items):
        for field in ("name", "store", "price_cents", "days_ago"):
            if field not in item:
                raise ValueError(f"item {i} has no {field}")
        if not isinstance(item["price_cents"], int) or item["price_cents"] <= 0:
            raise ValueError(f"item {i} price_cents must be a positive integer")
    return items


def seed_key(item: dict) -> str:
    return f"{SEED_KEY_PREFIX}{item['store']}|{item['name']}"


class GoalSeeder:
    """plan() only reads MongoDB and never calls Nessie. run(plan) does the writes and the transfers."""

    def __init__(self, users, verdicts, sleep=time.sleep, out=print, pace_s: float = PACE_S, now=None):
        self.users, self.verdicts = users, verdicts
        self.sleep, self.out, self.pace_s = sleep, out, pace_s
        self.now = now or (lambda: datetime.now(timezone.utc))

    # ---------- plan (read only) ----------

    def plan(self, email: str, items: list[dict]) -> dict:
        user = self.users.find_one({"email": email})
        if user is None:
            raise SystemExit(f"No user with email {email}.")
        problems = []
        if user.get("nessie_enabled") is not True:
            problems.append("user is not nessie_enabled (run scripts/setup_nessie_demo.py first)")
        if not (user.get("nessie_customer_id") and user.get("nessie_checking_id")):
            problems.append("user has no Nessie customer or Checking (run scripts/setup_nessie_demo.py first)")
        marker = user.get("goal_demo_savings_id")
        reuse = bool(marker) and marker == user.get("nessie_savings_id")
        rows = []
        for item in items:
            doc = self.verdicts.find_one({"user_id": user["_id"], "seed_key": seed_key(item)})
            rows.append({"item": item, "seed_key": seed_key(item), "verdict_id": doc["_id"] if doc else None,
                         "stored_status": ((doc or {}).get("transfer") or {}).get("status")})
        return {"email": email, "user": user, "problems": problems, "items": rows,
                "total_cents": sum(r["item"]["price_cents"] for r in rows),
                "savings": {"action": "reuse" if reuse else "create", "account_id": marker if reuse else None,
                            "previous_id": user.get("nessie_savings_id")},
                "goal": {"action": "keep" if user.get("goal") else "create",
                         "existing": user.get("goal"), "name": GOAL_NAME, "target_cents": GOAL_TARGET_CENTS}}

    def print_plan(self, plan: dict, dry_run: bool) -> None:
        p, user = self.out, plan["user"]
        p(f"{'DRY RUN: nothing will be written, no Nessie calls' if dry_run else 'PLAN'}")
        p(f"User {plan['email']}  nessie_enabled={user.get('nessie_enabled') is True}  "
          f"customer={user.get('nessie_customer_id')}  checking={user.get('nessie_checking_id')}")
        for problem in plan["problems"]:
            p(f"  BLOCKED: {problem}")
        s = plan["savings"]
        if s["action"] == "reuse":
            p(f"1. Savings: reuse the seeded account {s['account_id']} made by an earlier run")
        else:
            p(f"1. Savings: create a new Savings account \"{nessie.SAVINGS_NICKNAME}\" (balance 0) for customer "
              f"{user.get('nessie_customer_id')}; point nessie_savings_id at it; old Savings {s['previous_id']} "
              f"stays untouched (kept as goal_demo_previous_savings_id). An empty orphan from a crashed run is "
              f"reused instead of creating another")
        p(f"2. Skip verdicts ({len(plan['items'])} items, seeded: true):")
        for i, row in enumerate(plan["items"], 1):
            item = row["item"]
            state = (f"exists {row['verdict_id']} (transfer {row['stored_status'] or 'none'})"
                     if row["verdict_id"] else "new verdict")
            p(f"   {i:2}. {item['store']:<9} {item['name']:<32} {dollars(item['price_cents']):>9}  "
              f"{item['days_ago']:>2} days ago  image_url={'yes' if item.get('image_url') else 'null'}  {state}")
        p(f"   Total: {dollars(plan['total_cents'])}")
        p("3. Transfers Checking -> Savings, description \"Second Look skip <verdict_id>\". Before moving anything "
          "the run reads Savings and Checking transactions and skips every item already moved (not read in a "
          "dry run). Stored transfer status done: "
          f"{sum(1 for r in plan['items'] if r['stored_status'] == 'done')} of {len(plan['items'])}")
        p(f"   Top-up: if Checking available is below the amount still to move (at most "
          f"{dollars(plan['total_cents'])}), deposit the shortfall plus {dollars(nessie.TOPUP_CENTS)} "
          f"(\"{TOPUP_PREFIX} <time>\"), wait for it to settle (up to 120 s), then transfer. Checking balance is "
          f"not read in a dry run")
        p(f"   Pace: one item every {self.pace_s:g} s, 2 Nessie requests per item. "
          f"About {self.request_estimate(plan)} Nessie requests in total (under 100 per minute)")
        g = plan["goal"]
        if g["action"] == "keep":
            p(f"4. Goal: keep the existing goal \"{g['existing']['name']}\" "
              f"({dollars(g['existing']['target_cents'])})")
        else:
            p(f"4. Goal: create \"{g['name']}\" with target {dollars(g['target_cents'])}")
        remaining = GOAL_TARGET_CENTS - plan["total_cents"]
        p(f"After the run: Savings about {dollars(plan['total_cents'])}. A live skip of {dollars(remaining)} or "
          f"more completes the {dollars(GOAL_TARGET_CENTS)} goal")

    @staticmethod
    def request_estimate(plan: dict) -> int:
        account = 0 if plan["savings"]["action"] == "reuse" else 2  # list accounts, create
        top_up = 6  # find, deposit, settle, a few settle polls, re-read
        return account + 2 + 1 + top_up + 2 * len(plan["items"]) + 2

    # ---------- run (writes, real Nessie) ----------

    def run(self, plan: dict) -> dict:
        if plan["problems"]:
            raise SystemExit("Blocked: " + "; ".join(plan["problems"]))
        user = self.ensure_savings(plan)
        rows = self.ensure_verdicts(plan, user)
        moved, to_move = self.reconcile(user, rows)
        if to_move:
            self.ensure_funds(user, sum(r["item"]["price_cents"] for r in to_move))
        for i, row in enumerate(to_move):
            if i:
                self.sleep(self.pace_s)
            self.move(user, row)
            moved += 1
        self.ensure_goal(user)
        savings = nessie.projected(nessie.get_account(user["nessie_savings_id"]),
                                   nessie.account_transactions(user["nessie_savings_id"], 50))
        self.out(f"Done. {moved} of {len(rows)} items moved. Savings (projected) {dollars(savings)}")
        return {"user": user, "moved": moved, "savings_cents": savings}

    def ensure_savings(self, plan: dict) -> dict:
        user = self.users.find_one({"_id": plan["user"]["_id"]})
        marker = user.get("goal_demo_savings_id")
        if marker and marker == user.get("nessie_savings_id"):
            self.out(f"Savings: reusing {marker}")
            return user
        old = user.get("nessie_savings_id")
        accounts = nessie.request("GET", f"/customers/{user['nessie_customer_id']}/accounts",
                                  params={"limit": 100}).get("items") or []
        orphan = next((a for a in accounts if a.get("type") == "Savings" and a.get("nickname") == nessie.SAVINGS_NICKNAME
                       and a["id"] not in (old, user.get("goal_demo_previous_savings_id"))
                       and a.get("balance_cents") == 0 and a.get("available_cents") == 0), None)
        if orphan:
            account_id = orphan["id"]
            self.out(f"Savings: reusing empty account {account_id} from an earlier run")
        else:
            account_id = nessie.request("POST", f"/customers/{user['nessie_customer_id']}/accounts",
                                        {"type": "Savings", "nickname": nessie.SAVINGS_NICKNAME, "balance_cents": 0},
                                        timeout=nessie.SETUP_TIMEOUT_S)["id"]
            self.out(f"Savings: created {account_id}; old Savings {old} left untouched")
        fields = {"nessie_savings_id": account_id, "goal_demo_savings_id": account_id}
        if old and old != account_id and not user.get("goal_demo_previous_savings_id"):
            fields["goal_demo_previous_savings_id"] = old
        self.users.update_one({"_id": user["_id"]}, {"$set": fields})
        return {**user, **fields}

    def ensure_verdicts(self, plan: dict, user: dict) -> list[dict]:
        rows = []
        for row in plan["items"]:
            item = row["item"]
            doc = self.verdicts.find_one({"user_id": user["_id"], "seed_key": row["seed_key"]})
            if doc is None:
                doc = self.verdict_doc(user, item, row["seed_key"])
                doc["_id"] = self.verdicts.insert_one(doc).inserted_id
            rows.append({**row, "verdict_id": doc["_id"], "doc": doc})
        return rows

    def verdict_doc(self, user: dict, item: dict, key: str) -> dict:
        price = round(item["price_cents"] / 100, 2)
        created = self.now() - timedelta(days=item["days_ago"])
        return {"user_id": user["_id"], "seeded": True, "seed_key": key,
                "item": {"name": item["name"], "image_url": item.get("image_url"), "price": price,
                         "store": item["store"], "url": item.get("url"), "description": None, "breadcrumb": None},
                "attributes": None, "verdict": item.get("verdict") or "have_this_look",
                "reason": item.get("reason") or "You already own something close to this.",
                "matched_closet_item_id": None, "matched_closet_item_ids": [], "matched_trend_id": None,
                "alternatives": [], "expected_wears": None, "cost_per_wear": None, "decided_by": "code",
                "action": "skipped", "feedback": "none", "created_at": created,
                "transfer": {"status": "pending", "id": None, "amount_cents": item["price_cents"], "at": created}}

    def reconcile(self, user: dict, rows: list[dict]) -> tuple[int, list[dict]]:
        """Mark items whose transfer already exists in Nessie as done. Returns (already moved, still to move)."""
        seen = []
        for account_id in (user["nessie_savings_id"], user["nessie_checking_id"]):
            seen += nessie.account_transactions(account_id, 100)
        live = [t for t in seen if t.get("kind") == "transfer" and t.get("status") not in nessie.DEAD_STATUSES]
        moved, to_move = 0, []
        for row in rows:
            found = next((t for t in live if nessie.is_skip_for(t.get("description"), row["verdict_id"])), None)
            if found:
                moved += 1
                self.mark_done(row, found["id"])
            else:
                to_move.append(row)
        self.out(f"Transfers: {moved} already in Nessie, {len(to_move)} to move")
        return moved, to_move

    def ensure_funds(self, user: dict, needed: int) -> None:
        checking = user["nessie_checking_id"]
        available = nessie.get_account(checking)["available_cents"]
        if available >= needed:
            self.out(f"Checking: {dollars(available)} available covers {dollars(needed)}")
            return
        pending = nessie.find_transaction(checking, lambda d: (d or "").startswith(TOPUP_PREFIX))
        if pending and pending.get("status") == "pending":
            self.out(f"Checking: waiting for the earlier top-up {pending['id']} to settle")
            nessie.wait_settled(pending["id"], 120)
            available = nessie.get_account(checking)["available_cents"]
        if available < needed:
            amount = needed - available + nessie.TOPUP_CENTS
            tx = nessie.request("POST", f"/accounts/{checking}/deposits",
                                {"amount_cents": amount, "description": f"{TOPUP_PREFIX} {self.now().isoformat()}"})
            self.out(f"Checking: {dollars(available)} available, depositing {dollars(amount)} ({tx['id']}), "
                     f"waiting for it to settle")
            nessie.settle_early(tx["id"], nessie.CALL_TIMEOUT_S)
            nessie.wait_settled(tx["id"], 120)
            available = nessie.get_account(checking)["available_cents"]
        if available < needed:
            raise SystemExit(f"Checking still has only {dollars(available)} of {dollars(needed)}. Rerun later.")

    def move(self, user: dict, row: dict) -> None:
        vid, cents = row["verdict_id"], row["item"]["price_cents"]
        description = seed_description(vid)
        try:
            tx = nessie.request("POST", f"/accounts/{user['nessie_checking_id']}/transfers",
                                {"payee_id": user["nessie_savings_id"], "amount_cents": cents,
                                 "description": description})
        except NessieTimeout:  # may have landed: look once before giving up
            self.sleep(2)
            tx = nessie.find_transaction(user["nessie_savings_id"], lambda d: nessie.is_skip_for(d, vid))
            if tx is None:
                raise SystemExit(f"Transfer for {row['item']['name']} timed out. Rerun: it checks before moving.")
        try:
            nessie.settle_early(tx["id"], Deadline(nessie.CALL_TIMEOUT_S).timeout())
        except NessieError:
            pass
        self.mark_done(row, tx["id"])
        self.out(f"  moved {dollars(cents):>9}  {row['item']['name']}  ({tx['id']})")

    def mark_done(self, row: dict, tx_id: str) -> None:
        stored = (row.get("doc") or {}).get("transfer") or {}
        if stored.get("status") == "done" and stored.get("id") == tx_id:
            return
        record = {"status": "done", "id": tx_id, "amount_cents": row["item"]["price_cents"],
                  "at": (row.get("doc") or {}).get("created_at") or self.now()}
        self.verdicts.update_one({"_id": row["verdict_id"]}, {"$set": {"transfer": record}})

    def ensure_goal(self, user: dict) -> None:
        fresh = self.users.find_one({"_id": user["_id"]}) or user
        if fresh.get("goal"):
            self.out(f"Goal: keeping \"{fresh['goal']['name']}\"")
            return
        now = self.now()
        self.users.update_one({"_id": user["_id"]}, {"$set": {"goal": {
            "name": GOAL_NAME, "target_cents": GOAL_TARGET_CENTS, "created_at": now, "updated_at": now}}})
        self.out(f"Goal: created \"{GOAL_NAME}\" ({dollars(GOAL_TARGET_CENTS)})")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--email", default=DEFAULT_EMAIL)
    parser.add_argument("--dry-run", action="store_true", help="print the plan only: no Nessie calls, no writes")
    parser.add_argument("--items", default=str(ITEMS_FILE))
    args = parser.parse_args()
    from backend.db import users, verdicts
    seeder = GoalSeeder(users, verdicts)
    plan = seeder.plan(args.email, load_items(Path(args.items)))
    seeder.print_plan(plan, dry_run=args.dry_run)
    if args.dry_run:
        return
    if not nessie.configured():
        sys.exit("NESSIE_API_KEY is not set in .env")
    seeder.run(plan)


if __name__ == "__main__":
    main()
