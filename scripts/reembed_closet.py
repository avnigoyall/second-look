"""Recompute the embeddings of every closet item for one user, with the current Part 2 attribute_string.

Run from the project root:
    python scripts/reembed_closet.py                       # demo user
    python scripts/reembed_closet.py --email someone@x.y   # another user
    python scripts/reembed_closet.py --dry-run             # print the texts, call nothing, write nothing

For each item this recomputes:
- `embedding`: embed_text(embedding_text(attributes, description, name)), the same path new_closet_doc() uses,
  so a re-embedded item matches exactly like a newly added one.
- `image_embedding`: embed_image(photo) when the item has `image_data`, otherwise null.

The attribute_string is the embedding input. It is printed, not stored, because closet documents have no
field for it. Stored attributes are not re-extracted. Every vector is computed before anything is written,
so a failed embedding call leaves the closet unchanged. Safe to run again.
"""
import argparse
import base64
import math
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo import UpdateOne  # noqa: E402

from backend.db import closet_items, users  # noqa: E402
from backend.services.closet import embedding_text  # noqa: E402
from backend.services.embeddings import PARALLEL_CALLS, EmbeddingError, embed_image, embed_many  # noqa: E402

DEMO_EMAIL = "demo@secondlook.test"


def cosine(a: list[float] | None, b: list[float] | None) -> str:
    if not a or not b or len(a) != len(b):
        return "-"
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return f"{dot / norm:.4f}" if norm else "-"


def image_vector(image_data: str | None) -> list[float] | None:
    if not image_data:
        return None
    return embed_image(base64.b64decode(image_data.split(",", 1)[-1]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--email", default=DEMO_EMAIL, help=f"user whose closet to re-embed (default {DEMO_EMAIL})")
    parser.add_argument("--dry-run", action="store_true", help="print the texts only")
    args = parser.parse_args()

    user = users.find_one({"email": args.email}, {"_id": 1})
    if user is None:
        print(f"No user with email {args.email}")
        return 1
    docs = list(closet_items.find({"user_id": user["_id"]},
                                  {"name": 1, "attributes": 1, "description": 1, "image_data": 1,
                                   "embedding": 1, "image_embedding": 1}))
    print(f"{args.email}: {len(docs)} closet items")
    if not docs:
        return 0

    texts = [embedding_text(d.get("attributes") or {}, d.get("description"), d.get("name")) for d in docs]
    for d, text in zip(docs, texts):
        print(f"  {d['_id']}  {d.get('name')!r}\n      {text or '(empty, embedding will be null)'}")
    if args.dry_run:
        print("Dry run: nothing embedded or written.")
        return 0

    to_embed = [i for i, t in enumerate(texts) if t]
    try:
        text_vectors = dict(zip(to_embed, embed_many([texts[i] for i in to_embed])))
        with ThreadPoolExecutor(max_workers=PARALLEL_CALLS) as pool:
            image_vectors = list(pool.map(image_vector, [d.get("image_data") for d in docs]))
    except EmbeddingError as e:
        print(f"Embedding failed, nothing was written: {e}")
        return 1

    ops = []
    print("\nCosine of old vs new vector (text, image). '-' means one side is missing.")
    for i, d in enumerate(docs):
        new_text, new_image = text_vectors.get(i), image_vectors[i]
        print(f"  {d.get('name')!r}: text {cosine(d.get('embedding'), new_text)}, "
              f"image {cosine(d.get('image_embedding'), new_image)}")
        ops.append(UpdateOne({"_id": d["_id"]}, {"$set": {"embedding": new_text, "image_embedding": new_image}}))
    result = closet_items.bulk_write(ops, ordered=False)
    print(f"\nUpdated {result.modified_count} of {len(docs)} items "
          f"({len(text_vectors)} text embeddings, {sum(v is not None for v in image_vectors)} image embeddings).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
