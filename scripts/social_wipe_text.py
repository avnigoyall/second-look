"""Blank stored Bluesky post text. Run with --apply to perform the requested wipe."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Blank all social_posts text; embeddings remain")
    args = parser.parse_args()
    from backend.db import db
    query = {"text": {"$exists": True, "$ne": ""}}
    count = db.social_posts.count_documents(query)
    if not args.apply:
        print(f"Would blank text on {count} posts. Use --apply to wipe.")
        return
    result = db.social_posts.update_many(query, {"$set": {"text": ""}})
    print(f"Blanked text on {result.modified_count} posts. Embeddings remain.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Text wipe failed: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
