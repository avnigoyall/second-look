"""Social Radar embedding provider. The closet embedder in embeddings.py is separate and unchanged.

SOCIAL_EMBED_PROVIDER selects one provider for ingest-time vectors, query vectors, retrieval,
graph neighbors and evaluation. Vectors from different providers are never compared.

- vultr (default): OpenAI-compatible POST {VULTR_BASE_URL}/embeddings, model VULTR_EMBED_MODEL,
  dimensions=1024 (verified live; native output is 2560). A list input returns one unit-length
  vector per input in order (verified live against single calls). Stored in embedding_vultr
  with embedding_model and dims, searched through social_posts_vultr_vector.
- gemini: gemini-embedding-2, 768 dimensions, one call per text, stored in embedding,
  searched through social_posts_vector.

Credentials come from backend/.env. Errors carry only a status code and category, never
provider response text, request text or keys.
"""
from __future__ import annotations

import fcntl
import json
import math
import os
import random
import threading
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VULTR_DEFAULT_BASE = "https://api.vultrinference.com/v1"
VULTR_DEFAULT_MODEL = "qwen3-embedding-4b"
VULTR_DIMENSIONS = 1024
GEMINI_MODEL = "gemini-embedding-2"
GEMINI_DIMENSIONS = 768
_RETRYABLE = {408, 409, 425, 429, 500, 502, 503, 504}
# Spending guard. Vultr publishes $0.55 per million input tokens for text models; no separate
# embedding price was found, so the default assumes the highest listed rate ($2.75). The ledger is
# shared by every process (backend, scripts, eval) through a file lock and blocks calls at the cap.
LEDGER_PATH = ROOT / ".social_runs" / "vultr_usage.json"
DEFAULT_BUDGET_USD = 100.0
DEFAULT_PRICE_PER_MTOK = 2.75


@dataclass(frozen=True)
class Spec:
    provider: str
    model: str
    dims: int
    field: str
    index: str


class SocialEmbeddingError(RuntimeError):
    """Sanitized provider failure. status_code is None for network errors and timeouts."""

    def __init__(self, category: str, status_code: int | None = None):
        super().__init__(f"social embedding failed ({category}{'' if status_code is None else f', HTTP {status_code}'})")
        self.category = category
        self.status_code = status_code
        self.code = status_code
        self.retry_after = None


_ENV_LOADED = False


def _load_env():
    global _ENV_LOADED
    if not _ENV_LOADED:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
        load_dotenv(ROOT / "backend" / ".env")
        _ENV_LOADED = True


def provider() -> str:
    _load_env()
    name = os.getenv("SOCIAL_EMBED_PROVIDER", "vultr").strip().lower() or "vultr"
    if name not in ("vultr", "gemini"):
        raise ValueError("SOCIAL_EMBED_PROVIDER must be vultr or gemini")
    return name


def spec(name: str | None = None) -> Spec:
    name = name or provider()
    if name == "gemini":
        return Spec("gemini", GEMINI_MODEL, GEMINI_DIMENSIONS, "embedding", "social_posts_vector")
    _load_env()
    return Spec("vultr", os.getenv("VULTR_EMBED_MODEL") or VULTR_DEFAULT_MODEL, VULTR_DIMENSIONS,
                "embedding_vultr", "social_posts_vultr_vector")


def stored_vector(post: dict, s: Spec | None = None) -> list | None:
    """The post's vector for this provider, or None. Provenance comes from the vector field.

    embedding only ever holds gemini-embedding-2 output. embedding_model and dims describe
    embedding_vultr after a Vultr write, so they are checked only for the Vultr field.
    """
    s = s or spec()
    vector = post.get(s.field)
    if not isinstance(vector, list) or len(vector) != s.dims:
        return None
    if s.provider == "vultr" and (post.get("embedding_model") != s.model or post.get("dims") != s.dims):
        return None
    return vector


def pending_filter(s: Spec | None = None) -> dict:
    s = s or spec()
    text = {"text": {"$type": "string", "$ne": ""}}
    if s.provider == "gemini":
        return {**text, f"{s.field}.{s.dims - 1}": {"$exists": False}}
    return {**text, "$or": [{f"{s.field}.{s.dims - 1}": {"$exists": False}},
                            {"embedding_model": {"$ne": s.model}}, {"dims": {"$ne": s.dims}}]}


def embedded_filter(s: Spec | None = None) -> dict:
    s = s or spec()
    query = {f"{s.field}.{s.dims - 1}": {"$exists": True}}
    if s.provider == "vultr":
        query.update(embedding_model=s.model, dims=s.dims)
    return query


def _unit(vector, dims: int) -> list[float]:
    values = [float(v) for v in vector]
    if len(values) != dims or not all(math.isfinite(v) for v in values):
        raise SocialEmbeddingError("invalid_vector")
    norm = math.sqrt(sum(v * v for v in values))
    if norm == 0:
        raise SocialEmbeddingError("invalid_vector")
    return [v / norm for v in values]


_CLIENT = None
_CLIENT_LOCK = threading.Lock()


def _client():
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None:
            import httpx
            _load_env()
            key = os.getenv("VULTR_API_KEY")
            if not key:
                raise SocialEmbeddingError("credential_missing")
            _CLIENT = httpx.Client(headers={"Authorization": f"Bearer {key}"},
                                   limits=httpx.Limits(max_connections=16, max_keepalive_connections=16))
        return _CLIENT


def _retry_after(response) -> float | None:
    try:
        return max(0.0, float(response.headers.get("retry-after")))
    except (TypeError, ValueError):
        return None


