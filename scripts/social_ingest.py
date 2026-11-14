"""Bounded, resumable public Bluesky search ingestion. Never uses Nessie.

Run: .venv/bin/python scripts/social_ingest.py --probe-only
Then: .venv/bin/python scripts/social_ingest.py --max-minutes 90
State is in ignored .social_runs/. No raw API responses are saved.
"""
from __future__ import annotations

import argparse
import calendar
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from dotenv import dotenv_values, load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
BASE = "https://api.bsky.app/xrpc/"
STATE_PATH = ROOT / ".social_runs/ingest_state.json"
PROBE_PATH = ROOT / ".social_runs/probe.json"
MAX_POSTS = 35000
UTC = timezone.utc


def iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def utc_now() -> datetime:
    return datetime.now(UTC)


def load_json(path: Path, fallback):
    return json.loads(path.read_text()) if path.exists() else fallback


def save_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def normalize_times(collection, cap=MAX_POSTS) -> dict:
    """Canonicalize at most 35000 saved timestamps without altering text or hashes."""
    from pymongo import UpdateOne
    processed, modified, invalid, operations = 0, 0, 0, []
    for post in collection.find({}, {"uri": 1, "created_at": 1, "ingested_at": 1}).limit(min(cap, MAX_POSTS)):
        processed += 1
        updates = {}
        for field in ("created_at", "ingested_at"):
            try:
                value = post.get(field)
                normalized = iso(value if isinstance(value, datetime) else parse_time(value))
                if normalized != value:
                    updates[field] = normalized
            except (ValueError, TypeError, AttributeError):
                invalid += 1
        if updates:
            operations.append(UpdateOne({"uri": post["uri"]}, {"$set": updates}))
        if len(operations) >= 500:
            result = collection.bulk_write(operations, ordered=True)
            modified += result.modified_count
            operations = []
    if operations:
        modified += collection.bulk_write(operations, ordered=True).modified_count
    return {"processed": processed, "modified": modified, "invalid_fields": invalid, "cap": min(cap, MAX_POSTS)}


def get_salt() -> str:
    """Create one stable local secret, with no value emitted to logs."""
    load_dotenv(ROOT / ".env")
    salt = os.environ.get("SOCIAL_HASH_SALT") or dotenv_values(ROOT / ".env").get("SOCIAL_HASH_SALT")
    if not salt:
        salt = secrets.token_hex(32)
        path = ROOT / ".env"
        existing = path.read_text() if path.exists() else ""
        # Preserve every unrelated environment entry and replace a blank placeholder.
        if re.search(r"(?m)^SOCIAL_HASH_SALT=", existing):
            existing = re.sub(r"(?m)^SOCIAL_HASH_SALT=.*$", "SOCIAL_HASH_SALT=" + salt, existing)
        else:
            existing = existing.rstrip("\n") + "\nSOCIAL_HASH_SALT=" + salt + "\n"
        path.write_text(existing)
    os.environ["SOCIAL_HASH_SALT"] = salt
    return salt


def author_hash(did: str | None, salt: str) -> str | None:
    return hmac.new(salt.encode(), did.encode(), hashlib.sha256).hexdigest() if did else None


def uri_did(uri: str | None) -> str | None:
    if not uri or not uri.startswith("at://did:"):
        return None
    return uri[5:].split("/", 1)[0]


def normalize_post(post: dict, term: str, salt: str, store_text: bool = True,
                   ingested_at: str | None = None) -> dict | None:
    record = post.get("record") or {}
    uri = post.get("uri")
    did = (post.get("author") or {}).get("did") or uri_did(uri)
    created = record.get("createdAt") or post.get("indexedAt")
    if not uri or not did or not created:
        return None
    try:
        created = iso(parse_time(created))
    except (TypeError, ValueError):
        return None
    reply = record.get("reply") or {}
    parent = (reply.get("parent") or {}).get("uri")
    mentions = []
    for facet in record.get("facets") or []:
        for feature in facet.get("features") or []:
            if feature.get("$type") == "app.bsky.richtext.facet#mention" and feature.get("did"):
                mentions.append(author_hash(feature["did"], salt))
    embed = record.get("embed") or {}
    quote = None
    if embed.get("$type") == "app.bsky.embed.record":
        quote = (embed.get("record") or {}).get("uri")
    elif embed.get("$type") == "app.bsky.embed.recordWithMedia":
        quote = ((embed.get("record") or {}).get("record") or {}).get("uri")
    def count(key):
        return max(0, int(post.get(key) or 0))
    return {"uri": uri, "author_hash": author_hash(did, salt), "created_at": created,
            "text": record.get("text", "") if store_text else "", "term": term, "terms": [term],
            "reply_parent_uri": parent, "reply_parent_author_hash": author_hash(uri_did(parent), salt),
            "mention_author_hashes": sorted(set(mentions)), "quote_uri": quote,
            "quote_author_hash": author_hash(uri_did(quote), salt), "like_count": count("likeCount"),
            "repost_count": count("repostCount"), "reply_count": count("replyCount"),
            "ingested_at": ingested_at or iso(utc_now())}


class BudgetExceeded(Exception):
    pass


class ApiFailure(Exception):
    """Contains only a safe status, never a URL or response payload."""


