"""Offline tests for automatic Photon iMessage line assignment: backend/services/photon_lines.py, signup's
background assignment, POST /imessage/connect and GET /imessage/status. Photon is an httpx.MockTransport and
MongoDB is a small in-memory collection. No network, no real assignment.

Run from the project root:  python -m unittest discover tests
"""
import json
import logging
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
from bson import ObjectId  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pymongo.errors import DuplicateKeyError  # noqa: E402

from backend import main  # noqa: E402
from backend.auth import create_token  # noqa: E402
from backend.routers import imessage as router  # noqa: E402
from backend.services import photon_lines as pl  # noqa: E402

logging.getLogger("secondlook.photon").setLevel(logging.CRITICAL)

KEY = "pho_sk_test_SECRET_key_123"
PROJECT = "proj_test_abc"
PHONE = "+15550100001"
LINE = "+15550199999"
ENV = {"PHOTON_API_KEY": KEY, "PHOTON_PROJECT_ID": PROJECT}


def assignment(handle=PHONE, line=LINE, state="pending", aid="asn_1", released=False):
    a = {"assignmentId": aid, "assignedPhoneNumber": line, "handle": handle, "optIn": {"state": state},
         "platform": "imessage", "projectId": PROJECT, "createdAt": "2026-11-14T00:00:00.000Z",
         "updatedAt": "2026-11-14T00:00:00.000Z"}
    if state == "received":
        a["optIn"]["receivedAt"] = "2026-11-14T00:01:00.000Z"
    if released:
        a["releasedAt"] = "2026-11-14T00:02:00.000Z"
    return a


def problem(status, code):
    return httpx.Response(status, json={"code": code, "status": status, "title": code, "type": "x",
                                        "detail": f"detail mentioning {KEY}"},
                          headers={"content-type": "application/problem+json"})


class Photon:
    """Scripted Photon. `routes` maps (method, path suffix) to a Response or a callable(request)."""

    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    def __call__(self, request: httpx.Request):
        self.requests.append(request)
        path = request.url.path
        for (method, suffix), answer in self.routes.items():
            if request.method == method and path.endswith(suffix):
                return answer(request) if callable(answer) else answer
        return httpx.Response(500, json={"code": "INTERNAL_ERROR"})


class FakeUsers:
    """Enough of a pymongo collection for these paths: equality and $ne filters, $set and $unset."""

    def __init__(self, docs=None):
        self.docs = list(docs or [])

    @staticmethod
    def matches(doc, filt):
        for k, v in filt.items():
            if isinstance(v, dict) and "$ne" in v:
                if doc.get(k) == v["$ne"]:
                    return False
            elif doc.get(k) != v:
                return False
        return True

    def find_one(self, filt=None, *args, **kwargs):
        return next((d for d in self.docs if self.matches(d, filt or {})), None)

    def insert_one(self, doc):
        for d in self.docs:
            if d["email"] == doc["email"] or (doc.get("phone") and d.get("phone") == doc.get("phone")):
                key = "phone" if d["email"] != doc["email"] else "email"
                raise DuplicateKeyError("dup", details={"keyPattern": {key: 1}})
        doc.setdefault("_id", ObjectId())
        self.docs.append(doc)
        return mock.Mock(inserted_id=doc["_id"])

    def update_one(self, filt, update):
        doc = self.find_one(filt)
        if doc is None:
            return
        new_phone = update.get("$set", {}).get("phone")
        if new_phone and any(d is not doc and d.get("phone") == new_phone for d in self.docs):
            raise DuplicateKeyError("dup", details={"keyPattern": {"phone": 1}})
        doc.update(update.get("$set", {}))
        for k in update.get("$unset", {}):
            doc.pop(k, None)


