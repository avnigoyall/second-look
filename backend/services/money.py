"""Skip it moves money: the /action skip path, its background retry, and the GET /savings summary.

Only users with `nessie_enabled: true` (set by scripts/setup_nessie_demo.py) touch Nessie. Everyone else,
including the throwaway users the extension E2E creates, gets the local path: no Nessie calls, placeholder
balances, savings computed from skipped verdicts.

Stored on the verdict: `transfer {status done|pending|failed|no_price, id, amount_cents, at}` plus, when
relevant, `topup_id` and `error` (scrubbed). Nothing here ever raises to the shopper.
"""
import logging
from datetime import timedelta

from pymongo import ReturnDocument

from backend.services import nessie
from backend.services.nessie import Deadline, NessieError, TopUpPending
from backend.timeutil import as_utc

log = logging.getLogger("secondlook.money")

ACTION_BUDGET_S = 3.0  # the skip transfer plus the balance read must fit in this
RETRY_BUDGET_S = 15.0
TOPUP_WAIT_S = 120  # the hosted sweep settles within about a minute


def nessie_active(user: dict) -> bool:
    return nessie.configured() and user.get("nessie_enabled") is True


# ---------- database access (small on purpose, so tests can replace it) ----------

def _claim(verdict_id, cents: int | None):
    """Atomically mark the verdict's transfer as pending, unless one exists already (a failed one is retried).

    Returns (claimed_record or None, verdict doc before the claim).
    """
    from backend.db import verdicts
    record = {"status": "pending", "id": None, "amount_cents": cents, "at": nessie.now()}
    before = verdicts.find_one_and_update(
        {"_id": verdict_id, "$or": [{"transfer": None}, {"transfer.status": "failed"}]},
        {"$set": {"transfer": record}}, return_document=ReturnDocument.BEFORE)
    if before is None:
        return None, verdicts.find_one({"_id": verdict_id}) or {}
    return record, before


def _save_transfer(verdict_id, record: dict) -> None:
    from backend.db import verdicts
    verdicts.update_one({"_id": verdict_id}, {"$set": {"transfer": record}})


def _load_verdict(verdict_id) -> dict | None:
    from backend.db import verdicts
    return verdicts.find_one({"_id": verdict_id})


def _load_user(user_id) -> dict | None:
    from backend.db import users
    return users.find_one({"_id": user_id})


# ---------- POST /action skipped ----------

def on_skip(user: dict, verdict: dict, background) -> tuple[dict | None, dict | None]:
    """Run the skip transfer inside the click budget. Returns (transfer for the response, balances).

    transfer is {status, amount_cents} or None on the local path. balances is {checking_cents, savings_cents}
    or None when Nessie is off, down or out of time. A timeout or error answers "pending" and schedules one
    background retry, which first checks whether the transfer landed.
    """
    if not nessie_active(user):
        return None, None
    try:
        return _on_skip(user, verdict, background)
    except Exception as e:  # never surface to the shopper
        log.warning("skip transfer for verdict %s: %s", verdict.get("_id"), nessie.scrub(str(e))[:300])
        return None, None


def _on_skip(user: dict, verdict: dict, background):
    deadline = Deadline(ACTION_BUDGET_S)
    cents = nessie.amount_cents(verdict)
    record, before = _claim(verdict["_id"], cents)
    if record is None:  # already handled by an earlier click
        record = before.get("transfer") or {"status": "pending", "amount_cents": cents}
    elif not nessie.has_accounts(user):
        if cents is None:
            record = {**record, "status": "no_price"}
        else:
            background.add_task(retry_skip, verdict["_id"])  # provisioning takes up to 10 s: not on the click
        _save_transfer(verdict["_id"], record)
    else:
        retrying_failed = (before.get("transfer") or {}).get("status") == "failed"
        try:
            record = nessie.skip_transfer(user, verdict, deadline, check_existing=retrying_failed)
        except TopUpPending as e:
            record = {**record, "topup_id": e.deposit_id}
            background.add_task(retry_skip, verdict["_id"])
        except NessieError as e:  # includes NessieTimeout: the POST may have landed, so "pending", not "failed"
            log.info("skip transfer for verdict %s pending: %s", verdict["_id"], e)
            record = {**record, "error": str(e)[:200]}
            background.add_task(retry_skip, verdict["_id"])
        _save_transfer(verdict["_id"], record)
    balances = None
    if nessie.has_accounts(user):
        try:
            balances = nessie.get_balances(user, deadline)
        except NessieError as e:
            log.info("balances unavailable: %s", e)
    return {"status": record["status"], "amount_cents": record.get("amount_cents")}, balances


