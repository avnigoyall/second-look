"""Resumable Gemini embeddings and Atlas index for the sampled Bluesky corpus.

Run with --dry-run to inspect volume without making model calls. Each post is a
separate Gemini input. --limit is a per-run budget, not an ingest cap.
"""
import argparse
import json
import math
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from threading import Event

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MODEL = "gemini-embedding-2"
DIMENSIONS = 768
INDEX_NAME = "social_posts_vector"
INDEX_DEFINITION = {"fields": [
    {"type": "vector", "path": "embedding", "numDimensions": DIMENSIONS, "similarity": "cosine"},
    {"type": "filter", "path": "term"},
    {"type": "filter", "path": "created_at"},
]}
PENDING = {"text": {"$type": "string", "$ne": ""}, "$or": [
    {"embedding_model": {"$ne": MODEL}}, {"embedding": {"$exists": False}},
    {"embedding": None},
]}


def ensure_vector_index(collection, *, wait_seconds=300, sleep=time.sleep):
    """Create or update only our index, and wait for its new definition."""
    from pymongo.operations import SearchIndexModel
    existing = next(iter(collection.list_search_indexes(INDEX_NAME)), None)
    if existing is None:
        collection.create_search_index(SearchIndexModel(
            definition=INDEX_DEFINITION, name=INDEX_NAME, type="vectorSearch"))
    elif existing.get("latestDefinition") != INDEX_DEFINITION:
        collection.update_search_index(INDEX_NAME, INDEX_DEFINITION)
    started = time.monotonic()
    while True:
        state = next(iter(collection.list_search_indexes(INDEX_NAME)), {})
        if state.get("status") == "FAILED":
            raise RuntimeError("social_posts_vector index build failed")
        if (state.get("queryable") and state.get("status") == "READY"
                and state.get("latestDefinition") == INDEX_DEFINITION):
            return True
        if time.monotonic() - started >= wait_seconds:
            return False
        sleep(min(5, max(0.1, wait_seconds)))


def valid_embedding(vector):
    return (len(vector) == DIMENSIONS and all(math.isfinite(v) for v in vector)
            and sum(v * v for v in vector) > 0)


def provider_status(error):
    """Find a numeric HTTP code through sanitized wrapper exception chains."""
    seen, pending = set(), [error]
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        for value in (getattr(current, "status_code", None), getattr(current, "code", None),
                      getattr(getattr(current, "response", None), "status_code", None)):
            try:
                code = int(value)
            except (TypeError, ValueError):
                continue
            if 100 <= code <= 599:
                return code
        pending.extend(value for value in (current.__cause__, current.__context__) if value is not None)
    return None


_REASON_CATEGORIES = {
    "API_KEY_INVALID": "credential", "API_KEY_EXPIRED": "credential",
    "API_KEY_HTTP_REFERRER_BLOCKED": "credential", "API_KEY_IP_ADDRESS_BLOCKED": "credential",
    "CREDENTIALS_MISSING": "credential", "ACCESS_TOKEN_EXPIRED": "credential",
    "API_KEY_SERVICE_BLOCKED": "permission", "ACCESS_TOKEN_SCOPE_INSUFFICIENT": "permission",
    "SERVICE_DISABLED": "permission", "FREE_TIER_VERIFICATION_REQUIRED": "permission",
    "BILLING_DISABLED": "billing", "BILLING_NOT_ACTIVE": "billing",
    "QUOTA_EXCEEDED": "quota", "RATE_LIMIT_EXCEEDED": "quota",
    "CONTENT_BLOCKED": "content", "SAFETY": "content",
}


def provider_diagnostics(error):
    """Read structured reasons only; never expose provider messages or metadata."""
    seen, pending = set(), [error]
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        details = [getattr(current, "details", None)]
        while details:
            value = details.pop()
            if isinstance(value, dict):
                reason = value.get("reason")
                if isinstance(reason, str) and reason in _REASON_CATEGORIES:
                    return {"category": _REASON_CATEGORIES[reason], "reason": reason}
                details.extend(child for child in value.values() if isinstance(child, (dict, list)))
            elif isinstance(value, list):
                details.extend(child for child in value if isinstance(child, (dict, list)))
        pending.extend(value for value in (current.__cause__, current.__context__) if value is not None)
    code = provider_status(error)
    return {"category": {401: "credential", 402: "billing", 403: "permission", 429: "quota"}.get(code, "unknown")}


class EmbeddingJobStopped(RuntimeError):
    """Safe failure metadata and already-persisted progress, without provider text."""
    def __init__(self, report, failure):
        super().__init__("embedding job stopped after a nonretryable provider response")
        self.report = dict(report)
        self.status_code = failure["code"]
        self.provider_error_type = failure["error_type"]
        self.provider_diagnostics = failure["diagnostics"]