class BlueskyClient:
    def __init__(self, client=None, *, max_requests=None, deadline=None, interval=1.0,
                 sleep=time.sleep, monotonic=time.monotonic):
        self.client = client or httpx.Client(timeout=20.0, headers={"User-Agent": "SocialTrendRadar/0.1"})
        self.max_requests, self.deadline = max_requests, deadline
        self.interval, self.sleep, self.monotonic = max(1.0, interval), sleep, monotonic
        self.requests, self.last_request, self.rate_limits = 0, None, 0
        self.statuses = Counter()

    def get(self, endpoint: str, params: dict) -> dict:
        for attempt in range(7):
            if self.max_requests is not None and self.requests >= self.max_requests:
                raise BudgetExceeded("request budget reached")
            current = self.monotonic()
            if self.deadline is not None and current >= self.deadline:
                raise BudgetExceeded("wall time budget reached")
            delay = max(0, self.interval - (current - self.last_request)) if self.last_request is not None else 0
            if self.deadline is not None and current + delay >= self.deadline:
                raise BudgetExceeded("wall time budget reached")
            if delay:
                self.sleep(delay)
            self.last_request = self.monotonic()
            self.requests += 1
            try:
                response = self.client.get(BASE + endpoint, params=params)
            except httpx.HTTPError:
                raise ApiFailure("network_error") from None
            self.statuses[str(response.status_code)] += 1
            if response.status_code == 429:
                self.rate_limits += 1
                try:
                    retry = float(response.headers.get("Retry-After", "0"))
                except ValueError:
                    retry = 0
                wait = max(2 ** attempt, retry)
                if self.deadline is not None and self.monotonic() + wait >= self.deadline:
                    raise BudgetExceeded("wall time budget reached during backoff")
                self.sleep(wait)
                continue
            if response.status_code != 200:
                raise ApiFailure("http_" + str(response.status_code))
            try:
                result = response.json()
            except (ValueError, TypeError):
                raise ApiFailure("non_json_response") from None
            if not isinstance(result, dict):
                raise ApiFailure("invalid_response")
            return result
        raise ApiFailure("rate_limit_retries_exhausted")


def probe(client: BlueskyClient, previous: dict | None = None) -> dict:
    """At most 20 physical requests, including retries. Retain no author identities."""
    if previous and (previous.get("cursor_pagination", {}).get("passed") or previous.get("keyset_pagination", {}).get("passed")) and all(
            previous.get("endpoints", {}).get(name, {}).get("passed") for name in
            ["app.bsky.feed.searchPosts", "app.bsky.feed.getPostThread", "app.bsky.feed.getRepostedBy", "app.bsky.feed.getQuotes", "app.bsky.graph.getFollows"]):
        return previous
    result = {"checked_at": iso(utc_now()), "host": "api.bsky.app", "endpoints": {},
              "cursor_pagination": {"passed": False}, "requests": 0}
    prior_requests = (previous or {}).get("requests", 0)
    offline_attempts = sum(a.get("error") == "network_error" for a in (previous or {}).get("cursor_pagination", {}).get("attempts", []))
    prior_requests -= offline_attempts
    result["offline_connection_failures"] = (previous or {}).get("offline_connection_failures", 0) + offline_attempts
    if previous:
        result["endpoints"] = dict(previous.get("endpoints", {}))
    original_limit = client.max_requests
    remaining = max(0, 20 - prior_requests)
    client.max_requests = min(client.requests + remaining, original_limit) if original_limit is not None else client.requests + remaining
    start_requests = client.requests
    sample = []
    end = utc_now()
    attempts = []
    phrases = [] if remaining <= 4 else ["chinos", "ballet flats", "jeans", "cargo pants"]
    for phrase in phrases:
        params = {"q": '"' + phrase + '"', "sort": "latest", "since": iso(end - timedelta(days=365)),
                  "until": iso(end), "limit": 5}
        detail = {"phrase": phrase}
        try:
            first = client.get("app.bsky.feed.searchPosts", params)
            page = first.get("posts") or []
            sample += page
            result["endpoints"]["app.bsky.feed.searchPosts"] = {"passed": isinstance(first.get("posts"), list), "status": "200"}
            cursor = first.get("cursor")
            detail.update(first_page_count=len(page), has_cursor=bool(cursor))
            if cursor:
                second = client.get("app.bsky.feed.searchPosts", {**params, "cursor": cursor})
                one = {p.get("uri") for p in page}
                two = {p.get("uri") for p in second.get("posts") or []}
                detail.update(second_page_count=len(two), new_on_second_page=len(two - one),
                              cursor_changed=second.get("cursor") != cursor)
                sample += second.get("posts") or []
                if two - one:
                    result["cursor_pagination"]["passed"] = True
        except (ApiFailure, BudgetExceeded) as error:
            detail["error"] = str(error)
            result["endpoints"].setdefault("app.bsky.feed.searchPosts", {"passed": False, "status": str(error)})
        attempts.append(detail)
        result["cursor_pagination"]["attempts"] = attempts
        if result["cursor_pagination"]["passed"] or client.requests >= client.max_requests:
            break
    if not result["cursor_pagination"]["passed"] and client.requests < client.max_requests:
        detail = {"phrase": "cargo pants", "since": "2025-11-15T00:00:00Z", "until": "2026-03-01T00:00:00Z"}
        params = {"q": '"cargo pants"', "sort": "latest", "since": detail["since"], "until": detail["until"], "limit": 5}
        try:
            first = client.get("app.bsky.feed.searchPosts", params)
            page = first.get("posts") or []
            detail["first_page_count"] = len(page)
            if first.get("cursor"):
                try:
                    second = client.get("app.bsky.feed.searchPosts", {**params, "cursor": first["cursor"]})
                    new = {p.get("uri") for p in second.get("posts") or []} - {p.get("uri") for p in page}
                    detail["new_on_second_page"] = len(new)
                    result["cursor_pagination"]["passed"] = bool(new)
                except ApiFailure as error:
                    detail["cursor_error"] = str(error)
            if page:
                oldest = min((p.get("record") or {}).get("createdAt", p.get("indexedAt", detail["until"])) for p in page)
                second = client.get("app.bsky.feed.searchPosts", {**params, "until": oldest})
                new = {p.get("uri") for p in second.get("posts") or []} - {p.get("uri") for p in page}
                result["keyset_pagination"] = {"passed": bool(new), "new_on_second_page": len(new), "status": "200"}
        except (ApiFailure, BudgetExceeded) as error:
            detail["error"] = str(error)
        result["cursor_pagination"].setdefault("attempts", []).append(detail)
    post = sample[0] if sample else {}
    uri, did = post.get("uri"), (post.get("author") or {}).get("did")
    endpoints = [
        ("app.bsky.feed.getPostThread", {"uri": uri, "depth": 2, "parentHeight": 1}, "thread"),
        ("app.bsky.feed.getRepostedBy", {"uri": uri, "limit": 5}, "repostedBy"),
        ("app.bsky.feed.getQuotes", {"uri": uri, "limit": 5}, "posts"),
        ("app.bsky.graph.getFollows", {"actor": did, "limit": 5}, "follows")]
    for endpoint, params, expected in endpoints:
        if result["endpoints"].get(endpoint, {}).get("passed"):
            continue
        if not uri or not did:
            result["endpoints"][endpoint] = {"passed": False, "status": "no_sample_post"}
            continue
        try:
            data = client.get(endpoint, params)
            result["endpoints"][endpoint] = {"passed": expected in data, "status": "200",
                                             "returned_count": len(data[expected]) if isinstance(data.get(expected), list) else None}
        except (ApiFailure, BudgetExceeded) as error:
            result["endpoints"][endpoint] = {"passed": False, "status": str(error)}
    result["requests"] = prior_requests + client.requests - start_requests
    client.max_requests = original_limit
    return result


