"""Seed the database. Safe to run any number of times.

Run from the project root:  python seed/seed.py
Trends are loaded separately:  python scripts/seed_trends.py
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.auth import hash_password  # noqa: E402
from backend.db import closet_items, ensure_indexes, users  # noqa: E402
from backend.images import to_data_url  # noqa: E402

PHOTOS = Path(__file__).resolve().parent / "photos"
IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".webp"}

SEED_USERS = [
    ("demo@secondlook.test", "demo1234", "demo"),
    ("new@secondlook.test", "new1234", "new"),
]

def now():
    return datetime.now(timezone.utc)


def name_from_file(path: Path) -> str:
    return path.stem.replace("_", " ").replace("-", " ").strip().capitalize()


def seed_users() -> dict:
    ids = {}
    for email, password, profile_type in SEED_USERS:
        result = users.update_one(
            {"email": email},
            {"$setOnInsert": {
                "email": email,
                "password_hash": hash_password(password),
                "profile_type": profile_type,
                "nessie_customer_id": None,
                "nessie_checking_id": None,
                "nessie_savings_id": None,
                "preference_note": None,
                "created_at": now(),
            }},
            upsert=True,
        )
        ids[profile_type] = users.find_one({"email": email})["_id"]
        print(f"user {email}: {'created' if result.upserted_id else 'already exists'}")
    return ids


def seed_closet(demo_user_id):
    files = sorted(p for p in PHOTOS.iterdir() if p.suffix.lower() in IMAGE_TYPES) if PHOTOS.exists() else []
    if not files:
        print(f"closet: no images found in {PHOTOS}, skipping")
        return
    added = 0
    for path in files:
        name = name_from_file(path)
        if closet_items.find_one({"user_id": demo_user_id, "source": "snap", "name": name}):
            continue
        closet_items.insert_one({
            "user_id": demo_user_id,
            "source": "snap",
            "name": name,
            "image_data": to_data_url(path.read_bytes()),
            "attributes": None,
            "description": "",
            "embedding": None,
            "created_at": now(),
        })
        added += 1
    print(f"closet: {added} added, {len(files) - added} already present")


if __name__ == "__main__":
    ensure_indexes()
    ids = seed_users()
    seed_closet(ids["demo"])
