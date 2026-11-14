"""Part 3 closet intake: photos, a video, and free text become candidate closet items for the review page.

Pipeline (run_job): video to frames -> extract garments (Gemini, 10 images per call) -> merge duplicates
(one text-only Gemini call) -> compare to the existing closet ($vectorSearch) -> crop thumbnails -> name.
"""
import base64
import io
import json
import logging
import math
import shutil
import subprocess
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bson import ObjectId
from PIL import Image

from backend.db import ingest_jobs
from backend.services.closet import item_name, query_texts, search_closet, thresholds
from backend.services.embeddings import embed_many
from backend.services.gemini_json import GeminiError, generate_json
from backend.services.item_understanding import (
    FABRIC_CONFIDENCE, _enum_schema, attribute_string, resize_image)
from backend.settings import ATTRIBUTES

log = logging.getLogger("secondlook.ingest")

MAX_IMAGES = 15
MAX_VIDEOS = 1
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_VIDEO_BYTES = 300 * 1024 * 1024
MAX_TEXT_CHARS = 4000
MAX_FRAMES = 40
FFMPEG_TIMEOUT_S = 180
IMAGES_PER_CALL = 10
PARALLEL_EXTRACT_CALLS = 3
EXTRACT_TIMEOUT_MS = 120_000
TEXT_TIMEOUT_MS = 60_000
MAX_CANDIDATES = 80
LOW_CONFIDENCE = 0.6
CROP_PAD = 0.05
THUMB_SIDE = 600
THUMB_QUALITY = 80
MIN_BOX_PX = 12
STALE_AFTER = timedelta(minutes=20)  # a job still "running" after this died with a server restart
FFMPEG_MISSING = "ffmpeg is not installed, so videos cannot be read. Run: brew install ffmpeg"


class IngestError(Exception):
    """A user-facing failure. The message is shown on the review page."""


def now():
    return datetime.now(timezone.utc)


# ---------- schemas ----------

ATTRIBUTE_PROPS = {
    "category": {"type": "string", "enum": ATTRIBUTES["category"]},
    **{f: _enum_schema(ATTRIBUTES[f]) for f in ("color_family", "fabric", "cut", "style")},
    "fabric_confidence": {"type": "string", "enum": FABRIC_CONFIDENCE},
    "description": {"type": "string"},
    "confidence": {"type": "number"},
}
ATTRIBUTE_REQUIRED = list(ATTRIBUTE_PROPS)

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "image_index": {"type": "integer"},
            "box_2d": {"type": "array", "items": {"type": "integer"}, "minItems": 4, "maxItems": 4},
            **ATTRIBUTE_PROPS,
            "brand_visible": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        },
        "required": ["image_index", "box_2d", *ATTRIBUTE_REQUIRED, "brand_visible"],
    }}},
    "required": ["items"],
}

TEXT_SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object", "properties": ATTRIBUTE_PROPS, "required": ATTRIBUTE_REQUIRED}}},
    "required": ["items"],
}

MERGE_SCHEMA = {
    "type": "object",
    "properties": {"groups": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}}},
    "required": ["groups"],
}

ATTRIBUTE_RULES = """- category, color_family, fabric, cut, style: use only the allowed values in the schema.
- color_family is the dominant color. Use "multi" only when no single color dominates.
- fabric_confidence: "high" only if a label or the text states the fabric, "medium" if it is clear from the
  look (denim, knit wool, leather), "low" if you are guessing. Use fabric "unknown" when you cannot tell.
- When unsure of a value, use "unknown" or "other" if the list has one, otherwise null.
- description: at most 20 words, plain and factual (color, cut, notable details). Never invent a brand.
- confidence: 0 to 1, how sure you are that this entry is a real, distinct garment and the labels are right."""

EXTRACT_PROMPT = """You catalog a person's own closet from their photos and video frames.
There are {n} images below, numbered 0 to {last}. For each image, list every distinct garment it shows.

Rules:
- One entry per distinct garment in an image. A pile of 3 tees is 3 entries if each is clearly visible.
- Include garments worn by a person (top, bottom, outerwear, shoes, bag). Ignore the person and background.
- A pair of shoes (or socks, gloves, earrings) is ONE entry with one box around both.
- Skip items that are tiny, cut off so much that the type is unclear, blurry, or not clothing.
- image_index: the number of the image the garment is in.
- box_2d: [ymin, xmin, ymax, xmax] around that garment only, as integers normalized 0 to 1000.
- brand_visible: the brand name only if a logo or label is clearly readable, otherwise null.
{rules}"""