class Base(unittest.TestCase):
    def setUp(self):
        self.users = FakeUsers()
        for p in (mock.patch.dict(os.environ, ENV),
                  mock.patch.object(pl, "_users", lambda: self.users),
                  mock.patch.object(router, "users", self.users),
                  mock.patch.object(main, "users", self.users)):
            p.start()
            self.addCleanup(p.stop)

    def photon(self, routes):
        fake = Photon(routes)
        p = mock.patch.object(pl, "_transport", httpx.MockTransport(fake))
        p.start()
        self.addCleanup(p.stop)
        return fake

    def add_user(self, **fields):
        doc = {"_id": ObjectId(), "email": "test_autoassign@secondlook.test", "profile_type": "real",
               "created_at": datetime.now(timezone.utc), "password_hash": "x", **fields}
        self.users.docs.append(doc)
        return doc


class AssignPhoneTest(Base):
    def test_success_shape_and_request(self):
        fake = self.photon({("POST", "/assignments"): httpx.Response(201, json=assignment())})
        out = pl.assign_phone(PHONE, email="shopper@example.com")
        self.assertEqual(out, {"assignment_id": "asn_1", "line": LINE, "handle": PHONE, "opt_in": "pending",
                               "released": False})
        req = fake.requests[0]
        self.assertEqual(req.url.path, f"/v1/projects/{PROJECT}/platforms/imessage/assignments")
        self.assertEqual(req.headers["authorization"], f"Bearer {KEY}")
        self.assertEqual(json.loads(req.content), {"handle": PHONE, "email": "shopper@example.com"})
        key = req.headers["idempotency-key"]
        self.assertTrue(1 <= len(key) <= 255 and all(33 <= ord(c) <= 126 for c in key))

    def test_idempotency_key_is_stable_per_phone_and_body(self):
        a = pl.idempotency_key({"handle": PHONE})
        self.assertEqual(a, pl.idempotency_key({"handle": PHONE}))
        self.assertNotEqual(a, pl.idempotency_key({"handle": "+15550100002"}))
        self.assertNotIn(PHONE, a)

    def test_email_that_photon_would_reject_is_left_out(self):
        fake = self.photon({("POST", "/assignments"): httpx.Response(201, json=assignment())})
        pl.assign_phone(PHONE, email="not an email")
        self.assertEqual(json.loads(fake.requests[0].content), {"handle": PHONE})

    def test_already_assigned_resolves_to_the_existing_one(self):
        page1 = {"assignments": [assignment(handle="+15550100009", aid="asn_other")], "nextPageToken": "t2"}
        page2 = {"assignments": [assignment(aid="asn_old", released=True), assignment(aid="asn_live", state="received")]}
        fake = self.photon({
            ("POST", "/assignments"): problem(409, "ASSIGNMENT_EXISTS"),
            ("GET", "/assignments"): lambda r: httpx.Response(200, json=page2 if r.url.params.get("pageToken") else page1),
        })
        out = pl.assign_phone(PHONE)
        self.assertEqual((out["assignment_id"], out["opt_in"]), ("asn_live", "received"))
        self.assertEqual([r.method for r in fake.requests], ["POST", "GET", "GET"])

    def test_error_codes_map_to_safe_kinds(self):
        cases = [
            (409, "SHARED_LINE_CAP_EXCEEDED", "seats_full"),
            (503, "SHARED_LINE_POOL_EXHAUSTED", "pool_exhausted"),
            (503, "BILLING_NOT_PROVISIONED", "setup"),
            (409, "IMESSAGE_MODE_DEDICATED", "setup"),
            (409, "PROJECT_INITIALIZING", "setup"),
            (401, "NOT_AUTHENTICATED", "setup"),
            (403, "INSUFFICIENT_SCOPE", "setup"),
            (422, "VALIDATION_FAILED", "invalid_phone"),
            (429, "RATE_LIMITED", "busy"),
            (500, "INTERNAL_ERROR", "unavailable"),
            (502, "UPSTREAM_FAILURE", "unavailable"),
            (504, "UPSTREAM_TIMEOUT", "unavailable"),
        ]
        for status, code, kind in cases:
            self.photon({("POST", "/assignments"): problem(status, code)})
            with self.assertRaises(pl.PhotonLineError, msg=code) as ctx:
                pl.assign_phone(PHONE)
            self.assertEqual((ctx.exception.kind, ctx.exception.code, ctx.exception.status), (kind, code, status))
            self.assertEqual(str(ctx.exception), pl.MESSAGES[kind])
            self.assertNotIn(KEY, str(ctx.exception))
            self.assertNotIn(PROJECT, str(ctx.exception))

    def test_assignment_exists_but_not_in_list_is_an_error(self):
        self.photon({("POST", "/assignments"): problem(409, "ASSIGNMENT_EXISTS"),
                     ("GET", "/assignments"): httpx.Response(200, json={"assignments": []})})
        with self.assertRaises(pl.PhotonLineError) as ctx:
            pl.assign_phone(PHONE)
        self.assertEqual(ctx.exception.kind, "unavailable")

    def test_timeout_and_network_errors(self):
        for exc in (httpx.ReadTimeout(f"timed out {KEY}"), httpx.ConnectError(f"refused {KEY}")):
            def boom(request, exc=exc):
                raise exc
            self.photon({("POST", "/assignments"): boom})
            with self.assertLogs("secondlook.photon", level="WARNING") as logs, \
                    self.assertRaises(pl.PhotonLineError) as ctx:
                pl.assign_phone(PHONE)
            self.assertEqual(ctx.exception.kind, "unavailable")
            self.assertNotIn(KEY, str(ctx.exception) + "".join(logs.output))
            self.assertIsNone(ctx.exception.__cause__)

    def test_not_configured_makes_no_call(self):
        fake = self.photon({})
        with mock.patch.dict(os.environ, {"PHOTON_API_KEY": ""}):
            self.assertFalse(pl.configured())
            with self.assertRaises(pl.PhotonNotConfigured) as ctx:
                pl.assign_phone(PHONE)
        self.assertEqual(ctx.exception.kind, "not_configured")
        self.assertEqual(fake.requests, [])

    def test_get_assignment(self):
        self.photon({("GET", "/assignments/asn_1"): httpx.Response(200, json=assignment(state="received"))})
        self.assertEqual(pl.get_assignment("asn_1")["opt_in"], "received")
        self.photon({("GET", "/assignments/asn_1"): httpx.Response(200, json=assignment(released=True))})
        with self.assertRaises(pl.AssignmentGone):
            pl.get_assignment("asn_1")
        self.photon({("GET", "/assignments/asn_1"): problem(404, "ASSIGNMENT_NOT_FOUND")})
        with self.assertRaises(pl.AssignmentGone):
            pl.get_assignment("asn_1")

    def test_scrub(self):
        self.assertEqual(pl.scrub(f"x {KEY} y {PROJECT} z pho_sk_other"), "x *** y *** z pho_sk_***")


