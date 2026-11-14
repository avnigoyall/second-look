"""Savings goal (CONTRACT.md section 15): one optional goal per user, progress from the money skips kept.

Stored on the user doc as `goal: {name, target_cents, created_at, updated_at}`. `reached` and `percent` are
never stored; they are derived from saved_cents every time.

saved_cents: for Nessie users the projected Savings balance (the number GET /savings returns as savings_cents),
otherwise, or when Nessie does not answer, round(money_kept * 100).

Nessie Savings reads are cached for CACHE_TTL_S per user and Savings account, in this process only: a dict
of (monotonic time, cents) behind a lock. Several uvicorn workers would each keep their own cache, which is
fine for a 15 s staleness bound. The skip path writes the fresh post-transfer balance into the cache
(`remember_savings`), and the background retry forgets it when a delayed transfer lands (`forget_savings`).
"""
import logging
import threading
import time

from backend.services import money, nessie
from backend.services.nessie import Deadline, NessieError

log = logging.getLogger("secondlook.goal")

CACHE_TTL_S = 15.0
ITEMS_LIMIT = 100
ACTION_READ_BUDGET_S = 1.5  # a cache miss on the /action path may not add more than this to the click

_cache: dict[tuple[str, str], tuple[float, int]] = {}
_cache_lock = threading.Lock()
_clock = time.monotonic  # tests replace this to move time forward


# ---------- Nessie Savings cache ----------

def _key(user: dict) -> tuple[str, str]:
    # The account id is part of the key, so pointing the user at a new Savings account never serves the old one.
    return str(user["_id"]), str(user.get("nessie_savings_id"))


def cached_savings(user: dict) -> int | None:
    with _cache_lock:
        hit = _cache.get(_key(user))
    if hit and _clock() - hit[0] < CACHE_TTL_S:
        return hit[1]
    return None


def remember_savings(user: dict, cents: int) -> None:
    with _cache_lock:
        _cache[_key(user)] = (_clock(), cents)


def forget_savings(user_id) -> None:
    """Drop every cached Savings balance of this user (any account)."""
    with _cache_lock:
        for key in [k for k in _cache if k[0] == str(user_id)]:
            del _cache[key]


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def read_nessie_savings(user: dict, deadline: Deadline) -> int:
    """Projected Savings balance: the same number GET /savings returns, from 2 calls on the Savings account only."""
    account_id = user["nessie_savings_id"]
    account = nessie.get_account(account_id, timeout=deadline.timeout())
    return nessie.projected(account, nessie.account_transactions(account_id, 50, timeout=deadline.timeout()))


# ---------- saved_cents and the goal shapes ----------

def saved_cents(user: dict, budget_s: float = nessie.CALL_TIMEOUT_S) -> tuple[int, str]:
    """(saved_cents, source). Nessie (cached 15 s) for Nessie users, local when off or unreachable."""
    if money.nessie_active(user) and nessie.has_accounts(user):
        cents = cached_savings(user)
        if cents is not None:
            return cents, "nessie"
        try:
            cents = read_nessie_savings(user, Deadline(budget_s))
            remember_savings(user, cents)
            return cents, "nessie"
        except NessieError as e:
            log.info("goal: Nessie savings unavailable, using local: %s", e)
    return round(money.money_kept(user["_id"]) * 100), "local"


def progress(goal: dict, saved: int) -> dict:
    target = goal["target_cents"]
    return {"name": goal["name"], "target_cents": target, "saved_cents": saved,
            "percent": min(100, saved * 100 // target), "reached": saved >= target}


def goal_out(user: dict) -> dict:
    """GET /goal and PUT /goal: {goal: null} or {goal: {name, target_cents, saved_cents, percent, reached, source}}."""
    goal = user.get("goal")
    if not goal:
        return {"goal": None}
    saved, source = saved_cents(user)
    return {"goal": {**progress(goal, saved), "source": source}}


def action_goal(user: dict, transfer: dict | None, balances: dict | None) -> dict | None:
    """The /action goal field after a skip: {name, target_cents, saved_cents, reached}, or None without a goal.

    balances (from the skip path) already count this transfer, so they refresh the cache with no extra call.
    A done transfer without balances drops the cache, so the read below includes it.
    """
    goal = user.get("goal")
    if balances and balances.get("savings_cents") is not None and money.nessie_active(user):
        remember_savings(user, balances["savings_cents"])
    elif transfer and transfer.get("status") == "done":
        forget_savings(user["_id"])
    if not goal:
        return None
    saved, _ = saved_cents(user, ACTION_READ_BUDGET_S)
    out = progress(goal, saved)
    return {k: out[k] for k in ("name", "target_cents", "saved_cents", "reached")}


# ---------- storage ----------

def set_goal(user: dict, name: str, target_cents: int, now) -> dict:
    """Create or update. created_at survives updates. Returns the user doc with the new goal."""
    from backend.db import users
    old = user.get("goal") or {}
    goal = {"name": name, "target_cents": target_cents, "created_at": old.get("created_at") or now,
            "updated_at": now}
    users.update_one({"_id": user["_id"]}, {"$set": {"goal": goal}})
    return {**user, "goal": goal}


def delete_goal(user: dict) -> None:
    """Removes only the goal field. Skips, verdicts and Savings stay as they are. Idempotent."""
    from backend.db import users
    users.update_one({"_id": user["_id"]}, {"$unset": {"goal": ""}})


def skipped_items(user_id) -> list[dict]:
    """GET /goal/items: this user's skipped verdicts, newest first, at most 100. Makes no Nessie call."""
    from backend.db import verdicts
    docs = verdicts.find({"user_id": user_id, "action": "skipped"},
                         {"item.name": 1, "item.store": 1, "item.price": 1, "item.image_url": 1, "created_at": 1,
                          "transfer": 1}).sort([("created_at", -1), ("_id", -1)]).limit(ITEMS_LIMIT)
    return [item_out(d) for d in docs]


def item_out(doc: dict) -> dict:
    item = doc.get("item") or {}
    price = item.get("price")
    transfer = doc.get("transfer") or {}
    return {"verdict_id": str(doc["_id"]), "name": item.get("name") or "Item", "store": item.get("store"),
            "price_cents": round(price * 100) if price is not None else None,
            "image_url": item.get("image_url") or None, "skipped_at": doc["created_at"],
            "moved_cents": transfer.get("amount_cents") if transfer.get("status") == "done" else None}
