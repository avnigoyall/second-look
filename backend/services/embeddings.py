"""Part 3 embeddings: Gemini "gemini-embedding-2", 768 dimensions, unit length.

Gotcha: gemini-embedding-2 merges a list of contents into ONE embedding. It does not return one vector per
item like older embedding models. Every text or image therefore gets its own call. embed_many() runs those
calls in parallel.
"""
import logging
import math
from concurrent.futures import ThreadPoolExecutor

from google.genai import types

from backend.services.item_understanding import _get_client  # reuse Part 2's Gemini client

log = logging.getLogger("secondlook.embeddings")

EMBEDDING_MODEL = "gemini-embedding-2"
DIMENSIONS = 768
PARALLEL_CALLS = 8
_CONFIG = types.EmbedContentConfig(output_dimensionality=DIMENSIONS)


class EmbeddingError(Exception):
    pass


def _normalize(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in values))
    if norm == 0:
        raise EmbeddingError("Gemini returned an all-zero embedding")
    return [v / norm for v in values]


def _embed(content) -> list[float]:
    try:
        response = _get_client().models.embed_content(model=EMBEDDING_MODEL, contents=content, config=_CONFIG)
    except Exception as e:
        # Never fall back to another model: vectors from different models cannot be compared.
        # Provider errors can include request text and credentials.
        log.warning("embedding call failed: %s", type(e).__name__)
        raise EmbeddingError(f"{EMBEDDING_MODEL} call failed ({getattr(e, 'code', None) or type(e).__name__})") from e
    values = list(response.embeddings[0].values) if response.embeddings else []
    if len(values) != DIMENSIONS:
        raise EmbeddingError(f"{EMBEDDING_MODEL} returned {len(values)} dimensions, expected {DIMENSIONS}")
    return _normalize(values)


def embed_text(text: str) -> list[float]:
    """768 floats, unit length. Raises EmbeddingError on an empty text or a failed call."""
    if not text or not text.strip():
        raise EmbeddingError("cannot embed an empty text")
    return _embed(text.strip())


def embed_image(jpeg_bytes: bytes) -> list[float]:
    """768 floats, unit length, for one JPEG image."""
    return _embed(types.Part.from_bytes(data=jpeg_bytes, mime_type="image/jpeg"))


def embed_many(texts: list[str]) -> list[list[float]]:
    """One embedding per text, in order, with the calls run in parallel."""
    if not texts:
        return []
    with ThreadPoolExecutor(max_workers=min(PARALLEL_CALLS, len(texts))) as pool:
        return list(pool.map(embed_text, texts))