class SignupTest(Base):
    def setUp(self):
        super().setUp()
        self.client = TestClient(main.app)

    def signup(self, **extra):
        return self.client.post("/auth/signup", json={"email": "test_autoassign@secondlook.test",
                                                      "password": "secret123", **extra})

    def test_signup_with_phone_assigns_in_background(self):
        fake = self.photon({("POST", "/assignments"): httpx.Response(201, json=assignment())})
        r = self.signup(phone="(555) 010-0001")
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json()["user"]["imessage_line"])  # assigned after the response
        self.assertEqual(len(fake.requests), 1)  # TestClient runs background tasks before returning
        # The account email is never sent, so Photon sends the shopper no invite email.
        self.assertEqual(json.loads(fake.requests[0].content), {"handle": PHONE})
        doc = self.users.docs[0]
        self.assertEqual((doc["photon_assignment_id"], doc["photon_line"], doc["photon_handle"], doc["photon_opt_in"]),
                         ("asn_1", LINE, PHONE, "pending"))
        self.assertIn("photon_assigned_at", doc)
        self.assertNotIn("photon_error", doc)
        self.assertNotIn("photon_assigning_at", doc)
        self.assertNotIn(KEY, json.dumps(doc, default=str))

    def test_photon_down_signup_still_succeeds_and_error_is_stored(self):
        def boom(request):
            raise httpx.ReadTimeout("slow")
        self.photon({("POST", "/assignments"): boom})
        r = self.signup(phone=PHONE)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["token"])
        doc = self.users.docs[0]
        self.assertEqual(doc["photon_error"], pl.MESSAGES["unavailable"])
        self.assertNotIn("photon_assignment_id", doc)
        self.assertNotIn("photon_assigning_at", doc)

    def test_database_failure_in_background_never_raises(self):
        self.photon({("POST", "/assignments"): httpx.Response(201, json=assignment())})
        with mock.patch.object(pl, "assign_user", side_effect=RuntimeError("db down")):
            r = self.signup(phone=PHONE)
        self.assertEqual(r.status_code, 200)

    def test_no_phone_or_not_configured_makes_no_call(self):
        fake = self.photon({})
        self.assertEqual(self.signup().status_code, 200)
        self.users.docs.clear()
        with mock.patch.dict(os.environ, {"PHOTON_PROJECT_ID": ""}):
            self.assertEqual(self.signup(phone=PHONE).status_code, 200)
        self.assertEqual(fake.requests, [])
        self.assertNotIn("photon_assigning_at", self.users.docs[0])


