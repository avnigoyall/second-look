"""MongoDB connection and indexes."""
from pymongo import ASCENDING, DESCENDING, MongoClient

from backend.settings import DB_NAME, MONGODB_URI
from backend.trends import ensure_trend_indexes

if not MONGODB_URI:
    raise RuntimeError("MONGODB_URI is not set. Add it to .env in the project root.")

client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
db = client[DB_NAME]

users = db["users"]
closet_items = db["closet_items"]
trends = db["trends"]
verdicts = db["verdicts"]


def ensure_indexes():
    users.create_index([("email", ASCENDING)], unique=True)
    verdicts.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
    ensure_trend_indexes(trends)