TEXT_PROMPT = """A person describes clothes they own. Turn the text into closet items.

Rules:
- One entry per physical item. "two white tees" is two entries. Ignore anything that is not clothing,
  shoes, a bag, or an accessory. At most 30 entries.
- Use only what the text says or clearly implies. Do not invent details. If the text does not give a
  value, use "unknown"/"other" if the list has one, otherwise null.
- description: a short plain rewording of what the text says about that item.
{rules}

Text:
{text}"""

MERGE_PROMPT = """These garment candidates were detected in several photos and video frames of ONE person's
closet. Find candidates that are the SAME physical item seen more than once.

Rules:
- Candidates with the same image_index are always different items. Never group them.
- Video frames next to each other (close frame numbers) often show the same item again.
- Group only when category and color match and nothing in the description contradicts. Two plain items
  of the same type and color from different photos are probably the same item only if nothing differs.
- Return only groups with two or more ids. Each id may appear in at most one group.

Candidates (JSON):
{candidates}"""


# ---------- upload checks (run in the request, before the job starts) ----------

def load_image(raw: bytes, filename: str) -> bytes:
    """Return 1024px JPEG bytes. Raises IngestError for an unreadable image."""
    if len(raw) > MAX_IMAGE_BYTES:
        raise IngestError(f"{filename} is larger than 20 MB")
    try:
        return resize_image(raw)
    except Exception:
        raise IngestError(f"Could not read {filename} as an image. HEIC is not supported, export it as JPEG")


def require_ffmpeg():
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise IngestError(FFMPEG_MISSING)


# ---------- a. video to frames ----------

def video_frames(path: Path) -> list[bytes]:
    """About 1 frame per second, spread over the whole video, at most 40 frames, as 1024px JPEG bytes."""
    require_ffmpeg()
    duration = None
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30)
        duration = float(probe.stdout.strip())
    except (ValueError, subprocess.TimeoutExpired):
        pass
    fps = 1.0 if not duration or duration <= MAX_FRAMES else MAX_FRAMES / duration
    with tempfile.TemporaryDirectory() as out:
        try:
            subprocess.run(
                ["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"fps={fps:.4f}", "-frames:v", str(MAX_FRAMES),
                 "-q:v", "3", str(Path(out) / "f_%03d.jpg")],
                capture_output=True, text=True, timeout=FFMPEG_TIMEOUT_S, check=True)
        except subprocess.CalledProcessError as e:
            log.warning("ffmpeg failed: %s", e.stderr[-500:])
            raise IngestError("Could not read the video. Try an MP4 or MOV file")
        except subprocess.TimeoutExpired:
            raise IngestError("Reading the video took too long. Try a shorter clip")
        frames = [resize_image(p.read_bytes()) for p in sorted(Path(out).glob("f_*.jpg"))]
    if not frames:
        raise IngestError("The video has no readable frames")
    return frames


# ---------- b. extract ----------

def _clamp01(value) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _clean_attrs(raw: dict) -> dict | None:
    """Check enum values against attributes.json. Returns None when the entry is unusable."""
    if raw.get("category") not in ATTRIBUTES["category"]:
        return None
    attrs = {}
    for field, allowed in ATTRIBUTES.items():
        value = raw.get(field)
        attrs[field] = value if value in allowed else None
    confidence = raw.get("fabric_confidence")
    attrs["fabric_confidence"] = "low" if attrs["fabric"] in (None, "unknown") else (
        confidence if confidence in FABRIC_CONFIDENCE else "low")
    return attrs


def _candidate(raw: dict, origin: str, **extra) -> dict | None:
    attrs = _clean_attrs(raw)
    if attrs is None:
        return None
    description = " ".join(str(raw.get("description") or "").split()[:25])
    return {"id": uuid.uuid4().hex[:12], "origin": origin, "attributes": attrs, "description": description,
            "confidence": round(_clamp01(raw.get("confidence")), 2), "name": item_name(attrs),
            "thumbnail": None, "likely_duplicate_of": None, **extra}


def _extract_batch(images: list[bytes]) -> list[dict]:
    from google.genai import types  # local import keeps module import light for scripts
    contents = []
    for i, jpeg in enumerate(images):
        contents += [f"Image {i}:", types.Part.from_bytes(data=jpeg, mime_type="image/jpeg")]
    contents.append(EXTRACT_PROMPT.format(n=len(images), last=len(images) - 1, rules=ATTRIBUTE_RULES))
    return generate_json(contents, EXTRACT_SCHEMA, EXTRACT_TIMEOUT_MS, "extract").get("items", [])


