"""Resumable Social Radar embeddings and Atlas vector index for the sampled Bluesky corpus.

SOCIAL_EMBED_PROVIDER (vultr default, or gemini) selects model, dimensions, vector field
and index through backend.services.social_embeddings. Vultr requests carry a list of
posts (--request-size) and run --workers requests in parallel. Run with --dry-run to
inspect volume without model calls. --limit is a per-run budget, not an ingest cap.
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

from backend.services import social_embeddings as provider  # noqa: E402

SPEC = provider.spec()
MODEL, DIMENSIONS, FIELD, INDEX_NAME = SPEC.model, SPEC.dims, SPEC.field, SPEC.index


def index_definition(spec=SPEC):
    return {"fields": [
        {"type": "vector", "path": spec.field, "numDimensions": spec.dims, "similarity": "cosine"},
        {"type": "filter", "path": "term"},
        {"type": "filter", "path": "created_at"},
    ]}


INDEX_DEFINITION = index_definition()
PENDING = provider.pending_filter(SPEC)


def ensure_vector_index(collection, *, wait_seconds=300, sleep=time.sleep, spec=SPEC):
    """Create or update only this provider's index, and wait for its new definition."""
    from pymongo.operations import SearchIndexModel
    INDEX_NAME, INDEX_DEFINITION = spec.index, index_definition(spec)
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
            raise RuntimeError(f"{INDEX_NAME} index build failed")
        if (state.get("queryable") and state.get("status") == "READY"
                and state.get("latestDefinition") == INDEX_DEFINITION):
            return True
        if time.monotonic() - started >= wait_seconds:
            return False
        sleep(min(5, max(0.1, wait_seconds)))


