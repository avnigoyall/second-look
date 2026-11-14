"""Create the Atlas Vector Search index "closet_vector" on closet_items, then wait until it is queryable.

Run from the project root:  python scripts/create_indexes.py
Safe to run again. The Atlas free tier allows 3 search indexes in total, so this lists the existing ones
across every collection first and creates only what is missing. An existing closet_vector with an older
definition is updated in place (Atlas keeps serving the old version until the new one is built), and the
script waits until the current definition is READY.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo.errors import OperationFailure  # noqa: E402
from pymongo.operations import SearchIndexModel  # noqa: E402

from backend.db import closet_items, db  # noqa: E402
from backend.services.closet import VECTOR_INDEX  # noqa: E402
from backend.services.embeddings import DIMENSIONS  # noqa: E402

FREE_TIER_LIMIT = 3
WAIT_S = 300
DEFINITION = {
    "fields": [
        {"type": "vector", "path": "embedding", "numDimensions": DIMENSIONS, "similarity": "cosine"},
        {"type": "filter", "path": "user_id"},
        {"type": "filter", "path": "attributes.category"},
        {"type": "filter", "path": "has_description"},  # find_matches() compares like with like
    ]
}


def existing_search_indexes() -> dict[str, list[dict]]:
    found = {}
    for name in db.list_collection_names():
        found[name] = list(db[name].list_search_indexes())
    return found


def main():
    if "closet_items" not in db.list_collection_names():
        db.create_collection("closet_items")  # a search index needs the collection to exist
    try:
        existing = existing_search_indexes()
    except OperationFailure as e:
        sys.exit(f"Search indexes are not supported on this server ({e.code}). MONGODB_URI must point to Atlas.")

    total = sum(len(v) for v in existing.values())
    print(f"existing search indexes: {total} of {FREE_TIER_LIMIT} allowed on the free tier")
    for coll, indexes in existing.items():
        for i in indexes:
            print(f"  {coll}.{i['name']}  type={i.get('type')}  status={i.get('status')}  queryable={i.get('queryable')}")

    mine = next((i for i in existing.get("closet_items", []) if i["name"] == VECTOR_INDEX), None)
    if mine is None:
        if total >= FREE_TIER_LIMIT:
            sys.exit(f"{VECTOR_INDEX} is missing, but the cluster already has {total} search indexes. "
                     "Delete one in Atlas first.")
        closet_items.create_search_index(SearchIndexModel(definition=DEFINITION, name=VECTOR_INDEX, type="vectorSearch"))
        print(f"created {VECTOR_INDEX}")
    elif mine.get("latestDefinition") != DEFINITION:
        closet_items.update_search_index(VECTOR_INDEX, DEFINITION)
        print(f"updated {VECTOR_INDEX}. Old definition:\n  {mine.get('latestDefinition')}")

    started = time.monotonic()
    while True:
        index = next((i for i in closet_items.list_search_indexes(VECTOR_INDEX)), None) or {}
        status, queryable = index.get("status"), index.get("queryable")
        current = index.get("latestDefinition") == DEFINITION
        # During an update the old version stays queryable, so wait for READY on the new definition too.
        if queryable and status == "READY" and current:
            print(f"{VECTOR_INDEX} is queryable with the current definition (status {status})")
            return
        if status == "FAILED":
            sys.exit(f"{VECTOR_INDEX} failed to build: {index}")
        if time.monotonic() - started > WAIT_S:
            sys.exit(f"{VECTOR_INDEX} is still not READY after {WAIT_S} s (status {status}). Run this again later.")
        print(f"  waiting... status={status} queryable={queryable} current_definition={current}", flush=True)
        time.sleep(5)


if __name__ == "__main__":
    main()
