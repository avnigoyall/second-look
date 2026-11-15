"""Photo-only material estimate with FashionCLIP (patrickjohncyh/fashion-clip), zero-shot, local, no paid API.

Scope rules:
- Flag MATERIALS_PHOTO_ENABLED, default off.
- Called only when the item has no stated composition (estimate_for_item checks this first).
- Output is one labeled line ("Estimated from photo: likely wool or knit, low confidence"). It never changes a
  verdict and never enters a numeric rule. Confidence is capped at "medium".
- If the model cannot load (download failure, missing torch), the result is group "unknown" with status
  "model_unavailable" and the exact error. It never raises to the caller.

Scoring: each group has several prompt phrasings in config/materials_labels.json. Text embeddings are L2
normalized, averaged per group and normalized again. The image embedding is compared with each group
embedding, scaled by the model's logit_scale and softmaxed over the groups. score is the top probability,
margin is top minus second.
"""
import hashlib
import io
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

from backend.services.composition import parse_composition

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = ROOT / "config" / "materials_labels.json"
SOURCE = "photo_estimate"
UNKNOWN = "unknown"


def load_config(path: Path = CONFIG_PATH) -> dict:
    return json.loads(path.read_text())


CONFIG = load_config()
MODEL_ID = CONFIG["model_id"]


def _hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def labels_config_hash(config: dict = CONFIG) -> str:
    """Changes when the model, the groups or any prompt changes."""
    return _hash({"model_id": config["model_id"], "groups": config["groups"]})


def thresholds_hash(config: dict = CONFIG) -> str:
    return _hash({k: v for k, v in config["thresholds"].items() if not k.startswith("_")})


def enabled() -> bool:
    return os.getenv("MATERIALS_PHOTO_ENABLED", "0").lower() in ("1", "true", "yes")


# ---------- composition -> stated group (eval labels) ----------

_SECONDARY_WORDS = re.compile(r"\b(lining|linings|liner|yoke|pocket(?:ing)?s?|trim)\b", re.I)
_LABELED_SECTION = re.compile(r"\b(lining|linings|liner|yoke|pocket(?:ing)?s?|trim)\s*[:\-]", re.I)


def _main_fabric_text(composition: str) -> str:
    """Drop unlabeled clauses about secondary parts ("100% polyester taffeta yoke and sleeve lining").
    Clauses with a labeled section ("Lining: 100% polyester") are kept: parse_composition handles those."""
    text = re.sub(r"<[^>]+>", ". ", composition)
    clauses = re.split(r"[.;•\n]+", text)
    return ". ".join(c for c in clauses
                     if not (_SECONDARY_WORDS.search(c) and not _LABELED_SECTION.search(c)))


def stated_group(composition: str | None, config: dict = CONFIG) -> str | None:
    """Coarse group from stated composition text by the documented majority rule. None when nothing parses,
    "ambiguous" when no group is above majority_pct or a fiber is unmapped."""
    if not composition:
        return None
    fibers = parse_composition(_main_fabric_text(composition))
    if not fibers:
        return None
    mapping = config["composition_mapping"]
    totals: dict[str, float] = {}
    for f in fibers:
        group = mapping["fiber_to_group"].get(f["fiber"])
        if group is None:
            return "ambiguous"
        totals[group] = totals.get(group, 0) + (f["pct"] or 0)
    best = max(totals, key=totals.get)
    return best if totals[best] > mapping["majority_pct"] else "ambiguous"


def has_stated_composition(item: dict) -> bool:
    """True when the page states a composition (Item.materials, or a composition in the description)."""
    text = (item.get("materials") or "").strip()
    return bool(text) or bool(parse_composition(item.get("description")))


# ---------- model ----------