class EndpointTest(Base):
    def setUp(self):
        super().setUp()
        self.client = TestClient(main.app)

    def auth(self, user):
        return {"Authorization": f"Bearer {create_token(str(user['_id']))}"}

    def patch_auth_users(self):
        p = mock.patch("backend.auth.users", self.users)
        p.start()
        self.addCleanup(p.stop)

    def test_auth_required(self):
        self.assertEqual(self.client.post("/imessage/connect").status_code, 401)
        self.assertEqual(self.client.get("/imessage/status").status_code, 401)
        self.assertEqual(self.client.get("/imessage/status", headers={"Authorization": "Bearer nope"}).status_code, 401)

    def test_connect_without_phone_400(self):
        self.patch_auth_users()
        user = self.add_user()
        r = self.client.post("/imessage/connect", headers=self.auth(user))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.client.get("/imessage/status", headers=self.auth(user)).json(),
                         {"status": "none", "line": None, "phone": None, "message": "Add your phone number to use iMessage."})

    def test_connect_assigns_then_status_refreshes_opt_in(self):
        self.patch_auth_users()
        user = self.add_user(phone=PHONE)
        fake = self.photon({("POST", "/assignments"): httpx.Response(201, json=assignment()),
                            ("GET", "/assignments/asn_1"): httpx.Response(200, json=assignment(state="received"))})
        r = self.client.post("/imessage/connect", headers=self.auth(user))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"status": "pending_optin", "line": LINE, "phone": PHONE,
                                    "message": "Text Second Look at this number to start."})
        # Checked just now: status does not call Photon again within a minute.
        self.assertEqual(self.client.get("/imessage/status", headers=self.auth(user)).json()["status"], "pending_optin")
        self.assertEqual(len(fake.requests), 1)
        user["photon_checked_at"] = datetime.now(timezone.utc) - timedelta(minutes=2)
        r = self.client.get("/imessage/status", headers=self.auth(user))
        self.assertEqual(r.json(), {"status": "connected", "line": LINE, "phone": PHONE, "message": "iMessage connected."})
        self.assertEqual(len(fake.requests), 2)
        # Connected: no more Photon calls, and a second connect is a no-op.
        user["photon_checked_at"] = datetime.now(timezone.utc) - timedelta(minutes=5)
        self.client.get("/imessage/status", headers=self.auth(user))
        self.assertEqual(self.client.post("/imessage/connect", headers=self.auth(user)).json()["status"], "connected")
        self.assertEqual(len(fake.requests), 2)
        me = self.client.get("/auth/me", headers=self.auth(user)).json()["user"]
        self.assertEqual(me["imessage_line"], LINE)

    def test_status_refresh_failure_keeps_state_and_released_clears_it(self):
        self.patch_auth_users()
        old = datetime.now(timezone.utc) - timedelta(minutes=2)
        user = self.add_user(phone=PHONE, photon_assignment_id="asn_1", photon_line=LINE, photon_handle=PHONE,
                             photon_opt_in="pending", photon_checked_at=old)
        self.photon({("GET", "/assignments/asn_1"): problem(503, "UPSTREAM_UNAVAILABLE")})
        self.assertEqual(self.client.get("/imessage/status", headers=self.auth(user)).json()["status"], "pending_optin")
        self.assertNotIn("photon_error", user)
        user["photon_checked_at"] = old
        self.photon({("GET", "/assignments/asn_1"): problem(404, "ASSIGNMENT_NOT_FOUND")})
        self.assertEqual(self.client.get("/imessage/status", headers=self.auth(user)).json()["status"], "none")
        self.assertNotIn("photon_assignment_id", user)

    def test_seats_full_is_a_200_error_status(self):
        self.patch_auth_users()
        user = self.add_user(phone=PHONE)
        self.photon({("POST", "/assignments"): problem(409, "SHARED_LINE_CAP_EXCEEDED")})
        r = self.client.post("/imessage/connect", headers=self.auth(user))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"status": "error", "line": None, "phone": PHONE, "message": pl.MESSAGES["seats_full"]})
        self.assertNotIn(KEY, r.text)
        # A later success clears the stored error.
        self.photon({("POST", "/assignments"): httpx.Response(201, json=assignment())})
        self.assertEqual(self.client.post("/imessage/connect", headers=self.auth(user)).json()["status"], "pending_optin")
        self.assertNotIn("photon_error", user)

    def test_not_configured(self):
        self.patch_auth_users()
        user = self.add_user(phone=PHONE)
        fake = self.photon({})
        with mock.patch.dict(os.environ, {"PHOTON_API_KEY": ""}):
            r = self.client.post("/imessage/connect", headers=self.auth(user))
        self.assertEqual(r.json()["status"], "error")
        self.assertEqual(r.json()["message"], pl.MESSAGES["not_configured"])
        self.assertEqual(fake.requests, [])

    def test_connect_with_phone_sets_it_and_rejects_taken_or_invalid(self):
        self.patch_auth_users()
        user = self.add_user()
        self.add_user(email="other@secondlook.test", phone="+15550100002")
        self.photon({("POST", "/assignments"): httpx.Response(201, json=assignment())})
        r = self.client.post("/imessage/connect", json={"phone": "+15550100002"}, headers=self.auth(user))
        self.assertEqual(r.status_code, 409)
        r = self.client.post("/imessage/connect", json={"phone": "12"}, headers=self.auth(user))
        self.assertEqual(r.status_code, 422)
        r = self.client.post("/imessage/connect", json={"phone": "(555) 010-0001"}, headers=self.auth(user))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(user["phone"], PHONE)
        self.assertEqual(r.json()["status"], "pending_optin")

    def test_assignment_for_an_old_phone_does_not_count(self):
        self.patch_auth_users()
        user = self.add_user(phone="+15550100003", photon_assignment_id="asn_1", photon_line=LINE,
                             photon_handle=PHONE, photon_opt_in="received")
        self.photon({})
        r = self.client.get("/imessage/status", headers=self.auth(user)).json()
        self.assertEqual((r["status"], r["line"]), ("none", None))

    def test_assigning_window_after_signup(self):
        self.patch_auth_users()
        user = self.add_user(phone=PHONE, photon_assigning_at=datetime.now(timezone.utc))
        self.assertEqual(self.client.get("/imessage/status", headers=self.auth(user)).json()["status"], "assigning")
        user["photon_assigning_at"] = datetime.now(timezone.utc) - timedelta(minutes=5)
        self.assertEqual(self.client.get("/imessage/status", headers=self.auth(user)).json()["status"], "none")


if __name__ == "__main__":
    unittest.main()