def retry_skip(verdict_id) -> None:
    """Background step after a pending skip: provision if needed, wait for a top-up, then one more try.

    The retry looks for the verdict id in Checking descriptions first, so a transfer that landed is never repeated.
    """
    verdict = _load_verdict(verdict_id)
    record = (verdict or {}).get("transfer") or {}
    if not verdict or record.get("status") != "pending":
        return
    try:
        user = _load_user(verdict["user_id"])
        if not user or not nessie_active(user):
            raise NessieError("user is not set up for Nessie")
        if not nessie.has_accounts(user):
            user = nessie.ensure_accounts(user)
        if record.get("topup_id"):
            nessie.wait_settled(record["topup_id"], TOPUP_WAIT_S)
        try:
            result = nessie.skip_transfer(user, verdict, Deadline(RETRY_BUDGET_S), check_existing=True)
        except TopUpPending as e:
            nessie.wait_settled(e.deposit_id, TOPUP_WAIT_S)
            result = nessie.skip_transfer(user, verdict, Deadline(RETRY_BUDGET_S), check_existing=True)
        _save_transfer(verdict_id, result)
        from backend.services.goal import forget_savings  # lazy: goal imports this module
        forget_savings(verdict["user_id"])  # the goal's 15 s Savings cache now misses this transfer
    except Exception as e:
        message = nessie.scrub(str(e))[:200]
        log.warning("skip transfer for verdict %s failed after retry: %s", verdict_id, message)
        _save_transfer(verdict_id, {**record, "status": "failed", "error": message, "at": nessie.now()})


# ---------- bought later: undo an earlier skip of the same item ----------
# A skip moved money to Savings. When the shopper buys the same item later (a new check, a new verdict), that
# money did not stay saved: the earlier verdict is marked superseded (it leaves money_kept and the goal's local
# sum) and, for Nessie users, a background transfer moves the same cents back from Savings to Checking.

SAME_ITEM_WINDOW_DAYS = 60


def normalize_url(url) -> str | None:
    """Scheme, lowercase host and path without query, fragment or trailing slash: variants of one product page."""
    from urllib.parse import urlsplit
    if not url:
        return None
    parts = urlsplit(str(url).strip())
    if not parts.netloc:
        return None
    return f"{parts.netloc.lower().removeprefix('www.')}{parts.path.rstrip('/')}".lower()


def same_item(a: dict, b: dict) -> bool:
    """Same product page, else same name at the same store (case-insensitive)."""
    ua, ub = normalize_url(a.get("url")), normalize_url(b.get("url"))
    if ua and ub:
        return ua == ub
    name_a, name_b = (a.get("name") or "").strip().lower(), (b.get("name") or "").strip().lower()
    return bool(name_a) and name_a == name_b and (a.get("store") or "").lower() == (b.get("store") or "").lower()


def _recent_skips(user_id, exclude_id, since) -> list[dict]:
    from backend.db import verdicts
    return list(verdicts.find({"user_id": user_id, "_id": {"$ne": exclude_id}, "action": "skipped",
                               "superseded_by": None, "created_at": {"$gte": since}},
                              {"item": 1, "transfer": 1, "created_at": 1, "user_id": 1}).sort("created_at", -1).limit(100))


def _supersede(verdict_id, by_id) -> bool:
    """Atomically mark a skip superseded by a later purchase. False when it already was (double Buy)."""
    from backend.db import verdicts
    res = verdicts.update_one({"_id": verdict_id, "superseded_by": None},
                              {"$set": {"superseded_by": by_id, "superseded_at": nessie.now()}})
    return res.modified_count == 1


def _save_reversal(verdict_id, record: dict) -> None:
    from backend.db import verdicts
    verdicts.update_one({"_id": verdict_id}, {"$set": {"reversal": record}})


