"""MongoDB connection and indexes."""
from pymongo import ASCENDING, DESCENDING, MongoClient

from backend.settings import DB_NAME, MONGODB_URI

if not MONGODB_URI:
    raise RuntimeError("MONGODB_URI is not set. Add it to .env in the project root.")

client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
db = client[DB_NAME]

users = db["users"]
closet_items = db["closet_items"]
trends = db["trends"]
verdicts = db["verdicts"]
ingest_jobs = db["ingest_jobs"]


def ensure_indexes():
    users.create_index([("email", ASCENDING)], unique=True)
    verdicts.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
    closet_items.create_index([("user_id", ASCENDING), ("created_at", ASCENDING)])
    closet_items.create_index([("source_verdict_id", ASCENDING)], unique=True, sparse=True)  # one auto-add per verdict
    ingest_jobs.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
