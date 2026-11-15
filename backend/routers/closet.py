"""Part 3 closet endpoints: intake (photos, video, text), ownership question, and settings."""
import logging
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile

from backend import models as m
from backend.auth import current_user
from backend.db import closet_items, ingest_jobs, users
from backend.services import closet_ingest as ingest
from backend.services.closet import item_name, new_closet_doc
from backend.services.embeddings import EmbeddingError
from backend.services.gemini_json import GeminiError
from backend.services.ownership import SMALL_CLOSET, ownership_options

log = logging.getLogger("secondlook.closet")
router = APIRouter(tags=["closet"])

VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv", ".3gp"}
SOURCE_BY_ORIGIN = {"photo": "ingest_photo", "video": "ingest_video", "text": "ingest_text"}


def now():
    return datetime.now(timezone.utc)


def is_video(f: UploadFile) -> bool:
    return (f.content_type or "").startswith("video/") or Path(f.filename or "").suffix.lower() in VIDEO_SUFFIXES


def own_job(job_id: str, user) -> dict:
    try:
        job = ingest_jobs.find_one({"_id": ObjectId(job_id), "user_id": user["_id"]})
    except (InvalidId, TypeError):
        job = None
    if job is None:
        raise HTTPException(404, "Ingest job not found")
    return job


# ---------- intake ----------

@router.post("/closet/ingest", response_model=m.IngestStartResponse)
def start_ingest(background: BackgroundTasks, files: list[UploadFile] = File(default=[]), text: str = Form(""),
                 user=Depends(current_user)):
    """Upload up to 15 photos and 1 video (multipart field "files", repeat it per file) plus optional "text".

    Returns a job id right away. Poll GET /closet/ingest/{job_id}.
    """
    videos = [f for f in files if f.filename and is_video(f)]
    photos = [f for f in files if f.filename and not is_video(f)]
    if len(photos) > ingest.MAX_IMAGES:
        raise HTTPException(422, f"At most {ingest.MAX_IMAGES} photos per upload")
    if len(videos) > ingest.MAX_VIDEOS:
        raise HTTPException(422, "At most 1 video per upload")
    if not photos and not videos and not text.strip():
        raise HTTPException(422, "Add photos, a video, or a description of your clothes")
    if len(text) > ingest.MAX_TEXT_CHARS:
        raise HTTPException(422, f"Text is limited to {ingest.MAX_TEXT_CHARS} characters")

    try:
        if videos:
            ingest.require_ffmpeg()
        photo_bytes = [ingest.load_image(f.file.read(), f.filename) for f in photos]
    except ingest.IngestError as e:
        raise HTTPException(422, str(e))

    video_path = None
    if videos:
        folder = Path(tempfile.mkdtemp(prefix="secondlook-video-"))
        video_path = folder / ("upload" + (Path(videos[0].filename).suffix.lower() or ".mp4"))
        with video_path.open("wb") as out:
            shutil.copyfileobj(videos[0].file, out, length=1024 * 1024)
        if video_path.stat().st_size > ingest.MAX_VIDEO_BYTES:
            shutil.rmtree(folder, ignore_errors=True)
            raise HTTPException(422, "The video is larger than 300 MB. Trim it to a short clip")

    job = {"user_id": user["_id"], "status": "queued", "progress": {"step": "queued", "done": 0, "total": 0},
           "items": [], "warnings": [], "error": None, "created_at": now(), "updated_at": now(),
           "inputs": {"photos": len(photo_bytes), "video": bool(videos), "text": bool(text.strip())}}
    job_id = ingest_jobs.insert_one(job).inserted_id
    background.add_task(ingest.run_job, job_id, user["_id"], photo_bytes, str(video_path) if video_path else None, text)
    return {"job_id": str(job_id)}


@router.get("/closet/ingest/{job_id}", response_model=m.IngestJobResponse)
def get_ingest(job_id: str, user=Depends(current_user)):
    """Status and progress. Candidate items (with 600px thumbnails) are included once the job is done."""
    job = own_job(job_id, user)
    if ingest.is_stale(job):
        job["status"], job["error"] = "failed", "This job stopped, probably because the server restarted. Try again"
        ingest_jobs.update_one({"_id": job["_id"]}, {"$set": {"status": "failed", "error": job["error"]}})
    show_items = job["status"] in ("done", "saved")
    return {"job_id": str(job["_id"]), "status": job["status"], "progress": job["progress"],
            "items": job.get("items", []) if show_items else [], "warnings": job.get("warnings", []),
            "error": job.get("error"), "saved_count": job.get("saved_count")}


