"""Atlas export: shape, privacy and edge remapping, with no database or network."""
import json
import unittest
from unittest import mock

from fastapi import HTTPException

from backend.routers import social as router
from scripts import social_atlas as atlas

A, B, C = "a" * 64, "b" * 64, "c" * 64
URI = "at://did:plc:abc/app.bsky.feed.post/{}"


def post(i, author, terms, likes=0, text="Love my new barrel jeans with a cropped jacket", source=None, when="2026-03-02T10:00:00Z"):
    return {"uri": URI.format(i), "author_hash": author, "created_at": when, "term": terms[0], "terms": terms,
            "text": text, "like_count": likes, **({"source": source} if source else {})}


def trend(term, counts):
    return {"term": term, "weekly_counts": [{"week": f"2026-03-{2 + 7 * k:02d}", "count": n, "corpus_count": 10, "saturated": n >= 20}
                                            for k, n in enumerate(counts)],
            "breadth": 1, "concentration": 1.0, "momentum": "unknown", "confidence": "low"}


class AtlasTests(unittest.TestCase):
    def build(self):
        posts = [post(1, A, ["barrel jeans"], likes=9), post(2, A, ["barrel jeans", "sambas"], likes=1),
                 post(3, B, ["sambas"], text="Reply to @someone.bsky.social about sambas https://x.example/y"),
                 post(4, B, ["sambas"], source="thread")]
        communities = [{"author_hash": A, "community_id": "c1", "name": "Denim talk"},
                       {"author_hash": B, "community_id": "c1", "name": "Denim talk"},
                       {"author_hash": C, "community_id": "c2", "name": "sambas"}]
        trends = [trend("barrel jeans", [1, 1]), trend("sambas", [2, 0])]
        config = {"terms": [{"term": "barrel jeans", "label": "micro"}, {"term": "sambas", "label": "micro", "press_status": "fading"}]}
        edges = [[A, B, "reply"], [A, C, "similarity"], [B, "z" * 64, "mention"]]
        return atlas.build(posts, communities, trends, {"c1"}, config, edges, {"first_week": "2026-03-02", "last_week": "2026-03-09"})

    def test_counts_exclude_thread_posts_and_keep_multi_term_posts(self):
        result = self.build()
        styles = {s["term"]: s for s in result["styles"]}
        self.assertEqual(styles["barrel jeans"]["posts"], 2)
        self.assertEqual(styles["sambas"]["posts"], 2)  # post 2 and post 3; post 4 is thread enrichment
        self.assertEqual(styles["sambas"]["press_status"], "fading")
        self.assertEqual(result["style_style"], [[0, 1, 1]])

    def test_edges_are_remapped_to_indexes_and_unknown_authors_dropped(self):
        result = self.build()
        hashes = result["authors"]["hash"]
        self.assertEqual(len(result["author_edges"]["reply"]), 2)
        self.assertEqual(result["author_edges"]["mention"], [])
        self.assertEqual(result["meta"]["edges"], {"reply": 1, "quote": 0, "mention": 0, "similarity": 1})
        a, b = result["author_edges"]["reply"]
        self.assertEqual({hashes[a], hashes[b]}, {A[:10], B[:10]})
        self.assertEqual(result["communities"][0]["internal_edges"], {"reply": 1})
        self.assertEqual(result["community_links"][0][2], {"similarity": 1})

    def test_no_full_hashes_handles_or_links_leak(self):
        result = self.build()
        text = json.dumps(result)
        self.assertNotIn(A, text)
        self.assertNotIn("someone.bsky.social", text)
        self.assertNotIn("x.example", text)
        self.assertEqual(sorted(result["author_detail"]), sorted([A[:10], B[:10]]))
        examples = [e for s in result["styles"] for e in s["examples"]]
        self.assertTrue(all(e["url"].startswith("https://bsky.app/profile/did:") and len(e["snippet"]) <= 160 for e in examples))

    def test_claude_named_flag_and_author_without_posts(self):
        result = self.build()
        named = {c["name"]: c["named_by"] for c in result["communities"]}
        self.assertEqual(named, {"Denim talk": "claude", "sambas": "terms"})
        index = result["authors"]["hash"].index(C[:10])
        self.assertEqual(result["authors"]["posts"][index], 0)
        self.assertEqual(result["authors"]["style"][index], -1)


class AtlasEndpointTests(unittest.TestCase):
    def test_author_prefix_is_validated_before_file_access(self):
        for bad in ("../etc", "ABCDEF1234", "abc", "0123456789a"):
            with self.assertRaises(HTTPException) as caught:
                router.social_atlas_author(bad)
            self.assertEqual(caught.exception.status_code, 422)

    def test_author_detail_served_from_cached_file(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "social_atlas_authors.json"
            path.write_text(json.dumps({"0123456789": {"styles": [], "likes": 3, "posts": []}}))
            with mock.patch.object(router, "ATLAS_AUTHORS_PATH", path), \
                    mock.patch.dict(router._ATLAS_AUTHORS, {"mtime": None, "rows": {}}):
                self.assertEqual(router.social_atlas_author("0123456789")["likes"], 3)
                with self.assertRaises(HTTPException) as caught:
                    router.social_atlas_author("9999999999")
                self.assertEqual(caught.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
