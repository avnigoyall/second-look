"""item_materials: one document per scanned item, upserted as soon as the item is scanned.

- item_key is sha256 of the canonical product URL when there is one, else sha256 of the image bytes.
  Unique index on item_key.
- Image bytes are never stored.
- Resume: an item whose stored document has status "ok" and the same model_id, labels_config_hash and
  thresholds_hash is skipped unless rescan=True.
- Fallback: a failed Mongo write is appended to .social_runs/item_materials.jsonl and synced at the next start
  (sync_fallback). A result is never dropped.

See docs/contract_proposal_item_materials.md for the document shape.
"""
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urlsplit, urlunsplit

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
FALLBACK_PATH = ROOT / ".social_runs" / "item_materials.jsonl"
COLLECTION = "item_materials"
STATUSES = ("ok", "model_unavailable", "image_fetch_failed")


def canonical_url(url: str) -> str:
    """Lowercase scheme and host, no query or fragment, no trailing slash."""
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


def item_key(url: str | None = None, image_bytes: bytes | None = None) -> str:
    if url:
        return hashlib.sha256(canonical_url(url).encode()).hexdigest()
    if image_bytes:
        return hashlib.sha256(image_bytes).hexdigest()
    raise ValueError("item_key needs a product URL or image bytes")


def _default_collection():
    from backend.db import db  # imported lazily: tests run without a database
    return db[COLLECTION]


def _json_default(value):
    if isinstance(value, datetime):
        return {"$date": value.isoformat()}
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def _json_hook(obj):
    if set(obj) == {"$date"}:
        return datetime.fromisoformat(obj["$date"])
    return obj


class ItemMaterialsStore:
    def __init__(self, collection=None, fallback_path: Path = FALLBACK_PATH):
        self.collection = collection if collection is not None else _default_collection()
        self.fallback_path = Path(fallback_path)
        self._indexed = False

    def ensure_index(self) -> None:
        if not self._indexed:
            self.collection.create_index("item_key", unique=True, name="item_key_unique")
            self._indexed = True

    def upsert(self, doc: dict) -> str:
        """Write one document by item_key. Returns "db" or "fallback". Never raises on a write failure."""
        if "image_bytes" in doc or "image" in doc:
            raise ValueError("image bytes are never stored in item_materials")
        if doc.get("status") not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        try:
            self.ensure_index()
            self.collection.replace_one({"item_key": doc["item_key"]}, doc, upsert=True)
            return "db"
        except Exception as exc:
            log.warning("item_materials write failed, appending to %s: %s", self.fallback_path, exc)
            self.fallback_path.parent.mkdir(parents=True, exist_ok=True)
            with self.fallback_path.open("a") as fh:
                fh.write(json.dumps(doc, default=_json_default) + "\n")
                fh.flush()
            return "fallback"

    def sync_fallback(self) -> dict:
        """Upsert every queued jsonl line. Lines that still fail stay in the file. Returns counts."""
        if not self.fallback_path.exists():
            return {"synced": 0, "remaining": 0}
        lines = [ln for ln in self.fallback_path.read_text().splitlines() if ln.strip()]
        remaining = []
        for line in lines:
            doc = json.loads(line, object_hook=_json_hook)
            try:
                self.ensure_index()
                self.collection.replace_one({"item_key": doc["item_key"]}, doc, upsert=True)
            except Exception as exc:
                log.warning("item_materials sync still failing: %s", exc)
                remaining.append(line)
        if remaining:
            self.fallback_path.write_text("\n".join(remaining) + "\n")
        else:
            self.fallback_path.unlink()
        return {"synced": len(lines) - len(remaining), "remaining": len(remaining)}

    def get(self, key: str) -> dict | None:
        return self.collection.find_one({"item_key": key})

    def should_skip(self, key: str, model_id: str, labels_hash: str, thresholds_hash: str,
                    rescan: bool = False) -> bool:
        if rescan:
            return False
        doc = self.get(key)
        return bool(doc and doc.get("status") == "ok" and doc.get("model_id") == model_id
                    and doc.get("labels_config_hash") == labels_hash
                    and doc.get("thresholds_hash") == thresholds_hash)

    def all_docs(self, query: dict | None = None) -> list[dict]:
        return list(self.collection.find(query or {}, {"_id": 0}))


def build_doc(*, key: str, source: str, source_url: str | None, stated_composition: str | None,
              stated_group: str | None, estimate: dict | None, model_id: str, labels_hash: str,
              thresholds_hash: str, run_id: str, status: str, hand_labeled: bool = False,
              composition_hidden_for_eval: bool = True, extra: dict | None = None) -> dict:
    est = estimate or {}
    doc = {
        "item_key": key, "source": source, "source_url": source_url,
        "stated_composition": stated_composition, "stated_group": stated_group,
        "estimate": {k: est.get(k) for k in ("group", "score", "margin", "confidence")},
        "model_id": model_id, "labels_config_hash": labels_hash, "thresholds_hash": thresholds_hash,
        "scanned_at": datetime.now(timezone.utc),
        "flags": {"hand_labeled": hand_labeled, "composition_hidden_for_eval": composition_hidden_for_eval},
        "run_id": run_id, "status": status,
    }
    if extra:
        doc.update(extra)
    return doc


def run_items(store: ItemMaterialsStore, items: Iterable[dict], scan: Callable[[dict], dict], *,
              model_id: str, labels_hash: str, thresholds_hash: str, rescan: bool = False,
              on_done: Callable[[dict, dict, str], None] | None = None) -> dict:
    """Scan items in order. Each item dict has "key". scan(item) returns the document to store. The document
    is upserted before the next item starts. Queued fallback lines are synced first."""
    counts = {"synced_from_fallback": store.sync_fallback()["synced"], "skipped": 0, "scanned": 0,
              "fallback_writes": 0}
    for item in items:
        if store.should_skip(item["key"], model_id, labels_hash, thresholds_hash, rescan):
            counts["skipped"] += 1
            continue
        doc = scan(item)
        where = store.upsert(doc)
        counts["scanned"] += 1
        counts["fallback_writes"] += where == "fallback"
        if on_done:
            on_done(item, doc, where)
    return counts