def write_probe(result: dict, path: Path = ROOT / "docs/social_probe.md"):
    lines = ["# Bluesky public endpoint probe", "", f"Checked: {result['checked_at']}",
             "", "Host: api.bsky.app, unauthenticated. Every actual API request, including HTTP retries, counts toward the 20-request probe budget. Offline connection failures are reported separately.",
             f"API requests used: {result['requests']}. Offline connection failures: {result.get('offline_connection_failures', 0)}.", "", "| Endpoint | Passed | Status |", "| --- | --- | --- |"]
    for endpoint, value in result["endpoints"].items():
        lines.append(f"| {endpoint} | {value['passed']} | {value['status']} |")
    lines += ["", "Cursor pagination: " + json.dumps(result["cursor_pagination"], sort_keys=True),
              "", "Timestamp keyset pagination: " + json.dumps(result.get("keyset_pagination", {"passed": False}), sort_keys=True),
              "Four additional first-page tests for cargo pants, chinos, ballet flats, and jeans returned HTTP 200 with cursors; supplying their cursors returned HTTP 403. The historical-window test below reproduced the cursor failure. Offline sandbox failures never reached the API and are recorded separately.",
              "", "Only endpoints marked passed may be used. Search pages are followed until the cursor is absent, unchanged, or repeats. A short page alone does not establish exhaustion.",
              "Search results and hitsTotal are search index samples and may be capped. They are never platform totals.",
              "Replies identify parent authors in their post URIs. Repost counts are not repost-account interaction edges. No community field is supplied by Bluesky.",
              "", "The original seed file contains only a placeholder trend. The integration agent also inspected the live trends collection: all three entries were placeholders, not researched trends. The configured terms are a draft based on the requested fashion phrases; labels are evaluation annotations, never classifier inputs.",
              "", "References: [Bluesky HTTP API](https://endpoints.bsky.app/), [official search implementation](https://github.com/bluesky-social/atproto/blob/main/packages/bsky/src/api/app/bsky/feed/searchPosts.ts).",
              "Context7 was unavailable. The former docs.bsky.app API pages redirected to endpoints.bsky.app. Runtime HTTP probing establishes endpoint availability."]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def weekly_windows(end: datetime) -> list[tuple[str, str]]:
    start = end.replace(year=end.year - 1, day=min(end.day, calendar.monthrange(end.year - 1, end.month)[1]))
    result = []
    cursor = start
    while cursor < end:
        following = min(end, (cursor + timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0))
        # Align interior boundaries to UTC Monday; first and last are partial weeks.
        following = min(end, (cursor + timedelta(days=7 - cursor.weekday())).replace(hour=0, minute=0, second=0, microsecond=0))
        result.append((iso(cursor), iso(following)))
        cursor = following
    return result


def persist_post(collection, post: dict, cap: int) -> bool:
    """Return whether a new unique post was added; preserve matching terms on duplicates."""
    existing = collection.find_one({"uri": post["uri"]}, {"_id": 1, "source": 1})
    if existing:
        if post.get("source") == "thread":
            return False
        update = {"$addToSet": {"terms": post["term"]}}
        if existing.get("source") == "thread" and post.get("source") != "thread":
            update["$set"] = {"source": "search", "term": post["term"]}
        collection.update_one({"uri": post["uri"]}, update)
        return False
    if collection.count_documents({}) >= cap:
        raise BudgetExceeded("global post cap reached")
    collection.update_one({"uri": post["uri"]}, {"$setOnInsert": post}, upsert=True)
    return True


def persist_page(collection, posts: list[dict], cap: int):
    """Two reads plus one bulk write per page rather than per-post WAN calls."""
    if not hasattr(collection, "bulk_write"):
        for post in posts:
            persist_post(collection, post, cap)
        return
    from pymongo import UpdateOne
    existing = {doc["uri"]: doc for doc in collection.find(
        {"uri": {"$in": [post["uri"] for post in posts]}}, {"uri": 1, "source": 1})}
    room = max(0, cap - collection.count_documents({}))
    operations, skipped = [], False
    for post in posts:
        previous = existing.get(post["uri"])
        if previous is None:
            if not room:
                skipped = True
                continue
            room -= 1
            existing[post["uri"]] = post
            fields = {k: v for k, v in post.items() if k != "terms"}
            update = {"$setOnInsert": fields, "$addToSet": {"terms": post["term"]}}
        else:
            update = {"$addToSet": {"terms": post["term"]}}
            if previous.get("source") == "thread" and post.get("source") != "thread":
                update["$set"] = {"source": "search", "term": post["term"]}
        operations.append(UpdateOne({"uri": post["uri"]}, update, upsert=True))
    if operations:
        collection.bulk_write(operations, ordered=True)
    if skipped:
        raise BudgetExceeded("global post cap reached")