def extract(images: list[dict], on_batch_done) -> list[dict]:
    """images: [{jpeg, origin, frame}] in global order. Returns candidates with a global image_index."""
    batches = [list(range(s, min(s + IMAGES_PER_CALL, len(images)))) for s in range(0, len(images), IMAGES_PER_CALL)]

    def run(batch):
        raw_items = _extract_batch([images[i]["jpeg"] for i in batch])
        out = []
        for raw in raw_items:
            local = raw.get("image_index")
            if not isinstance(local, int) or not 0 <= local < len(batch):
                continue
            g = batch[local]
            c = _candidate(raw, images[g]["origin"], image_index=g, frame=images[g].get("frame"),
                           box_2d=raw.get("box_2d"), brand_visible=raw.get("brand_visible") or None)
            if c:
                out.append(c)
        on_batch_done()
        return out

    with ThreadPoolExecutor(max_workers=PARALLEL_EXTRACT_CALLS) as pool:
        results = list(pool.map(run, batches))
    return [c for batch in results for c in batch]


# ---------- free text ----------

def parse_text(text: str) -> list[dict]:
    text = (text or "").strip()[:MAX_TEXT_CHARS]
    if not text:
        return []
    reply = generate_json([TEXT_PROMPT.format(text=text, rules=ATTRIBUTE_RULES)], TEXT_SCHEMA, TEXT_TIMEOUT_MS,
                          "parse-text")
    items = [_candidate(raw, "text", image_index=None, frame=None, box_2d=None, brand_visible=None)
             for raw in reply.get("items", [])[:30]]
    return [c for c in items if c]


# ---------- c. merge duplicates ----------

def merge_duplicates(candidates: list[dict]) -> list[dict]:
    """One text-only Gemini call groups the same physical item. The highest-confidence member is kept."""
    if len(candidates) < 2:
        return candidates
    by_id = {c["id"]: c for c in candidates}
    listing = [{"id": c["id"], "image_index": c["image_index"], "frame": c.get("frame"),
                "item": attribute_string({**c["attributes"], "description": c["description"]})} for c in candidates]
    reply = generate_json([MERGE_PROMPT.format(candidates=json.dumps(listing))], MERGE_SCHEMA, TEXT_TIMEOUT_MS,
                          "merge")
    drop, seen = set(), set()
    for group in reply.get("groups", []):
        members = [by_id[i] for i in dict.fromkeys(group) if i in by_id and i not in seen]
        seen.update(m["id"] for m in members)
        if len(members) < 2:
            continue
        members.sort(key=lambda m: m["confidence"], reverse=True)
        keeper = members[0]
        for m in members[1:]:
            # Code guard: two candidates from the same image are never the same item, whatever the model said.
            if m["image_index"] == keeper["image_index"]:
                continue
            drop.add(m["id"])
            keeper["seen_count"] = keeper.get("seen_count", 1) + 1
    return [c for c in candidates if c["id"] not in drop]


# ---------- d. compare to the existing closet ----------

def flag_closet_duplicates(user_id: ObjectId, candidates: list[dict]) -> None:
    """Set likely_duplicate_of when the closest closet item scores at least duplicate_similarity. Never drops."""
    if not candidates:
        return
    limit = thresholds()["duplicate_similarity"]
    # Full text against described closet items, attributes-only text against hand-added ones (see closet.py).
    pairs = [query_texts({**c["attributes"], "description": c["description"]}) for c in candidates]
    texts = list(dict.fromkeys(t for pair in pairs for t in pair if t))
    vectors = dict(zip(texts, embed_many(texts)))

    def top(args):
        c, (full, attr_only) = args
        if not full:
            return c, None
        hits = search_closet(user_id, vectors[full], vectors[attr_only or full], c["attributes"].get("category"), k=1)
        return c, hits[0] if hits else None

    with ThreadPoolExecutor(max_workers=8) as pool:
        for c, hit in pool.map(top, zip(candidates, pairs)):
            if hit is None:
                continue
            score = round(2 * hit["score"] - 1, 4)  # Atlas reports (1 + cos) / 2
            if score >= limit:
                c["likely_duplicate_of"] = {"id": str(hit["_id"]), "name": hit["name"], "score": score}


# ---------- e. crop ----------

