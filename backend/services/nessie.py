"""Capital One Nessie v2 client: the shopper's Checking and Savings accounts, and the Skip it transfer.

Facts from the live API (scripts/nessie_smoke.py, Nov 14):
- Amounts are integer cents (`amount_cents`, `balance_cents`). No dollar conversion is needed.
- Every money movement is created `pending`. `POST /transactions/{id}/settle` only moves `settle_at` to now;
  the hosted sweep applies the money about 30 to 60 s later. While pending, the payer's `available_cents`
  already drops, but the payee's balance does not grow yet. `projected()` below counts both sides.
- A pending deposit does not count as available funds: a top-up must settle before a transfer can use it.
- There is no idempotency key. A create that timed out may have landed, so callers look for the transfer's
  description in the account's transactions before they try again.
- Errors are problem+json with a stable `type`, e.g. `.../problems/insufficient_funds` (HTTP 422).

The key only ever travels in the Authorization header. Error text is scrubbed before it is logged or stored.
"""
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import httpx

from backend.settings import ROOT  # noqa: F401  (loads .env)

log = logging.getLogger("secondlook.nessie")

BASE_URL = "https://v2-prod-api.nessieisreal.com"
CALL_TIMEOUT_S = 3.0
SETUP_TIMEOUT_S = 10.0
CHECKING_NICKNAME = "Second Look Checking"
SAVINGS_NICKNAME = "Second Look Savings"
CHECKING_START_CENTS = 150000
SAVINGS_START_CENTS = 0
TOPUP_CENTS = 150000  # added on top of the shortfall when checking cannot cover a skip
LOCK_STALE_S = 30  # a provisioning claim older than this belongs to a crashed run and may be taken over
CUSTOMER_ADDRESS = {"street_number": "1", "street_name": "Nassau St", "city": "Princeton", "state": "NJ",
                    "zip": "08542"}
CREDIT_KINDS = {"deposit", "interest_credit", "refund"}  # kinds where the path account (payer_id) receives
DEAD_STATUSES = {"declined", "cancelled"}

_transport: httpx.BaseTransport | None = None  # tests install an httpx.MockTransport here
_sleep = time.sleep  # tests replace this (patching time.sleep itself would spin pymongo's monitor threads)


class NessieError(Exception):
    """Any Nessie failure. `problem` is the problem+json type suffix when the API sent one."""

    def __init__(self, message: str, problem: str | None = None, status: int | None = None):
        super().__init__(scrub(message))
        self.problem = problem
        self.status = status


class NessieTimeout(NessieError):
    """The call timed out. A create may still have landed: reconcile before retrying."""


class TopUpPending(NessieError):
    """Checking could not cover the skip. A top-up deposit is on its way and must settle first."""

    def __init__(self, deposit_id: str):
        super().__init__("top-up deposit is settling")
        self.deposit_id = deposit_id


def api_key() -> str:
    return os.getenv("NESSIE_API_KEY", "")


def configured() -> bool:
    return bool(api_key())


def scrub(text: str) -> str:
    key = api_key()
    return text.replace(key, "***") if key else text


def has_accounts(user: dict) -> bool:
    return bool(user.get("nessie_customer_id") and user.get("nessie_checking_id") and user.get("nessie_savings_id"))


def skip_description(verdict_id) -> str:
    return f"Second Look skip {verdict_id}"


def topup_description(verdict_id) -> str:
    return f"Second Look top-up {verdict_id}"


def now() -> datetime:
    return datetime.now(timezone.utc)


# ---------- HTTP ----------

class Deadline:
    """A time budget shared by several calls. Each call gets min(per-call cap, time left)."""

    def __init__(self, seconds: float, per_call: float = CALL_TIMEOUT_S):
        self.end = time.monotonic() + seconds
        self.per_call = per_call

    def timeout(self) -> float:
        left = self.end - time.monotonic()
        if left <= 0.05:
            raise NessieTimeout("time budget used up")
        return min(self.per_call, left)


def request(method: str, path: str, body: dict | None = None, params: dict | None = None,
            timeout: float = CALL_TIMEOUT_S):
    """One Nessie call. Returns the decoded JSON. Raises NessieTimeout or NessieError, never httpx errors."""
    key = api_key()
    if not key:
        raise NessieError("NESSIE_API_KEY is not set")
    try:
        with httpx.Client(base_url=BASE_URL, headers={"Authorization": f"Bearer {key}"}, timeout=timeout,
                          transport=_transport) as client:
            r = client.request(method, path, json=body, params=params)
    except httpx.TimeoutException as e:
        raise NessieTimeout(f"{method} {path} timed out ({type(e).__name__})")
    except httpx.HTTPError as e:
        raise NessieError(f"{method} {path} failed: {type(e).__name__}: {str(e)[:160]}")
    if r.status_code >= 300:
        problem = None
        try:
            problem = (r.json().get("type") or "").rsplit("/", 1)[-1] or None
        except ValueError:
            pass
        raise NessieError(f"{method} {path} -> HTTP {r.status_code} {problem or r.text[:160]}", problem,
                          r.status_code)
    try:
        return r.json()
    except ValueError:
        raise NessieError(f"{method} {path} returned non-JSON")


