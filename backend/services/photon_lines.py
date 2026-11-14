"""Photon shared iMessage lines: give each Second Look account with a phone its own line, automatically.

Facts from Photon's public schema (https://api.photon.codes/openapi.json, Nov 14):
- `POST /v1/projects/{id}/platforms/imessage/assignments {handle, email?}` maps a shopper's phone (E.164) onto
  one of the project's pooled lines and uses one seat. Header `Idempotency-Key` is required. 201 returns
  `{assignmentId, assignedPhoneNumber, handle, optIn: {state: pending|received}, releasedAt?, ...}`.
- Each shopper may get a different line. The shopper must text that line first (opt-in); until then the
  line cannot message them. A fresh assignment is always `pending`.
- When `email` is sent, Photon emails the shopper an invite for the line.
- Errors are problem+json with a stable `code` (mapped in `_raise_for`). Releasing (DELETE) is never called here.

The key only travels in the Authorization header. Every message a caller sees is a fixed, user-safe string,
and anything logged is scrubbed of the key and project id.
"""
import hashlib
import logging
import os
import re
from datetime import datetime, timedelta, timezone

import httpx

from backend.settings import ROOT  # noqa: F401  (loads .env)

log = logging.getLogger("secondlook.photon")

BASE_URL = "https://api.photon.codes/v1"
TIMEOUT_S = 5.0
LIST_PAGE_SIZE = 200
LIST_MAX_PAGES = 10
OPT_IN_REFRESH = timedelta(minutes=1)  # GET /imessage/status asks Photon about opt-in at most this often
ASSIGNING_WINDOW = timedelta(seconds=30)  # after signup, "assigning" shows until the background task finishes
# Photon's email pattern (CreateSharedLineAssignmentRequest.email). Others are left out rather than failing the call.
EMAIL_RE = re.compile(r"^(?:[A-Za-z0-9_'+\-]+\.)*[A-Za-z0-9_'+\-]*[A-Za-z0-9_+-]@(?:[A-Za-z0-9][A-Za-z0-9\-]*\.)+[A-Za-z]{2,}$")

_transport: httpx.BaseTransport | None = None  # tests install an httpx.MockTransport here

# User-safe messages, one per error kind. These reach the extension popup.
MESSAGES = {
    "not_configured": "iMessage is not set up on this server yet.",
    "setup": "iMessage is not set up on this server yet.",
    "seats_full": "All Second Look iMessage lines are taken right now. The extension still works as usual.",
    "pool_exhausted": "Photon has no free iMessage lines right now. Try again later.",
    "invalid_phone": "iMessage could not use this phone number. Check it and try again.",
    "busy": "iMessage setup is busy. Try again in a minute.",
    "unavailable": "iMessage setup is unavailable right now. Try again in a minute.",
}
SETUP_CODES = {"IMESSAGE_MODE_DEDICATED", "PROJECT_CLOSING", "PROJECT_INITIALIZING", "BILLING_NOT_PROVISIONED",
               "BILLING_ENTITLEMENTS_PENDING", "NOT_AUTHENTICATED", "FORBIDDEN", "INSUFFICIENT_SCOPE",
               "ORGANIZATION_SSO_REQUIRED", "RESOURCE_MISMATCH"}


class PhotonLineError(Exception):
    """A failed Photon call. `kind` is a key of MESSAGES; str(err) is the user-safe message for that kind.
    `code` is Photon's problem code when it sent one, `status` the HTTP status."""

    def __init__(self, kind: str, code: str | None = None, status: int | None = None):
        super().__init__(MESSAGES[kind])
        self.kind = kind
        self.code = code
        self.status = status


class PhotonNotConfigured(PhotonLineError):
    def __init__(self):
        super().__init__("not_configured")


class AssignmentGone(PhotonLineError):
    """The assignment was released or no longer exists. The account can connect again."""

    def __init__(self):
        super().__init__("unavailable", code="ASSIGNMENT_NOT_FOUND", status=404)


def _config() -> tuple[str, str] | None:
    project, key = os.getenv("PHOTON_PROJECT_ID", "").strip(), os.getenv("PHOTON_API_KEY", "").strip()
    return (project, key) if project and key else None


def configured() -> bool:
    return _config() is not None


def scrub(text: str) -> str:
    """Remove the API key and project id from text that may be logged."""
    out = str(text)
    for secret in (os.getenv("PHOTON_API_KEY", "").strip(), os.getenv("PHOTON_PROJECT_ID", "").strip()):
        if secret:
            out = out.replace(secret, "***")
    return re.sub(r"pho_sk_[A-Za-z0-9_\-]+", "pho_sk_***", out)