def budget() -> dict:
    _load_env()
    usd = float(os.getenv("VULTR_BUDGET_USD") or DEFAULT_BUDGET_USD)
    price = float(os.getenv("VULTR_PRICE_PER_MTOK") or DEFAULT_PRICE_PER_MTOK)
    if not (0 < usd and 0 < price):
        raise ValueError("VULTR_BUDGET_USD and VULTR_PRICE_PER_MTOK must be positive")
    return {"budget_usd": usd, "price_per_mtok": price, "cap_tokens": usd / price * 1_000_000}


def estimate_tokens(texts: list[str]) -> int:
    """Deliberate overestimate: about two characters per token plus per-input overhead."""
    return sum(len(t) // 2 + 8 for t in texts)


def _ledger_update(change):
    """Read-modify-write the shared ledger under an exclusive cross-process lock."""
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER_PATH.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            ledger = json.loads(LEDGER_PATH.read_text()) if LEDGER_PATH.exists() else {}
        except ValueError:
            # A corrupt ledger must not silently reset spending to zero.
            raise SocialEmbeddingError("budget_ledger_unreadable") from None
        ledger.setdefault("tokens", 0.0)
        ledger.setdefault("requests", 0)
        result = change(ledger)
        ledger["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        tmp = LEDGER_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(ledger, indent=2) + "\n")
        os.replace(tmp, LEDGER_PATH)
        return result


def _reserve(estimate: int):
    limits = budget()
    def change(ledger):
        if ledger["tokens"] + estimate > limits["cap_tokens"]:
            raise SocialEmbeddingError("budget")
        ledger["tokens"] += estimate
        ledger["requests"] += 1
        ledger.update(cap_tokens=limits["cap_tokens"], budget_usd=limits["budget_usd"],
                      price_per_mtok=limits["price_per_mtok"])
    _ledger_update(change)


def _settle(estimate: int, actual):
    """Replace a reservation with the provider-reported count. Failed attempts keep the estimate."""
    if isinstance(actual, (int, float)) and actual >= 0:
        def change(ledger):
            ledger["tokens"] += actual - estimate
            ledger["reported_tokens"] = ledger.get("reported_tokens", 0) + actual
        _ledger_update(change)


def usage_report() -> dict:
    limits = budget()
    ledger = json.loads(LEDGER_PATH.read_text()) if LEDGER_PATH.exists() else {"tokens": 0.0, "requests": 0}
    spent = ledger["tokens"] / 1_000_000 * limits["price_per_mtok"]
    return {**limits, "tokens": round(ledger["tokens"]), "requests": ledger["requests"],
            "estimated_usd_at_assumed_price": round(spent, 4),
            "remaining_usd_at_assumed_price": round(limits["budget_usd"] - spent, 4)}


def _vultr(texts: list[str], *, timeout: float, attempts: int, sleep, max_backoff: float = 30.0) -> list[list[float]]:
    import httpx
    s = spec("vultr")
    estimate = estimate_tokens(texts)
    url = (os.getenv("VULTR_BASE_URL") or VULTR_DEFAULT_BASE).rstrip("/") + "/embeddings"
    body = {"model": s.model, "input": texts, "dimensions": s.dims}
    for attempt in range(1, attempts + 1):
        wait = None
        # Every attempt, including retries, is charged before it is sent.
        _reserve(estimate)
        try:
            response = _client().post(url, json=body, timeout=timeout)
        except httpx.TimeoutException:
            error = SocialEmbeddingError("timeout")
        except httpx.HTTPError:
            error = SocialEmbeddingError("network")
        else:
            code = response.status_code
            if code == 200:
                try:
                    _settle(estimate, (response.json().get("usage") or {}).get("total_tokens"))
                except SocialEmbeddingError:
                    raise
                except Exception:
                    pass
                try:
                    data = sorted(response.json()["data"], key=lambda row: row["index"])
                    if [row["index"] for row in data] != list(range(len(texts))):
                        raise ValueError
                    return [_unit(row["embedding"], s.dims) for row in data]
                except SocialEmbeddingError:
                    raise
                except Exception:
                    raise SocialEmbeddingError("malformed_response", code) from None
            category = {401: "credential", 402: "billing", 403: "permission", 429: "quota"}.get(code, "http")
            error = SocialEmbeddingError(category, code)
            if code not in _RETRYABLE:
                # Authentication and other client errors stop promptly.
                raise error
            wait = _retry_after(response)
            error.retry_after = wait
        if attempt == attempts:
            raise error
        backoff = min(max_backoff, 2 ** (attempt - 1)) * (0.75 + random.random() / 2)
        sleep(min(max_backoff, wait) if wait is not None else backoff)
    raise SocialEmbeddingError("exhausted")


def embed_texts(texts: list[str], *, timeout: float = 60.0, attempts: int = 5, sleep=time.sleep) -> list[list[float]]:
    """One unit vector per text, in order. Vultr sends the list in one request."""
    if not texts or any(not isinstance(t, str) or not t.strip() for t in texts):
        raise SocialEmbeddingError("empty_text")
    texts = [t.strip() for t in texts]
    if provider() == "gemini":
        from backend.services.embeddings import embed_text
        return [_unit(embed_text(t), GEMINI_DIMENSIONS) for t in texts]
    return _vultr(texts, timeout=timeout, attempts=attempts, sleep=sleep)


def embed_query(text: str, *, timeout: float = 0.45) -> list[float]:
    """Runtime query vector with one attempt and a short HTTP timeout. Callers return null on error."""
    return embed_texts([text], timeout=timeout, attempts=1)[0]