def safe_error_report(exc):
    error = {"status": "failed", "error_type": type(exc).__name__}
    code = provider_status(exc)
    if code is not None:
        error["code"] = code
    if isinstance(exc, EmbeddingJobStopped):
        error.update(progress=exc.report, provider_error_type=exc.provider_error_type,
                     diagnostics=exc.provider_diagnostics)
    else:
        error["diagnostics"] = provider_diagnostics(exc)
    return error


def embed_posts(collection, embed_one, *, limit=1000, batch_size=32, workers=4,
                attempts=4, sleep=time.sleep, transient_403_retries=0):
    """Persist each success immediately; failures remain eligible on the next run.

    Do not send a list of posts as Gemini contents: that produces one merged
    multimodal vector with this model rather than one vector per post.
    """
    if limit < 0 or batch_size < 1 or workers < 1 or attempts < 1 or transient_403_retries < 0:
        raise ValueError("invalid embedding budget")
    report = {"attempted": 0, "embedded": 0, "failed": 0, "model_calls": 0,
              "model": MODEL, "dimensions": DIMENSIONS}
    stop = Event()

    def one(post):
        calls, retries_403, transient_failures = 0, 0, 0
        while True:
            if stop.is_set():
                return post, None, None, calls
            try:
                calls += 1
                vector = list(embed_one(post["text"]))
                if not valid_embedding(vector):
                    raise ValueError("invalid vector")
                norm = math.sqrt(sum(v * v for v in vector))
                return post, [v / norm for v in vector], None, calls
            except Exception as exc:
                code = provider_status(exc)
                failure = {"code": code, "error_type": type(exc).__name__, "diagnostics": provider_diagnostics(exc),
                           "fatal": code is not None and 400 <= code < 500 and code not in (408, 409, 429)}
                # Explicit opt-in only: 403 normally indicates a permanent client
                # error. This budget is for a measured intermittent provider issue.
                if code == 403 and retries_403 < transient_403_retries:
                    retries_403 += 1
                    sleep(2 ** retries_403)
                    continue
                if failure["fatal"]:
                    stop.set()
                    return post, None, failure, calls
                transient_failures += 1
                if transient_failures >= attempts:
                    return post, None, failure, calls
                sleep(2 ** transient_failures)

    cursor = collection.find(PENDING, {"uri": 1, "text": 1}).sort("uri", 1).limit(limit)
    batch = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        def process(rows):
            iterator, pending, fatal = iter(rows), set(), None

            def schedule():
                while len(pending) < workers and not stop.is_set():
                    post = next(iterator, None)
                    if post is None:
                        break
                    pending.add(pool.submit(one, post))

            schedule()
            while pending:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    post, vector, failure, calls = future.result()
                    report["model_calls"] += calls
                    if calls:
                        report["attempted"] += 1
                    if failure and failure["fatal"]:
                        fatal = failure
                    if vector is None:
                        report["failed"] += bool(calls)
                        continue
                    collection.update_one({"uri": post["uri"]}, {"$set": {
                        "embedding": vector, "embedding_model": MODEL,
                        "embedded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    }})
                    report["embedded"] += 1
                schedule()
            if fatal:
                raise EmbeddingJobStopped(report, fatal)
        # Mongo limit(0) means unlimited, so handle a zero request budget here.
        if limit:
            for post in cursor:
                batch.append(post)
                if len(batch) >= batch_size:
                    process(batch)
                    batch = []
            if batch:
                process(batch)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--create-index", action="store_true")
    parser.add_argument("--index-only", action="store_true")
    parser.add_argument("--transient-403-retries", type=int, default=0,
                        help="Explicit opt-in for measured intermittent 403s; retries per post, default 0")
    args = parser.parse_args()
    if args.limit < 0 or args.workers < 1 or args.batch_size < 1 or args.transient_403_retries < 0:
        parser.error("limit must be nonnegative, workers and batch-size positive")
    from backend.db import db
    posts = db["social_posts"]
    report = {"pending": posts.count_documents(PENDING), "limit": args.limit}
    report.update(planned_posts=min(report["pending"], args.limit),
                  model=MODEL, dimensions=DIMENSIONS,
                  transient_403_retries=args.transient_403_retries,
                  maximum_model_calls=min(report["pending"], args.limit) * (4 + args.transient_403_retries))
    if args.dry_run:
        print(json.dumps(report))
        return
    if not args.index_only:
        from backend.services.embeddings import embed_text
        report.update(embed_posts(posts, embed_text, limit=args.limit,
                                  workers=args.workers, batch_size=args.batch_size,
                                  transient_403_retries=args.transient_403_retries))
    if args.create_index or args.index_only:
        report["index_ready"] = ensure_vector_index(posts)
    print(json.dumps(report))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Provider exceptions can contain request text or credentials.
        print(json.dumps(safe_error_report(exc)), file=sys.stderr)
        sys.exit(1)