@router.post("/closet/ingest/{job_id}/save", response_model=m.IngestSaveResponse)
def save_ingest(job_id: str, body: m.IngestSaveRequest, user=Depends(current_user)):
    """Save the reviewed items. Each one is embedded and inserted into closet_items. Saving twice is refused."""
    job = own_job(job_id, user)
    # Claim the job first so a double click cannot save the same items twice.
    claimed = ingest_jobs.update_one({"_id": job["_id"], "status": "done"}, {"$set": {"status": "saving"}})
    if claimed.matched_count == 0:
        status = job["status"]
        raise HTTPException(409, "These items are already saved" if status in ("saved", "saving")
                            else f"The job is not ready to save (status {status})")
    candidates = {c["id"]: c for c in job.get("items", [])}

    def build(item: m.SaveItem) -> dict:
        cand = candidates.get(item.candidate_id or "")
        origin = cand["origin"] if cand else "text"
        return new_closet_doc(
            user["_id"], name=item.name.strip(), attributes=item.attributes.model_dump(), source=SOURCE_BY_ORIGIN[origin],
            image_data=cand.get("thumbnail") if cand else None, description=item.description.strip(),
            extra={"ingest_job_id": job["_id"]})

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            docs = list(pool.map(build, body.items))
        ids = [str(i) for i in closet_items.insert_many(docs).inserted_ids] if docs else []
    except EmbeddingError as e:
        ingest_jobs.update_one({"_id": job["_id"]}, {"$set": {"status": "done"}})
        raise HTTPException(502, f"Could not save: {e}")
    except Exception:
        ingest_jobs.update_one({"_id": job["_id"]}, {"$set": {"status": "done"}})
        raise
    ingest_jobs.update_one({"_id": job["_id"]}, {"$set": {"status": "saved", "saved_count": len(ids), "updated_at": now()}})
    return {"saved": len(ids), "ids": ids}


@router.post("/closet/parse-text", response_model=m.CandidateListResponse)
def parse_text(body: m.ParseTextRequest, user=Depends(current_user)):
    """Candidate items from free text, such as "two white tees and black jeans". No photo, no box."""
    try:
        items = ingest.parse_text(body.text)
    except GeminiError as e:
        raise HTTPException(502, str(e))
    try:
        ingest.flag_closet_duplicates(user["_id"], items)
    except Exception as e:
        log.warning("parse-text: closet compare skipped: %s", str(e)[:300])
    for c in items:
        c["low_confidence"] = c["confidence"] < ingest.LOW_CONFIDENCE
    return {"items": items}


# ---------- ownership question ----------

@router.post("/closet/ownership-options", response_model=m.OwnershipOptionsResponse)
def get_ownership_options(body: m.OwnershipOptionsRequest, user=Depends(current_user)):
    """Only for closets with fewer than 10 items. Otherwise returns show false without calling Gemini."""
    if closet_items.count_documents({"user_id": user["_id"]}, limit=SMALL_CLOSET) >= SMALL_CLOSET:
        return {"show": False}
    try:
        result = ownership_options(body.name, body.attributes.model_dump())
    except GeminiError as e:
        raise HTTPException(502, str(e))
    return {"show": bool(result["options"]), **result}


@router.post("/closet/ownership-answer", response_model=m.ClosetItemResponse)
def ownership_answer(body: m.OwnershipAnswerRequest, user=Depends(current_user)):
    attrs = body.attributes.model_dump()
    try:
        doc = new_closet_doc(user["_id"], name=(body.name or "").strip() or item_name(attrs), attributes=attrs,
                             source="ownership_answer")
    except EmbeddingError as e:
        raise HTTPException(502, f"Could not save: {e}")
    doc["_id"] = closet_items.insert_one(doc).inserted_id
    from backend.main import closet_out  # the shared converter lives in main.py
    return {"item": closet_out(doc, include_image=False)}


# ---------- settings ----------

def settings_of(user: dict) -> dict:
    return {"auto_add_purchases": (user.get("settings") or {}).get("auto_add_purchases") is not False}


@router.delete("/closet/{item_id}", response_model=m.OkResponse)
def delete_closet_item(item_id: str, user=Depends(current_user)):
    """Remove one of the user's closet items for good (closet page trash button). 404 if it is not theirs."""
    try:
        oid = ObjectId(item_id)
    except (InvalidId, TypeError):
        raise HTTPException(404, "Closet item not found")
    if closet_items.delete_one({"_id": oid, "user_id": user["_id"]}).deleted_count == 0:
        raise HTTPException(404, "Closet item not found")
    return {"ok": True}


@router.get("/settings", response_model=m.Settings)
def get_settings(user=Depends(current_user)):
    return settings_of(user)


@router.patch("/settings", response_model=m.Settings)
def patch_settings(body: m.SettingsPatch, user=Depends(current_user)):
    changes = {f"settings.{k}": v for k, v in body.model_dump(exclude_none=True).items()}
    if changes:
        users.update_one({"_id": user["_id"]}, {"$set": changes})
        user = users.find_one({"_id": user["_id"]})
    return settings_of(user)