def initial_state(terms: list[str], end: datetime) -> dict:
    windows = weekly_windows(end)
    # First visit every term/week before deeper pages, reducing global-cap allocation bias.
    tasks = [{"term": term, "since": since, "until": until, "cursor": None, "done": False,
              "pages": 0, "seen_cursors": [], "returned": 0, "keyset_until": None}
             for since, until in reversed(windows) for term in terms]
    return {"version": 1, "window_end": iso(end), "window_start": windows[0][0], "tasks": tasks,
            "requests": 0, "thread": {"requests": 0, "fetched": [], "new_posts": 0, "errors": 0}}


def prepare_resume(state: dict) -> int:
    """Retry failed windows once on a new invocation, retaining paging progress."""
    retried = 0
    for task in state["tasks"]:
        if task.get("stop_reason") == "request_error":
            task["done"] = False
            task["partial"] = bool(task.get("equal_timestamp_boundary_risk"))
            task["previous_error"] = task.pop("last_error", None)
            task.pop("stop_reason", None)
            task["resume_retries"] = task.get("resume_retries", 0) + 1
            retried += 1
    state.pop("stop_reason", None)
    return retried


def extend_terms(state: dict, rows: list[dict]) -> int:
    """Extend the exact saved window without changing any existing cursor."""
    existing = {task["term"] for task in state["tasks"]}
    windows = list(dict.fromkeys((task["since"], task["until"]) for task in state["tasks"]))
    added = 0
    for row in rows:
        term = row["term"]
        if term not in existing:
            for since, until in windows:
                state["tasks"].append({"term": term, "since": since, "until": until, "cursor": None,
                    "done": False, "pages": 0, "seen_cursors": [], "returned": 0, "keyset_until": None})
                added += 1
            existing.add(term)
        state.setdefault("evaluation_terms", {})[term] = {
            key: value for key, value in row.items() if key in
            {"label", "press_status", "press_source", "press_sources", "press_date", "press_dates"}}
    return added


def known_uris(collection, posts: list[dict], term=None) -> set[str]:
    uris = {post["uri"] for post in posts}
    if hasattr(collection, "find"):
        existing = list(collection.find({"uri": {"$in": list(uris)}}, {"uri": 1, "term": 1, "terms": 1, "source": 1}))
    else:
        existing = [collection.find_one({"uri": uri}) for uri in uris]
    return {post["uri"] for post in existing if post and (term is None or
        (post.get("source") != "thread" and term in (post.get("terms") or [post.get("term")])))}


def audit_first_pages(client, state: dict, selected_terms=None, state_path=STATE_PATH):
    """Recover reliable first-page saturation without inserting or moving cursors."""
    before = client.requests
    try:
        for task in state["tasks"]:
            if selected_terms is not None and task["term"] not in selected_terms:
                continue
            if "first_page_count" in task:
                continue
            if task.get("pages") == 1 and 0 <= task.get("returned", -1) <= 20:
                task["first_page_count"] = task["returned"]
                task["first_page_count_source"] = "saved_single_page"
            else:
                try:
                    page = client.get("app.bsky.feed.searchPosts", {"q": '"' + task["term"] + '"',
                        "sort": "latest", "since": task["since"], "until": task["until"], "limit": 20})
                except ApiFailure as error:
                    task["audit_error"] = str(error)
                    continue
                task["first_page_count"] = len(page.get("posts") or [])
                task["first_page_count_source"] = "audit_request"
            task["saturated"] = task["first_page_count"] >= 20
            save_json(state_path, state)
    finally:
        state["audit_requests"] = state.get("audit_requests", 0) + client.requests - before
        state["requests"] += client.requests - before
        save_json(state_path, state)


def requeue_verification(state, selected_terms=None, deepen_saturated=False):
    count = 0
    for task in state["tasks"]:
        if selected_terms is not None and task["term"] not in selected_terms:
            continue
        if (task.get("equal_timestamp_boundary_risk") and not task.get("verification_restarted")) or (deepen_saturated and task.get("saturated")):
            task.update(done=False, partial=True, unresolved=False, keyset_until=task["until"],
                        verification_restarted=True)
            task.pop("stop_reason", None)
            count += 1
    return count


def insertion_budget(cap, current_count, reserved_press_posts, pending_threads=0, press_only=False):
    """Keep pending enrichment from consuming the capacity promised to press seeds."""
    press_reservation = 0 if press_only else reserved_press_posts
    thread_reservation = min(1000, max(0, pending_threads) * 5)
    return max(0, cap - current_count - press_reservation - thread_reservation)