def valid_embedding(vector, dims=DIMENSIONS):
    return (len(vector) == dims and all(math.isfinite(v) for v in vector)
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


_SPLIT_CODES = {400, 413, 422}


def embed_posts(collection, embed_batch, *, limit=1000, batch_size=256, request_size=32, workers=4,
                attempts=4, sleep=time.sleep, transient_403_retries=0, on_progress=None, spec=SPEC,
                max_backoff=30.0):
    """Embed pending posts and flush successful vectors in bounded bulk writes.

    embed_batch(texts) returns one vector per text, in order. Each call is one provider
    request of at most request_size posts; workers requests run in parallel. Transient
    failures back off exponentially (honoring a provider retry_after hint). 401/402/403
    stop the job promptly; a 400/413/422 on a multi-post request is split into single
    posts so one rejected text cannot stop the corpus. Failures remain resumable.
    """
    if limit < 0 or batch_size < 1 or request_size < 1 or workers < 1 or attempts < 1 or transient_403_retries < 0:
        raise ValueError("invalid embedding budget")
    report = {"attempted": 0, "embedded": 0, "failed": 0, "model_calls": 0,
              "provider": spec.provider, "model": spec.model, "dimensions": spec.dims, "field": spec.field}
    stop = Event()

    def failure_for(exc):
        code = provider_status(exc)
        category = getattr(exc, "category", None)
        # The spending cap is final: no retry, no split, stop the whole job.
        budget_stop = category in ("budget", "budget_ledger_unreadable")
        return {"code": code, "error_type": type(exc).__name__,
                "diagnostics": {"category": category} if budget_stop else provider_diagnostics(exc),
                "fatal": budget_stop or (code is not None and 400 <= code < 500
                                         and code not in {408, 409, 425, 429} | _SPLIT_CODES)}

    def request(posts):
        """Returns (results, calls) where results pairs each post with (vector or None, failure or None)."""
        calls, retries_403, transient_failures = 0, 0, 0
        while True:
            if stop.is_set():
                return [(post, None, None) for post in posts], calls
            try:
                calls += 1
                vectors = [list(v) for v in embed_batch([post["text"] for post in posts])]
                if len(vectors) != len(posts) or not all(valid_embedding(v, spec.dims) for v in vectors):
                    raise ValueError("invalid vector")
                return [(post, [x / math.sqrt(sum(y * y for y in v)) for x in v], None)
                        for post, v in zip(posts, vectors)], calls
            except Exception as exc:
                failure = failure_for(exc)
                code = failure["code"]
                if code in _SPLIT_CODES:
                    if len(posts) == 1:
                        return [(posts[0], None, failure)], calls
                    results = []
                    for post in posts:
                        part, used = request([post])
                        results.extend(part)
                        calls += used
                    return results, calls
                # Explicit opt-in only: 403 normally indicates a permanent client error.
                if code == 403 and retries_403 < transient_403_retries:
                    retries_403 += 1
                    sleep(2 ** retries_403)
                    continue
                if failure["fatal"]:
                    stop.set()
                    return [(post, None, failure) for post in posts], calls
                transient_failures += 1
                if transient_failures >= attempts:
                    return [(post, None, failure) for post in posts], calls
                hint = getattr(exc, "retry_after", None)
                sleep(min(max_backoff, hint if isinstance(hint, (int, float)) and hint >= 0
                          else 2 ** transient_failures))

    cursor = collection.find(PENDING if spec is SPEC else provider.pending_filter(spec),
                             {"uri": 1, "text": 1}).sort("uri", 1).limit(limit)
    batch = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        def process(rows):
            chunks = iter([rows[i:i + request_size] for i in range(0, len(rows), request_size)])
            pending, fatal, writes = set(), None, []

            def schedule():
                while len(pending) < workers and not stop.is_set():
                    chunk = next(chunks, None)
                    if chunk is None:
                        break
                    pending.add(pool.submit(request, chunk))

            schedule()
            while pending:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    results, calls = future.result()
                    report["model_calls"] += calls
                    for post, vector, failure in results:
                        if calls:
                            report["attempted"] += 1
                        if failure and failure["fatal"]:
                            fatal = failure
                        if vector is None:
                            report["failed"] += bool(calls)
                            continue
                        fields = {spec.field: vector, "embedding_model": spec.model, "dims": spec.dims,
                                  "embedded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
                        if spec.provider == "gemini":
                            # embedding_model and dims describe embedding_vultr; Gemini provenance is its field.
                            fields = {spec.field: vector, "embedded_at": fields["embedded_at"]}
                        writes.append(({"uri": post["uri"]}, {"$set": fields}))
                schedule()
            if writes:
                bulk_write = getattr(collection, "bulk_write", None)
                if callable(bulk_write):
                    from pymongo import UpdateOne
                    bulk_write([UpdateOne(query, update) for query, update in writes], ordered=False)
                else:
                    # Small offline fakes can preserve the original minimal API.
                    for query, update in writes:
                        collection.update_one(query, update)
                report["embedded"] += len(writes)
            if on_progress is not None:
                on_progress(dict(report))
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
    parser.add_argument("--workers", type=int, default=4, help="parallel provider requests")
    parser.add_argument("--request-size", type=int, default=None,
                        help="posts per provider request (Vultr default 32; Gemini is always 1)")
    parser.add_argument("--batch-size", type=int, default=256, help="posts per bulk database write")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--create-index", action="store_true")
    parser.add_argument("--index-only", action="store_true")
    parser.add_argument("--transient-403-retries", type=int, default=0,
                        help="Explicit opt-in for measured intermittent 403s; retries per request, default 0")
    args = parser.parse_args()
    request_size = 1 if SPEC.provider == "gemini" else (args.request_size or 32)
    if args.limit < 0 or args.workers < 1 or args.batch_size < 1 or request_size < 1 or args.transient_403_retries < 0:
        parser.error("limit must be nonnegative; workers, batch-size and request-size positive")
    from backend.db import db
    posts = db["social_posts"]
    report = {"pending": posts.count_documents(PENDING), "limit": args.limit}
    planned = min(report["pending"], args.limit)
    report.update(planned_posts=planned, provider=SPEC.provider, model=MODEL, dimensions=DIMENSIONS,
                  field=FIELD, index=INDEX_NAME, request_size=request_size, workers=args.workers,
                  transient_403_retries=args.transient_403_retries,
                  maximum_model_calls=math.ceil(planned / request_size) * (4 + args.transient_403_retries)
                  + (planned if request_size > 1 else 0))
    report["vultr_budget"] = provider.usage_report() if SPEC.provider == "vultr" else None
    if args.dry_run:
        print(json.dumps(report))
        return
    if not args.index_only:
        started = time.monotonic()
        report.update(embed_posts(
            posts, lambda texts: provider.embed_texts(texts, attempts=1), limit=args.limit,
            workers=args.workers, batch_size=args.batch_size, request_size=request_size,
            transient_403_retries=args.transient_403_retries,
            on_progress=lambda progress: print(json.dumps({
                "status": "progress", "elapsed_s": round(time.monotonic() - started, 1), **progress}), flush=True)))
        elapsed = time.monotonic() - started
        report.update(elapsed_s=round(elapsed, 2),
                      embeddings_per_second=round(report["embedded"] / elapsed, 2) if elapsed else None)
    if SPEC.provider == "vultr":
        report["vultr_budget"] = provider.usage_report()
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