def _client() -> tuple[httpx.Client, str]:
    cfg = _config()
    if cfg is None:
        raise PhotonNotConfigured()
    project, key = cfg
    client = httpx.Client(base_url=f"{BASE_URL}/projects/{project}/platforms/imessage", timeout=TIMEOUT_S,
                          headers={"Authorization": f"Bearer {key}"}, transport=_transport)
    return client, project


def _problem_code(res: httpx.Response) -> str | None:
    try:
        body = res.json()
    except ValueError:
        return None
    return body.get("code") if isinstance(body, dict) else None


def _raise_for(res: httpx.Response, what: str) -> None:
    """Map a non-2xx Photon response to a PhotonLineError."""
    code = _problem_code(res)
    log.warning("photon %s failed: HTTP %d code=%s", what, res.status_code, scrub(code or "-"))
    if code == "SHARED_LINE_CAP_EXCEEDED":
        kind = "seats_full"
    elif code == "SHARED_LINE_POOL_EXHAUSTED":
        kind = "pool_exhausted"
    elif code == "ASSIGNMENT_NOT_FOUND" or res.status_code == 404:
        raise AssignmentGone()
    elif code in SETUP_CODES or res.status_code in (401, 403):
        kind = "setup"
    elif code == "VALIDATION_FAILED" or res.status_code == 422:
        kind = "invalid_phone"
    elif res.status_code == 429:
        kind = "busy"
    else:
        kind = "unavailable"  # 5xx, UPSTREAM_*, timeouts reported by Photon, anything unexpected
    raise PhotonLineError(kind, code=code, status=res.status_code)


def _request(client: httpx.Client, method: str, url: str, what: str, **kwargs) -> httpx.Response:
    try:
        return client.request(method, url, **kwargs)
    except httpx.HTTPError as e:  # timeouts, DNS, refused connections
        log.warning("photon %s failed: %s", what, scrub(type(e).__name__))
        raise PhotonLineError("unavailable") from None


def _shape(a: dict) -> dict:
    state = (a.get("optIn") or {}).get("state")
    return {
        "assignment_id": a["assignmentId"],
        "line": a["assignedPhoneNumber"],
        "handle": a.get("handle"),
        "opt_in": "received" if state == "received" else "pending",  # the schema says: unknown means not opted in
        "released": bool(a.get("releasedAt")),
    }


def idempotency_key(body: dict) -> str:
    """Same phone and body, same key: a retry (or a signup racing a Connect click) replays the first create
    instead of using a second seat. A different body gets a new key, so Photon never answers KEY_REUSED."""
    digest = hashlib.sha256(repr(sorted(body.items())).encode()).hexdigest()[:40]
    return f"secondlook-assign-{digest}"


def _find_by_handle(client: httpx.Client, phone: str) -> dict | None:
    token = None
    for _ in range(LIST_MAX_PAGES):
        params = {"pageSize": LIST_PAGE_SIZE, **({"pageToken": token} if token else {})}
        res = _request(client, "GET", "/assignments", "list", params=params)
        if res.status_code != 200:
            _raise_for(res, "list")
        page = res.json()
        for a in page.get("assignments") or []:
            if a.get("handle") == phone and not a.get("releasedAt"):
                return a
        token = page.get("nextPageToken")
        if not token:
            return None
    return None


def assign_phone(phone: str, email: str | None = None, first_name: str | None = None) -> dict:
    """Give this phone (E.164) a shared line. Idempotent per phone: when Photon says it is already assigned,
    the existing assignment is returned. Returns {assignment_id, line, handle, opt_in, released}.
    Raises PhotonLineError (PhotonNotConfigured makes no network call)."""
    client, _ = _client()
    body = {"handle": phone}
    if email and EMAIL_RE.match(email) and len(email) <= 254:
        body["email"] = email
    if first_name and first_name.strip():
        body["firstName"] = first_name.strip()[:100]
    with client:
        res = _request(client, "POST", "/assignments", "assign", json=body,
                       headers={"Idempotency-Key": idempotency_key(body)})
        if res.status_code in (200, 201):
            return _shape(res.json())
        if _problem_code(res) in ("ASSIGNMENT_EXISTS", "IDEMPOTENCY_KEY_REUSED"):
            existing = _find_by_handle(client, phone)
            if existing:
                return _shape(existing)
        _raise_for(res, "assign")


def get_assignment(assignment_id: str) -> dict:
    """One assignment, for its opt-in state. Raises AssignmentGone when Photon no longer has it."""
    client, _ = _client()
    with client:
        res = _request(client, "GET", f"/assignments/{assignment_id}", "get")
        if res.status_code != 200:
            _raise_for(res, "get")
        shaped = _shape(res.json())
    if shaped["released"]:
        raise AssignmentGone()
    return shaped