def ingest(client, collection, state: dict, salt: str, store_text: bool, cap: int, state_path=STATE_PATH,
           *, confirm_only=False, max_new_posts=None, selected_terms=None, only_saturated=False,
           first_pages_only=False):
    request_start, consecutive_errors = client.requests, 0
    blocked = set()
    remaining_quota = 0 if confirm_only else max_new_posts
    window_order = {window: number for number, window in enumerate(dict.fromkeys(
        (task["since"], task["until"]) for task in state["tasks"]))}
    try:
        while True:
            remaining = [task for task in state["tasks"] if not task["done"] and id(task) not in blocked
                         and (selected_terms is None or task["term"] in selected_terms)
                         and (not only_saturated or task.get("saturated") is True)
                         and (not first_pages_only or task.get("first_page_insertion_pending") or
                              (task["pages"] == 0 and "first_page_count" not in task))]
            remaining.sort(key=lambda task: (window_order[(task["since"], task["until"])],
                                             task["term"].casefold(), task["term"]))
            if not remaining:
                break
            for task in remaining:
                pagination = state.get("pagination", "cursor")
                limit = 20 if first_pages_only else (100 if confirm_only or task["pages"] > 0 else 20)
                query_until = task.get("keyset_until") or task["until"]
                params = {"q": '"' + task["term"] + '"', "sort": "latest", "since": task["since"],
                          "until": query_until, "limit": limit}
                if task.get("cursor") and pagination == "cursor":
                    params["cursor"] = task["cursor"]
                try:
                    data = client.get("app.bsky.feed.searchPosts", params)
                except ApiFailure as error:
                    task.update(last_error=str(error), done=True, partial=True, unresolved=True, stop_reason="request_error")
                    state["failed_windows"] = state.get("failed_windows", 0) + 1
                    consecutive_errors += 1
                    save_json(state_path, state)
                    if consecutive_errors >= 20:
                        raise BudgetExceeded("twenty consecutive request failures") from None
                    continue
                consecutive_errors = 0
                posts = data.get("posts") or []
                if task["pages"] == 0 and limit == 20:
                    task.update(first_page_count=len(posts), saturated=len(posts) >= 20,
                                first_page_count_source="initial_ingest_request")
                normalized = []
                for raw in posts:
                    post = normalize_post(raw, task["term"], salt, store_text)
                    if post and parse_time(task["since"]) <= parse_time(post["created_at"]) < parse_time(task["until"]):
                        normalized.append(post)
                known = known_uris(collection, normalized)
                unseen = {post["uri"] for post in normalized if post["uri"] not in known}
                term_new = {post["uri"] for post in normalized} - known_uris(collection, normalized, task["term"])
                room = max(0, cap - collection.count_documents({}))
                if remaining_quota is not None:
                    room = min(room, remaining_quota)
                allowed = set(sorted(unseen)[:room])
                selected = [post for post in normalized if post["uri"] in known or post["uri"] in allowed]
                persist_page(collection, selected, cap)
                if remaining_quota is not None:
                    remaining_quota -= len(allowed)
                task["returned"] += len(posts)
                task["pages"] += 1
                task["last_page_count"] = len(posts)
                task["last_new_uri_count"] = len(term_new)
                task["last_new_global_uri_count"] = len(unseen)
                if posts and len(normalized) != len(posts):
                    task.update(done=False, partial=True, unresolved=True, stop_reason="invalid_or_out_of_window_posts")
                    blocked.add(id(task))
                elif unseen - allowed:
                    # Preserve this exact request position; unseen posts remain retrievable.
                    task.update(done=False, partial=True, unresolved=True,
                                stop_reason="new_posts_require_capacity", pending_unseen_count=len(unseen - allowed))
                    blocked.add(id(task))
                    if first_pages_only:
                        task["first_page_insertion_pending"] = True
                elif not posts:
                    task.update(done=True, partial=False, unresolved=False, equal_timestamp_boundary_risk=False,
                                stop_reason="confirmed_empty")
                elif not term_new and len(posts) < limit:
                    task.update(done=True, partial=False, unresolved=False, equal_timestamp_boundary_risk=False,
                                stop_reason="confirmed_no_new")
                elif not term_new and len(posts) >= limit:
                    task.update(done=False, partial=True, unresolved=True, stop_reason="unresolved_full_page")
                    blocked.add(id(task))
                elif pagination == "keyset":
                    times = [parse_time(post["created_at"]) for post in normalized]
                    next_until = min(parse_time(task["until"]), min(times) + timedelta(microseconds=1)) if times else parse_time(query_until)
                    if next_until >= parse_time(query_until) and not term_new:
                        task.update(done=False, partial=True, unresolved=True, stop_reason="unresolved_full_page")
                        blocked.add(id(task))
                    else:
                        task.update(keyset_until=iso(next_until), partial=True, unresolved=False, pagination_overlap=True)
                elif pagination == "first_page":
                    task.update(done=False, partial=True, unresolved=True, stop_reason="pagination_unavailable")
                    blocked.add(id(task))
                else:
                    cursor = data.get("cursor")
                    if not cursor or cursor == task.get("cursor") or cursor in task["seen_cursors"]:
                        task.update(done=False, partial=True, unresolved=True, stop_reason="cursor_confirmation_required")
                        blocked.add(id(task))
                    else:
                        task["seen_cursors"].append(cursor)
                        task["cursor"] = cursor
                state["updated_at"] = iso(utc_now())
                if first_pages_only:
                    if not (unseen - allowed):
                        task["first_page_insertion_pending"] = False
                        task["first_page_ingested"] = True
                    blocked.add(id(task))
                save_json(state_path, state)
                if (client.requests - request_start) % 20 == 0:
                    print(json.dumps({"phase": "confirmation" if confirm_only else "search",
                        "posts": collection.count_documents({}), "requests": client.requests - request_start,
                        "completed_windows": sum(t["done"] and not t.get("partial") for t in state["tasks"]),
                        "total_windows": len(state["tasks"])}), flush=True)
        state["stop_reason"] = "confirmation_finished" if confirm_only else "search_pass_finished"
    finally:
        state["requests"] += client.requests - request_start
        save_json(state_path, state)


def thread_posts(thread: dict):
    """Yield real visible posts, ignoring blocked/deleted thread placeholders."""
    if not isinstance(thread, dict):
        return
    if isinstance(thread.get("post"), dict):
        yield thread["post"]
    if thread.get("parent"):
        yield from thread_posts(thread["parent"])
    for child in thread.get("replies") or []:
        yield from thread_posts(child)