def crop_thumbnail(jpeg: bytes, box) -> tuple[str, bool]:
    """Return (JPEG data URL, cropped). An invalid box falls back to the whole image."""
    img = Image.open(io.BytesIO(jpeg))
    w, h = img.size
    cropped = False
    if isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) and math.isfinite(v) for v in box):
        ymin, xmin, ymax, xmax = (max(0.0, min(1000.0, float(v))) for v in box)
        if ymax > ymin and xmax > xmin:
            top, left, bottom, right = ymin / 1000 * h, xmin / 1000 * w, ymax / 1000 * h, xmax / 1000 * w
            pad_y, pad_x = (bottom - top) * CROP_PAD, (right - left) * CROP_PAD
            area = (max(0, int(left - pad_x)), max(0, int(top - pad_y)),
                    min(w, int(math.ceil(right + pad_x))), min(h, int(math.ceil(bottom + pad_y))))
            if area[2] - area[0] >= MIN_BOX_PX and area[3] - area[1] >= MIN_BOX_PX:
                img, cropped = img.crop(area), True
    img.thumbnail((THUMB_SIDE, THUMB_SIDE))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=THUMB_QUALITY)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode(), cropped


# ---------- the job ----------

def _progress(job_id: ObjectId, step: str, done: int, total: int, **fields):
    ingest_jobs.update_one({"_id": job_id}, {"$set": {"progress": {"step": step, "done": done, "total": total},
                                                       "updated_at": now(), **fields}})


def run_job(job_id: ObjectId, user_id: ObjectId, photos: list[bytes], video_path: str | None, text: str):
    """Background task. Writes progress, then status done (with items) or failed (with error)."""
    warnings = []
    try:
        images = [{"jpeg": j, "origin": "photo", "frame": None} for j in photos]
        if video_path:
            _progress(job_id, "frames", 0, 1, status="running")
            frames = video_frames(Path(video_path))
            images += [{"jpeg": j, "origin": "video", "frame": i} for i, j in enumerate(frames)]
            _progress(job_id, "frames", 1, 1)

        candidates = []
        if images:
            total = math.ceil(len(images) / IMAGES_PER_CALL)
            done = [0]
            _progress(job_id, "extract", 0, total, status="running")

            def batch_done():
                done[0] += 1
                _progress(job_id, "extract", done[0], total)

            candidates = extract(images, batch_done)
            if len(candidates) > 1:
                _progress(job_id, "merge", 0, 1)
                try:
                    candidates = merge_duplicates(candidates)
                except GeminiError as e:
                    warnings.append("Could not merge repeated items, so some may appear twice.")
                    log.warning("merge skipped: %s", e)
                _progress(job_id, "merge", 1, 1)

        if text.strip():
            _progress(job_id, "text", 0, 1, status="running")
            candidates += parse_text(text)
            _progress(job_id, "text", 1, 1)

        candidates = candidates[:MAX_CANDIDATES]
        _progress(job_id, "compare", 0, 1)
        try:
            flag_closet_duplicates(user_id, candidates)
        except Exception as e:  # embeddings and vector search are helpers here, never blockers
            warnings.append("Could not compare with your closet, so duplicates are not flagged.")
            log.warning("closet compare skipped: %s", str(e)[:300])
        _progress(job_id, "compare", 1, 1)

        image_items = [c for c in candidates if c["image_index"] is not None]
        for i, c in enumerate(image_items):
            c["thumbnail"], c["cropped"] = crop_thumbnail(images[c["image_index"]]["jpeg"], c["box_2d"])
            if i % 5 == 4:
                _progress(job_id, "crop", i + 1, len(image_items))
        for c in candidates:
            c["low_confidence"] = c["confidence"] < LOW_CONFIDENCE
        _progress(job_id, "done", 1, 1, status="done", items=candidates, warnings=warnings)
    except (IngestError, GeminiError) as e:
        _progress(job_id, "failed", 0, 0, status="failed", error=str(e), warnings=warnings)
    except Exception as e:
        log.exception("ingest job %s crashed", job_id)
        _progress(job_id, "failed", 0, 0, status="failed", warnings=warnings,
                  error=f"Something went wrong while reading your closet ({type(e).__name__})")
    finally:
        if video_path:
            shutil.rmtree(Path(video_path).parent, ignore_errors=True)


def is_stale(job: dict) -> bool:
    started = job.get("updated_at") or job["created_at"]
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return job["status"] in ("queued", "running") and now() - started > STALE_AFTER