# ---------- accounts ----------

_local_lock = threading.Lock()  # one provisioning per process; the Mongo claim covers other processes


def ensure_accounts(user: dict) -> dict:
    """Create (once) the shopper's Nessie customer, Checking and Savings. Returns the fresh user doc.

    Idempotent and race-safe: a Mongo claim (`nessie_lock`) lets one caller provision while others wait for
    the ids. Ids are stored as soon as each object exists, and existing accounts are found by nickname, so a
    crashed run resumes instead of creating doubles.
    """
    from backend.db import users

    deadline = Deadline(SETUP_TIMEOUT_S, per_call=SETUP_TIMEOUT_S)
    fresh = users.find_one({"_id": user["_id"]}) or user
    if has_accounts(fresh):
        return fresh
    with _local_lock:
        while True:
            fresh = users.find_one({"_id": user["_id"]}) or user
            if has_accounts(fresh):
                return fresh
            stale = now() - timedelta(seconds=LOCK_STALE_S)
            claimed = users.find_one_and_update(
                {"_id": user["_id"], "$or": [{"nessie_lock": None}, {"nessie_lock": {"$lt": stale}}]},
                {"$set": {"nessie_lock": now()}})
            if claimed:
                break
            if deadline.end - time.monotonic() <= 0:
                raise NessieTimeout("another request is still creating the Nessie accounts")
            _sleep(0.25)
        try:
            return _provision(users, fresh, deadline)
        finally:
            users.update_one({"_id": user["_id"]}, {"$unset": {"nessie_lock": ""}})


def _provision(users, user: dict, deadline: Deadline) -> dict:
    customer_id = user.get("nessie_customer_id")
    if not customer_id:
        first = "".join(ch for ch in (user.get("email") or "shopper").split("@")[0] if ch.isalnum())[:40]
        customer = request("POST", "/customers", {"first_name": first or "Shopper", "last_name": "Second Look",
                                                  "address": CUSTOMER_ADDRESS}, timeout=deadline.timeout())
        customer_id = customer["id"]
        users.update_one({"_id": user["_id"]}, {"$set": {"nessie_customer_id": customer_id}})
    existing = request("GET", f"/customers/{customer_id}/accounts", params={"limit": 100},
                       timeout=deadline.timeout()).get("items") or []
    by_nickname = {a["nickname"]: a["id"] for a in existing}
    ids = {}
    for field, kind, nickname, cents in (("nessie_checking_id", "Checking", CHECKING_NICKNAME, CHECKING_START_CENTS),
                                         ("nessie_savings_id", "Savings", SAVINGS_NICKNAME, SAVINGS_START_CENTS)):
        account_id = by_nickname.get(nickname)
        if not account_id:
            account_id = request("POST", f"/customers/{customer_id}/accounts",
                                 {"type": kind, "nickname": nickname, "balance_cents": cents},
                                 timeout=deadline.timeout())["id"]
        ids[field] = account_id
        users.update_one({"_id": user["_id"]}, {"$set": {field: account_id}})
    return {**user, "nessie_customer_id": customer_id, **ids}


def get_account(account_id: str, timeout: float = CALL_TIMEOUT_S) -> dict:
    return request("GET", f"/accounts/{account_id}", timeout=timeout)


def account_transactions(account_id: str, limit: int = 100, timeout: float = CALL_TIMEOUT_S) -> list[dict]:
    return request("GET", f"/accounts/{account_id}/transactions", params={"limit": limit},
                   timeout=timeout).get("items") or []


def projected(account: dict, transactions: list[dict]) -> int:
    """Balance once everything pending settles: available (holds already taken out) plus pending credits."""
    incoming = sum(t["amount_cents"] for t in transactions if t.get("status") == "pending" and (
        (t.get("kind") == "transfer" and t.get("payee_id") == account["id"])
        or (t.get("kind") in CREDIT_KINDS and t.get("payer_id") == account["id"])))
    return account["available_cents"] + incoming


