"""Skip it moves money: the /action skip path, its background retry, and the GET /savings summary.

Only users with `nessie_enabled: true` (set by scripts/setup_nessie_demo.py) touch Nessie. Everyone else,
including the throwaway users the extension E2E creates, gets the local path: no Nessie calls, placeholder
balances, savings computed from skipped verdicts.

Stored on the verdict: `transfer {status done|pending|failed|no_price, id, amount_cents, at}` plus, when
relevant, `topup_id` and `error` (scrubbed). Nothing here ever raises to the shopper.
"""
import logging

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
    except Exception as e:
        message = nessie.scrub(str(e))[:200]
        log.warning("skip transfer for verdict %s failed after retry: %s", verdict_id, message)
        _save_transfer(verdict_id, {**record, "status": "failed", "error": message, "at": nessie.now()})


# ---------- GET /savings and /dashboard ----------

def money_kept(user_id) -> float:
    """Dollars: the sum of item.price over skipped verdicts. Stays local even when Nessie is on."""
    from backend.db import verdicts
    result = list(verdicts.aggregate([{"$match": {"user_id": user_id, "action": "skipped"}},
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
    docs = verdicts.find({"user_id": user["_id"], "action": "skipped", "item.price": {"$gt": 0}},
                         {"item.price": 1, "item.name": 1, "created_at": 1, "transfer": 1}).sort("created_at", -1).limit(20)
    transfers = [{"amount_cents": round(d["item"]["price"] * 100), "description": nessie.verdict_skip_description(d),
                  "at": as_utc((d.get("transfer") or {}).get("at") or d["created_at"])} for d in docs]
    return {"checking_cents": None, "savings_cents": round(money_kept(user["_id"]) * 100), "transfers": transfers,
            "source": "local"}