class _Model:
    """Lazy FashionCLIP. Loads once on first use; a failed load is remembered and not retried in-process."""

    def __init__(self, model_id: str):
        self.model_id = model_id
        self._lock = threading.Lock()
        self.loaded = False
        self.error: str | None = None
        self.device: str | None = None
        self.load_seconds: float | None = None
        self._model = self._processor = self._text = self._groups = None

    def ensure(self, config: dict) -> bool:
        if self.loaded or self.error:
            return self.loaded
        with self._lock:
            if self.loaded or self.error:
                return self.loaded
            start = time.monotonic()
            try:
                import torch
                from transformers import CLIPModel, CLIPProcessor
                self.device = "mps" if torch.backends.mps.is_available() else "cpu"
                self._model = CLIPModel.from_pretrained(self.model_id).to(self.device).eval()
                self._processor = CLIPProcessor.from_pretrained(self.model_id)
                self._groups, self._text = self._text_embeddings(config)
                self.loaded = True
            except Exception as exc:  # download, network, missing package: non-fatal by design
                self.error = f"{type(exc).__name__}: {exc}"
                log.warning("FashionCLIP unavailable: %s", self.error)
            self.load_seconds = time.monotonic() - start
            return self.loaded

    def _text_embeddings(self, config: dict):
        import torch
        groups, rows = list(config["groups"]), []
        with torch.no_grad():
            for g in groups:
                prompts = config["groups"][g]["prompts"]
                tok = self._processor.tokenizer(prompts, padding=True, truncation=True, return_tensors="pt")
                tok = {k: v.to(self.device) for k, v in tok.items()}
                # Explicit projection: stable across transformers versions (v5 changed get_text_features).
                pooled = self._model.text_model(**tok).pooler_output
                emb = self._model.text_projection(pooled)
                emb = emb / emb.norm(dim=-1, keepdim=True)
                mean = emb.mean(dim=0)
                rows.append(mean / mean.norm())  # re-normalize the mean
        return groups, torch.stack(rows)

    def probabilities(self, image) -> dict[str, float]:
        import torch
        pixels = self._processor.image_processor(images=image.convert("RGB"), return_tensors="pt")
        with torch.no_grad():
            pooled = self._model.vision_model(pixel_values=pixels["pixel_values"].to(self.device)).pooler_output
            img = self._model.visual_projection(pooled)
            img = img / img.norm(dim=-1, keepdim=True)
            logits = self._model.logit_scale.exp() * img @ self._text.T
            probs = logits.softmax(dim=-1)[0].float().cpu().tolist()
        return dict(zip(self._groups, probs))


_MODEL = _Model(MODEL_ID)


def model_status() -> dict:
    return {"model_id": _MODEL.model_id, "loaded": _MODEL.loaded, "error": _MODEL.error,
            "device": _MODEL.device, "load_seconds": _MODEL.load_seconds}


# ---------- estimate ----------

def decide(probs: dict[str, float], config: dict = CONFIG) -> dict:
    """Group, score, margin and capped confidence from group probabilities and the config thresholds."""
    t = config["thresholds"]
    ranked = sorted(probs.items(), key=lambda kv: -kv[1])
    top, score = ranked[0]
    margin = score - (ranked[1][1] if len(ranked) > 1 else 0.0)
    out = {"group": top, "score": round(score, 4), "margin": round(margin, 4), "source": SOURCE,
           "confidence": "low"}
    if score < t["min_score"] or margin < t["min_margin"]:
        out.update(group=UNKNOWN, confidence=None)
    elif score >= t["medium_score"] and margin >= t["medium_margin"]:
        out["confidence"] = "medium"  # the cap: never "high" from a photo
    return out


def _unknown(status: str, error: str | None = None) -> dict:
    return {"group": UNKNOWN, "score": None, "margin": None, "source": SOURCE, "confidence": None,
            "status": status, "error": error}


def estimate_image(image_bytes: bytes, config: dict = CONFIG) -> dict:
    """Estimate from one image. Adds status ("ok", "model_unavailable", "image_unreadable"), latency_ms and
    probs. Never raises."""
    if not _MODEL.ensure(config):
        return _unknown("model_unavailable", _MODEL.error)
    try:
        from PIL import Image
        image = Image.open(io.BytesIO(image_bytes))
        image.load()
    except Exception as exc:
        return _unknown("image_unreadable", f"{type(exc).__name__}: {exc}")
    start = time.monotonic()
    probs = _MODEL.probabilities(image)
    out = decide(probs, config)
    out.update(status="ok", error=None, latency_ms=round((time.monotonic() - start) * 1000, 1),
               probs={g: round(p, 4) for g, p in probs.items()})
    return out


def estimate_for_item(item: dict, image_bytes: bytes | None) -> dict | None:
    """The only entry point for product flows. None when the flag is off, the item states a composition,
    or there is no image. The estimator is not called in those cases."""
    if not enabled() or has_stated_composition(item) or not image_bytes:
        return None
    return estimate_image(image_bytes)


def photo_line(estimate: dict | None, config: dict = CONFIG) -> str | None:
    """The single labeled line this feature may show, or None."""
    if not estimate or estimate.get("group") in (None, UNKNOWN) or not estimate.get("confidence"):
        return None
    display = config["groups"][estimate["group"]]["display"]
    return f"Estimated from photo: likely {display}, {estimate['confidence']} confidence"