def enrich_threads(client, collection, state, probe_result, salt, store_text, cap, state_path=STATE_PATH):
    stats = state.setdefault("thread", {"requests": 0, "fetched": [], "new_posts": 0, "errors": 0})
    stats["per_thread_new_post_cap"] = 5
    if not probe_result.get("endpoints", {}).get("app.bsky.feed.getPostThread", {}).get("passed"):
        stats["skipped"] = "endpoint_probe_failed"
        return
    if "candidates" not in stats:
        stats["candidates"] = list(collection.find({"reply_count": {"$gt": 0}, "source": {"$ne": "thread"}},
                                                  {"_id": 0, "uri": 1, "term": 1}).sort([("reply_count", -1), ("uri", 1)]).limit(200))
        save_json(state_path, state)
    candidates = stats["candidates"]
    for candidate in candidates:
        if candidate["uri"] in stats["fetched"] or stats["requests"] >= 400:
            continue
        before = client.requests
        try:
            # Physical retries count toward the 400-request enrichment budget.
            old_limit = client.max_requests
            client.max_requests = min(old_limit, client.requests + 400 - stats["requests"]) if old_limit is not None else client.requests + 400 - stats["requests"]
            data = client.get("app.bsky.feed.getPostThread", {"uri": candidate["uri"], "depth": 6, "parentHeight": 2})
            normalized_by_uri = {}
            for raw in thread_posts(data.get("thread") or {}):
                normalized = normalize_post(raw, candidate["term"], salt, store_text)
                if normalized and parse_time(state["window_start"]) <= parse_time(normalized["created_at"]) < parse_time(state["window_end"]):
                    normalized["source"] = "thread"
                    normalized_by_uri[normalized["uri"]] = normalized
            known = {post["uri"] for post in collection.find(
                {"uri": {"$in": list(normalized_by_uri)}}, {"uri": 1})}
            room = min(5, max(0, cap - collection.count_documents({})))
            new_posts = [post for uri, post in normalized_by_uri.items() if uri not in known]
            selected = new_posts[:room]
            persist_page(collection, selected, cap)
            new_for_thread = len(selected)
            skipped_for_thread = len(new_posts) - len(selected)
            stats["new_posts"] += new_for_thread
            stats["duplicate_posts"] = stats.get("duplicate_posts", 0) + len(known)
            stats["skipped_new_posts"] = stats.get("skipped_new_posts", 0) + skipped_for_thread
            stats["truncated_threads"] = stats.get("truncated_threads", 0) + int(skipped_for_thread > 0)
            stats["fetched"].append(candidate["uri"])
            if len(stats["fetched"]) % 20 == 0:
                print(json.dumps({"phase": "threads", "fetched": len(stats["fetched"]),
                                  "new_posts": stats["new_posts"], "requests": stats["requests"] + client.requests - before}), flush=True)
        except ApiFailure:
            stats["errors"] += 1
        finally:
            stats["requests"] += client.requests - before
            client.max_requests = old_limit
            save_json(state_path, state)