def on_buy(user: dict, verdict: dict, background) -> int | None:
    """Called on "bought". Finds the latest earlier skip of the same item, supersedes it and, when its money moved
    to Nessie Savings, schedules moving it back. Returns the cents being moved back, or None. Never raises."""
    try:
        since = nessie.now() - timedelta(days=SAME_ITEM_WINDOW_DAYS)
        item = verdict.get("item") or {}
        if verdict.get("action") == "skipped":  # this very verdict was skipped first (read before the update)
            earlier = verdict
        else:
            earlier = next((d for d in _recent_skips(user["_id"], verdict["_id"], since)
                            if same_item(item, d.get("item") or {})), None)
        if earlier is None or not _supersede(earlier["_id"], verdict["_id"]):
            return None
        transfer = earlier.get("transfer") or {}
        if transfer.get("status") != "done" or not transfer.get("amount_cents") or not nessie_active(user):
            return None
        record = {"status": "pending", "id": None, "amount_cents": transfer["amount_cents"], "at": nessie.now(),
                  "bought_verdict_id": verdict["_id"]}
        _save_reversal(earlier["_id"], record)
        background.add_task(reverse_skip, earlier["_id"])
        return transfer["amount_cents"]
    except Exception as e:  # the purchase itself is already recorded; money bookkeeping never fails it
        log.warning("bought: earlier skip check failed: %s", nessie.scrub(str(e))[:200])
        return None


def reverse_skip(verdict_id) -> None:
    """Background: move a superseded skip's money back to Checking. Waits for the skip transfer to settle when
    Savings cannot spend it yet, and checks for an existing undo first, so it is never repeated."""
    verdict = _load_verdict(verdict_id)
    record = (verdict or {}).get("reversal") or {}
    if not verdict or record.get("status") != "pending":
        return
    try:
        user = _load_user(verdict["user_id"])
        if not user or not nessie_active(user) or not nessie.has_accounts(user):
            raise NessieError("user is not set up for Nessie")
        try:
            result = nessie.undo_transfer(user, verdict, Deadline(RETRY_BUDGET_S), check_existing=True)
        except NessieError as e:
            if e.problem != "insufficient_funds":
                raise
            skip_tx = (verdict.get("transfer") or {}).get("id")
            if skip_tx:
                nessie.wait_settled(skip_tx, TOPUP_WAIT_S)
            result = nessie.undo_transfer(user, verdict, Deadline(RETRY_BUDGET_S), check_existing=True)
        _save_reversal(verdict_id, {**record, **result})
        from backend.services.goal import forget_savings  # lazy: goal imports this module
        forget_savings(verdict["user_id"])
    except Exception as e:
        message = nessie.scrub(str(e))[:200]
        log.warning("undo for verdict %s failed: %s", verdict_id, message)
        _save_reversal(verdict_id, {**record, "status": "failed", "error": message, "at": nessie.now()})


# ---------- GET /savings and /dashboard ----------

def money_kept(user_id) -> float:
    """Dollars: the sum of item.price over skipped verdicts that were not bought later (superseded). Stays local
    even when Nessie is on."""
    from backend.db import verdicts
    result = list(verdicts.aggregate([{"$match": {"user_id": user_id, "action": "skipped",
                                                  "superseded_by": None}},
                                      {"$group": {"_id": None, "total": {"$sum": "$item.price"}}}]))
    return round(result[0]["total"], 2) if result else 0.0


def savings_summary(user: dict) -> dict:
    """{checking_cents, savings_cents, transfers, source}. Nessie when active and reachable, else local."""
    if nessie_active(user) and nessie.has_accounts(user):
        try:
            deadline = Deadline(nessie.CALL_TIMEOUT_S)
            balances = nessie.get_balances(user, deadline)
            transfers = nessie.list_transfers(user, 20, timeout=deadline.timeout())
            return {**balances, "transfers": transfers, "source": "nessie"}
        except NessieError as e:
            log.info("savings from Nessie unavailable, using local: %s", e)
    return local_savings(user)


def local_savings(user: dict) -> dict:
    from backend.db import verdicts
    docs = verdicts.find({"user_id": user["_id"], "action": "skipped", "item.price": {"$gt": 0},
                          "superseded_by": None},
                         {"item.price": 1, "item.name": 1, "created_at": 1, "transfer": 1}).sort("created_at", -1).limit(20)
    transfers = [{"amount_cents": round(d["item"]["price"] * 100), "description": nessie.verdict_skip_description(d),
                  "at": as_utc((d.get("transfer") or {}).get("at") or d["created_at"])} for d in docs]
    return {"checking_cents": None, "savings_cents": round(money_kept(user["_id"]) * 100), "transfers": transfers,
            "source": "local"}