# ---------- account level: the users document ----------
#
# Fields on users: photon_assignment_id, photon_line (the assigned number, E.164), photon_handle (the phone it
# was assigned for), photon_opt_in (pending | received), photon_assigned_at, photon_checked_at (last opt-in
# refresh), photon_error (last user-safe error, cleared on success), photon_assigning_at (set at signup while
# the background assignment runs). The key is never stored.

ASSIGNMENT_FIELDS = ("photon_assignment_id", "photon_line", "photon_handle", "photon_opt_in", "photon_assigned_at",
                     "photon_checked_at")


def _users():
    from backend.db import users  # imported late so the Photon client stays usable without MongoDB
    return users


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


def has_assignment(user: dict) -> bool:
    """An assignment counts only for the phone it was made for (the phone may have changed or moved since)."""
    return bool(user.get("photon_assignment_id") and user.get("phone") and user.get("photon_handle") == user["phone"])


def assign_user(user_id) -> dict:
    """Assign a line to this account's phone and store the result on the users document. Never raises:
    a failure is stored as photon_error. Returns the account's status (see `status_for`)."""
    users = _users()
    user = users.find_one({"_id": user_id})
    if user is None:
        return {"status": "error", "line": None, "message": "Account not found."}
    if not user.get("phone"):
        return status_for(user)
    if has_assignment(user) and not user.get("photon_error"):
        return status_for(user)
    try:
        # No email: Photon would send the shopper an invite email. The popup shows the line instead.
        a = assign_phone(user["phone"])
    except PhotonLineError as e:
        update = {"$set": {"photon_error": str(e)}, "$unset": {"photon_assigning_at": ""}}
    else:
        now = _now()
        update = {"$set": {"photon_assignment_id": a["assignment_id"], "photon_line": a["line"],
                           "photon_handle": user["phone"], "photon_opt_in": a["opt_in"], "photon_assigned_at": now,
                           "photon_checked_at": now},
                  "$unset": {"photon_error": "", "photon_assigning_at": ""}}
    users.update_one({"_id": user["_id"]}, update)
    return status_for(users.find_one({"_id": user["_id"]}))


def assign_user_quietly(user_id) -> None:
    """Background task after signup. Must never raise into the server."""
    try:
        assign_user(user_id)
    except Exception as e:  # noqa: BLE001  (a database hiccup here must not surface anywhere)
        log.warning("photon background assign failed: %s", scrub(e))
        try:
            _users().update_one({"_id": user_id}, {"$unset": {"photon_assigning_at": ""}})
        except Exception:  # noqa: BLE001
            pass


def refresh_opt_in(user: dict) -> dict:
    """Ask Photon whether the shopper has texted the line yet, at most once per OPT_IN_REFRESH.
    Returns the (possibly updated) users document. Photon failures keep the stored state."""
    if not has_assignment(user) or user.get("photon_opt_in") == "received" or not configured():
        return user
    checked = _aware(user.get("photon_checked_at"))
    if checked and _now() - checked < OPT_IN_REFRESH:
        return user
    users = _users()
    update: dict = {"$set": {"photon_checked_at": _now()}}
    try:
        a = get_assignment(user["photon_assignment_id"])
    except AssignmentGone:
        update = {"$unset": {f: "" for f in ASSIGNMENT_FIELDS}}  # released in the dashboard: connect again
    except PhotonLineError:
        pass  # keep what we know; try again after OPT_IN_REFRESH
    else:
        update["$set"]["photon_opt_in"] = a["opt_in"]
        if a["line"] != user.get("photon_line"):
            update["$set"]["photon_line"] = a["line"]
    users.update_one({"_id": user["_id"]}, update)
    return users.find_one({"_id": user["_id"]}) or user


def status_for(user: dict) -> dict:
    """{status, line, message, phone}. status is one of:
    none (no line yet), assigning (signup's background assignment is running), pending_optin (the shopper
    must text `line` first), connected, error (message says why)."""
    phone = user.get("phone")
    if has_assignment(user):
        line = user["photon_line"]
        if user.get("photon_opt_in") == "received":
            return {"status": "connected", "line": line, "phone": phone, "message": "iMessage connected."}
        return {"status": "pending_optin", "line": line, "phone": phone,
                "message": "Text Second Look at this number to start."}
    if not phone:
        return {"status": "none", "line": None, "phone": None, "message": "Add your phone number to use iMessage."}
    started = _aware(user.get("photon_assigning_at"))
    if started and _now() - started < ASSIGNING_WINDOW:
        return {"status": "assigning", "line": None, "phone": phone, "message": "Setting up your iMessage line."}
    if user.get("photon_error"):
        return {"status": "error", "line": None, "phone": phone, "message": user["photon_error"]}
    return {"status": "none", "line": None, "phone": phone, "message": "Connect iMessage to text Second Look photos."}