def quality_report(collection, state: dict, probe_result: dict, path=ROOT / "docs/social_quality.md") -> dict:
    posts = list(collection.find({}, {"embedding": 0, "text": 0}))
    by_term = Counter()
    search_by_term, thread_by_term = Counter(), Counter()
    by_week = Counter()
    replies, authors, thread_posts_count = 0, set(), 0
    for post in posts:
        authors.add(post["author_hash"])
        replies += bool(post.get("reply_parent_uri"))
        thread_posts_count += post.get("source") == "thread"
        stamp = parse_time(post["created_at"])
        week = (stamp - timedelta(days=stamp.weekday())).date().isoformat()
        for term in post.get("terms") or [post["term"]]:
            by_term[term] += 1
            if post.get("source") == "thread":
                thread_by_term[term] += 1
            else:
                search_by_term[term] += 1
                by_week[(term, week)] += 1
    terms = sorted({t["term"] for t in state["tasks"]})
    weeks = sorted({(parse_time(t["since"]) - timedelta(days=parse_time(t["since"]).weekday())).date().isoformat() for t in state["tasks"]})
    gaps = {term: [week for week in weeks if not by_week[(term, week)]] for term in terms}
    incomplete = {term: [{"since": t["since"], "until": t["until"], "pages": t["pages"]}
                         for t in state["tasks"] if t["term"] == term and (not t["done"] or t.get("partial") or t.get("equal_timestamp_boundary_risk"))] for term in terms}
    stats = {"posts": len(posts), "search_posts": len(posts) - thread_posts_count, "thread_posts": thread_posts_count,
             "authors": len(authors), "reply_share": replies / len(posts) if posts else 0,
             "posts_per_term": {term: by_term[term] for term in terms}, "coverage_gaps": gaps,
             "search_posts_per_term": {term: search_by_term[term] for term in terms},
             "thread_posts_per_term": {term: thread_by_term[term] for term in terms},
             "completed_windows": sum(t["done"] and not t.get("partial") and not t.get("equal_timestamp_boundary_risk") for t in state["tasks"]), "total_windows": len(state["tasks"]),
             "search_requests": state["requests"], "probe_requests": probe_result.get("requests", 0),
             "thread_results": {k: (len(v) if k in {"fetched", "candidates"} else v) for k, v in state["thread"].items()},
             "stop_reason": state.get("stop_reason"), "graph_gate": "pass" if len(posts) >= 3000 else "await_user_reply",
             "incomplete_windows": incomplete}
    stats["pagination"] = state.get("pagination", "cursor")
    stats["equal_timestamp_boundary_risk_windows"] = sum(bool(t.get("equal_timestamp_boundary_risk")) for t in state["tasks"])
    stats["completed_windows_per_term"] = {term: sum(t["term"] == term and t["done"] and not t.get("partial") and not t.get("unresolved") and not t.get("equal_timestamp_boundary_risk") for t in state["tasks"]) for term in terms}
    stats["saturated_windows_per_term"] = {term: sum(t["term"] == term and t.get("saturated") is True for t in state["tasks"]) for term in terms}
    stats["saturation_unknown_windows"] = sum("first_page_count" not in t for t in state["tasks"])
    stats["saturated_windows"] = sum(t.get("saturated") is True for t in state["tasks"])
    stats["first_page_zero_windows"] = sum(t.get("first_page_count") == 0 for t in state["tasks"])
    stats["first_page_short_nonempty_windows"] = sum(0 < t.get("first_page_count", -1) < 20 for t in state["tasks"])
    stats["first_page_metadata_sources"] = dict(Counter(t.get("first_page_count_source", "unknown") for t in state["tasks"]))
    stats["original_first_page_counts_unrecoverable_from_saved_state"] = stats["first_page_metadata_sources"].get("audit_request", 0)
    stats["global_cap"] = MAX_POSTS
    stats["remaining_capacity"] = max(0, MAX_POSTS - len(posts))
    stats["reserved_press_capacity"] = min(state.get("reserved_press_posts", 10000), stats["remaining_capacity"])
    stats["nonpress_available_capacity"] = max(0, stats["remaining_capacity"] - stats["reserved_press_capacity"])
    stats["press_seed_terms"] = sorted(term for term, metadata in state.get("evaluation_terms", {}).items()
                                      if metadata.get("press_status") is not None)
    press_note = ("Ten exact user-provided press_status seed rows are pending receipt."
                  if not stats["press_seed_terms"] else
                  f"{len(stats['press_seed_terms'])} exact press_status seed rows are configured as evaluation metadata.")
    lines = ["# Social ingestion data quality", "", "Generated: " + iso(utc_now()), "",
             "Counts are sampled search index results, not Bluesky platform totals. The 12-month window and global 35000-post cap limit coverage.",
             "", "```json", json.dumps({k: v for k, v in stats.items() if k not in {"posts_per_term", "search_posts_per_term", "thread_posts_per_term", "coverage_gaps", "incomplete_windows"}}, indent=2), "```", "",
             "| Term | Total posts | Search matches | Thread additions | Empty search weeks | Completed windows | Saturated windows |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    lines += [f"| {term} | {by_term[term]} | {search_by_term[term]} | {thread_by_term[term]} | {len(gaps[term])} | {stats['completed_windows_per_term'][term]} | {stats['saturated_windows_per_term'][term]} |" for term in terms]
    lines += ["", "## Per-week coverage gaps", "", "Only search matches determine coverage gaps. Inherited thread terms never fill a search gap. Empty weeks can reflect no indexed phrase matches, not necessarily no platform activity. Unvisited/incomplete windows must not be interpreted as zero activity."]
    lines += ["", "## First-page audit provenance", "",
              f"{stats['first_page_metadata_sources'].get('saved_single_page', 0)} first-page counts were recovered from saved single-page counters. "
              f"{stats['original_first_page_counts_unrecoverable_from_saved_state']} required fresh audit requests because the original first-page counts could not be recovered reliably. "
              "Fresh-audit saturation describes the response at audit time. It does not reconstruct missing original per-window flags or assign an external aggregate baseline to individual windows."]
    lines += ["", "```json", json.dumps(gaps, indent=2), "```", "", "## Incomplete or unvisited windows", "",
              "These windows have not been exhausted. A zero here is unknown coverage, not an observed zero.",
              "", "```json", json.dumps(incomplete, indent=2), "```", "", "## Limits", "",
              "Quoted phrase search misses wording variants. Cherry red, sambas and other broad phrases may include non-fashion posts. Sample balance depends on index availability. Labels are a draft and not verified ground truth.",
              "Thread enrichment adds real reply edges but can oversample active threads. A stable top-200 candidate list is fetched with at most five new posts retained per thread, preserving room under the global cap. Skipped posts and truncated threads are counted. Posts marked source=thread should be excluded from search trend counts. Posts matched by multiple terms remain deduplicated by URI with every matched term in terms.",
              "Author identity metadata uses only hashes; original post text can contain public mentions. Runtime snippets must redact handles. STORE_TEXT defaults true; the wipe script can blank stored text. Hashes are pseudonyms, not a guarantee of anonymity.",
              "", "## Contract deviations", "", "Additive source=thread identifies enrichment posts so aggregate metrics can avoid sampling bias. Terms were drafted from the requested list because the seed file contains only a placeholder and the integration agent found only three placeholders in the live trends collection.",
              "Cursor page requests returned HTTP 403 in the probe. Timestamp keyset fallback now uses until=oldest createdAt plus one microsecond and URI overlap deduplication. Legacy exclusive-boundary windows require actual re-verification; saturation alone never proves exhaustion. Empty responses or zero new term/window URIs on a non-full confirmation page establish sampled completion. Full repeated pages remain unresolved. Unseen posts with no insertion quota preserve their continuation position. First pages sample at most 20 posts per term/week; saturated windows are lower bounds, not platform totals.",
              "The latest global cap is 35000 posts. Non-press collection reserves capacity for press_status seeds. " + press_note + " No press seed rows have been invented."]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    save_json(ROOT / ".social_runs/quality.json", stats)
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-only", action="store_true")
    parser.add_argument("--max-minutes", type=float, default=90)
    parser.add_argument("--max-posts", type=int, default=MAX_POSTS)
    parser.add_argument("--skip-threads", action="store_true")
    parser.add_argument("--terms-file", type=Path, default=ROOT / "config/social_terms.json")
    parser.add_argument("--press-only", action="store_true")
    parser.add_argument("--term", action="append", help="Restrict processing to this canonical term; repeatable")
    parser.add_argument("--confirm-only", action="store_true", help="Permit confirmation requests with zero new post quota")
    parser.add_argument("--audit-first-pages", action="store_true", help="Recover missing first-page saturation metadata without insertion")
    parser.add_argument("--deepen-saturated", action="store_true")
    parser.add_argument("--first-pages-only", action="store_true", help="Collect at most one initial 20-result page per uncaptured selected window; never automatically deepen")
    parser.add_argument("--new-post-budget", "--max-new-posts", dest="new_post_budget", type=int)
    parser.add_argument("--reserve-posts", type=int, default=10000, help="Capacity reserved for pending press seeds during non-press collection")
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--threads-only", action="store_true", help="Skip search and enrich the stable top 200 posts, at most five new posts per thread")
    parser.add_argument("--normalize-times", action="store_true", help="Canonicalize stored timestamps to fixed UTC microseconds, max 35000 posts")
    args = parser.parse_args()
    if not 0 < args.max_minutes <= 90 or not 0 < args.max_posts <= MAX_POSTS:
        parser.error("max-minutes must be in (0,90] and max-posts in (0,35000]")
    if args.new_post_budget is not None and args.new_post_budget < 0:
        parser.error("new-post-budget must be nonnegative")
    if args.reserve_posts < 0:
        parser.error("reserve-posts must be nonnegative")
    if args.first_pages_only and args.deepen_saturated:
        parser.error("--first-pages-only cannot be combined with --deepen-saturated")
    config = load_json(args.terms_file, {})
    rows = config if isinstance(config, list) else config.get("terms", [])
    if not rows or not all(isinstance(row, dict) and isinstance(row.get("term"), str) and row["term"].strip() for row in rows):
        parser.error("terms-file requires rows with a nonempty term")
    selected_terms = set(args.term) if args.term else None
    if args.press_only:
        press_terms = {row["term"] for row in rows if row.get("press_status") is not None}
        if not press_terms:
            parser.error("No press_status seed rows configured. Supply the exact user-provided rows with --terms-file.")
        selected_terms = press_terms if selected_terms is None else selected_terms & press_terms
    if args.normalize_times:
        from backend.db import db
        print(json.dumps(normalize_times(db["social_posts"])), flush=True)
        return 0
    started = time.monotonic()
    salt = get_salt()
    store_text = os.getenv("STORE_TEXT", "true").lower() not in {"0", "false", "no"}
    client = BlueskyClient(deadline=started + args.max_minutes * 60)
    probe_result = load_json(PROBE_PATH, {})
    if args.probe_only or not probe_result:
        probe_result = probe(client, probe_result or None)
        save_json(PROBE_PATH, probe_result)
        write_probe(probe_result)
        print(json.dumps(probe_result), flush=True)
    if args.probe_only:
        return 0
    if not probe_result.get("endpoints", {}).get("app.bsky.feed.searchPosts", {}).get("passed"):
        print("Search probe failed. No ingestion performed.", flush=True)
        return 2
    if not probe_result.get("cursor_pagination", {}).get("passed"):
        print("Cursor pagination failed. Using verified first pages and timestamp keyset fallback when available.", flush=True)
    from backend.db import db
    collection = db["social_posts"]
    collection.create_index("uri", unique=True)
    collection.create_index([("term", 1), ("created_at", 1)])
    terms = [row["term"] for row in rows]
    state = load_json(STATE_PATH, None) or initial_state(terms, utc_now())
    extend_terms(state, rows)
    if not args.press_only:
        state["reserved_press_posts"] = args.reserve_posts
    state["pagination"] = ("cursor" if probe_result.get("cursor_pagination", {}).get("passed") else
                           "keyset" if probe_result.get("keyset_pagination", {}).get("passed") else "first_page")
    if args.threads_only and args.skip_threads:
        parser.error("--threads-only cannot be combined with --skip-threads")
    if not args.report_only:
        if args.threads_only and not state.get("stop_reason"):
            state["stop_reason"] = "search_handoff_to_thread_enrichment"
        if not args.threads_only:
            prepare_resume(state)
            if args.audit_first_pages:
                try:
                    audit_first_pages(client, state, selected_terms)
                except BudgetExceeded as error:
                    state["stop_reason"] = "first_page_audit_budget_reached"
                    print("Audit stop: " + str(error), flush=True)
                    stats = quality_report(collection, state, probe_result)
                    print(json.dumps({k: v for k, v in stats.items() if k not in {"coverage_gaps", "incomplete_windows"}}), flush=True)
                    return 0
            if args.confirm_only or args.deepen_saturated:
                requeue_verification(state, selected_terms, args.deepen_saturated)
            if args.deepen_saturated:
                saturated_terms = {t["term"] for t in state["tasks"] if t.get("saturated")}
                selected_terms = saturated_terms if selected_terms is None else selected_terms & saturated_terms
        try:
            # Reserve up to ten minutes for real-thread enrichment when possible.
            reserve_thread_time = 0 if args.skip_threads or args.confirm_only or args.press_only or args.first_pages_only else min(600, args.max_minutes * 60 * 0.15)
            search_deadline = client.deadline - reserve_thread_time
            client.deadline = search_deadline
            search_cap = args.max_posts
            will_enrich = not (args.skip_threads or args.confirm_only or args.press_only or args.audit_first_pages or args.first_pages_only)
            thread_stats = state.get("thread", {})
            pending_threads = max(0, len(thread_stats.get("candidates", range(200))) - len(thread_stats.get("fetched", [])))
            quota = insertion_budget(search_cap, collection.count_documents({}), args.reserve_posts,
                                     pending_threads if will_enrich else 0, args.press_only)
            if args.new_post_budget is not None:
                quota = min(quota, args.new_post_budget)
            if args.confirm_only:
                quota = 0
            state["search_cap"] = search_cap
            if not args.threads_only:
                ingest(client, collection, state, salt, store_text, search_cap, confirm_only=args.confirm_only,
                       max_new_posts=quota, selected_terms=selected_terms, only_saturated=args.deepen_saturated,
                       first_pages_only=args.first_pages_only)
        except BudgetExceeded as error:
            state.setdefault("stop_reason", str(error))
            print("Search stop: " + str(error), flush=True)
        finally:
            client.deadline = started + args.max_minutes * 60
        if not args.confirm_only and not args.press_only and not args.audit_first_pages and not args.first_pages_only and not args.skip_threads and (args.threads_only or collection.count_documents({}) < args.max_posts - args.reserve_posts):
            try:
                enrich_threads(client, collection, state, probe_result, salt, store_text,
                               max(0, args.max_posts - args.reserve_posts))
            except BudgetExceeded as error:
                print("Thread stop: " + str(error), flush=True)
        save_json(STATE_PATH, state)
    stats = quality_report(collection, state, probe_result)
    print(json.dumps({k: v for k, v in stats.items() if k not in {"coverage_gaps", "incomplete_windows"}}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        # Exception messages may include connection strings or returned text.
        print("Ingestion failed: " + type(error).__name__ + ". Inspect configuration without printing secrets.", file=sys.stderr)
        raise SystemExit(1)