def get_balances(user: dict, deadline: Deadline | None = None) -> dict:
    """{checking_cents, savings_cents} as they will be once pending money settles."""
    deadline = deadline or Deadline(CALL_TIMEOUT_S)
    out = {}
    for field, key in (("nessie_checking_id", "checking_cents"), ("nessie_savings_id", "savings_cents")):
        account = get_account(user[field], timeout=deadline.timeout())
        out[key] = projected(account, account_transactions(user[field], 50, timeout=deadline.timeout()))
    return out


def list_transfers(user: dict, limit: int = 20, timeout: float = CALL_TIMEOUT_S) -> list[dict]:
    """Money moved into Savings, newest first: [{amount_cents, description, at}]."""
    savings = user["nessie_savings_id"]
    items = account_transactions(savings, 100, timeout=timeout)
    return [{"amount_cents": t["amount_cents"], "description": t.get("description") or "", "at": t["created_at"]}
            for t in items
            if t.get("kind") == "transfer" and t.get("payee_id") == savings and t.get("status") not in DEAD_STATUSES
            ][:limit]


def find_transaction(account_id: str, description: str, timeout: float = CALL_TIMEOUT_S) -> dict | None:
    """The live (not declined or cancelled) transaction with this exact description, if any."""
    for t in account_transactions(account_id, 100, timeout=timeout):
        if t.get("description") == description and t.get("status") not in DEAD_STATUSES:
            return t
    return None


def settle_early(tx_id: str, timeout: float) -> None:
    """Skip the demo pending window. Best effort: the sweep settles it anyway."""
    try:
        request("POST", f"/transactions/{tx_id}/settle", timeout=timeout)
    except NessieError as e:
        log.info("settle %s skipped: %s", tx_id, e)


# ---------- the skip ----------

def amount_cents(verdict: dict) -> int | None:
    price = (verdict.get("item") or {}).get("price")
    if price is None or price <= 0:
        return None
    return round(price * 100)


def skip_transfer(user: dict, verdict: dict, deadline: Deadline | None = None, check_existing: bool = False) -> dict:
    """Move the item's price from Checking to Savings. Returns {status, id, amount_cents, at}.

    status is "done" (Nessie accepted the transfer) or "no_price" (nothing to move). Raises NessieTimeout
    (may have landed), TopUpPending (checking was short, a deposit is settling) or NessieError.
    check_existing looks for an earlier transfer with the same description first, so a retry never repeats one.
    """
    deadline = deadline or Deadline(CALL_TIMEOUT_S)
    cents = amount_cents(verdict)
    if cents is None:
        return {"status": "no_price", "id": None, "amount_cents": None, "at": now()}
    checking, savings = user["nessie_checking_id"], user["nessie_savings_id"]
    description = skip_description(verdict["_id"])
    if check_existing:
        found = find_transaction(checking, description, timeout=deadline.timeout())
        if found:
            return {"status": "done", "id": found["id"], "amount_cents": cents, "at": now()}
    account = get_account(checking, timeout=deadline.timeout())
    if account["available_cents"] < cents:
        raise TopUpPending(top_up(checking, verdict["_id"], cents - account["available_cents"], deadline))
    try:
        tx = request("POST", f"/accounts/{checking}/transfers",
                     {"payee_id": savings, "amount_cents": cents, "description": description},
                     timeout=deadline.timeout())
    except NessieError as e:
        if e.problem == "insufficient_funds":  # funds moved between the read and the transfer
            raise TopUpPending(top_up(checking, verdict["_id"], cents, deadline))
        raise
    try:
        settle_early(tx["id"], deadline.timeout())
    except NessieTimeout:
        pass
    return {"status": "done", "id": tx["id"], "amount_cents": cents, "at": now()}


def top_up(checking: str, verdict_id, shortfall: int, deadline: Deadline) -> str:
    """Deposit enough into Checking for this skip, once per verdict. Returns the deposit id."""
    description = topup_description(verdict_id)
    found = find_transaction(checking, description, timeout=deadline.timeout())
    if found:
        return found["id"]
    tx = request("POST", f"/accounts/{checking}/deposits",
                 {"amount_cents": shortfall + TOPUP_CENTS, "description": description}, timeout=deadline.timeout())
    settle_early(tx["id"], deadline.timeout())
    return tx["id"]


def wait_settled(tx_id: str, max_wait_s: float = 120, poll_s: float = 5) -> str:
    """Poll a transaction until it leaves pending. Returns its last status."""
    end = time.monotonic() + max_wait_s
    while True:
        status = request("GET", f"/transactions/{tx_id}")["status"]
        if status != "pending" or time.monotonic() >= end:
            return status
        _sleep(poll_s)
