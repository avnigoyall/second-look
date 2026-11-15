"""Offline Social Ask tests. Model, vector retrieval and Mongo are mocked."""
import unittest
from datetime import datetime, timezone
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.auth import current_user
from backend.routers import social as router
from backend.services import social_ask as ask

URI = "at://did:plc:example/app.bsky.feed.post/abc"
OTHER = "at://did:plc:other/app.bsky.feed.post/xyz"
SOURCE = {"uri": URI, "url": "https://bsky.app/profile/did:plc:example/post/abc",
          "snippet": "Barrel jeans get wider at the knee", "community_name": "Denim shapes"}


class GroundingTests(unittest.TestCase):
    def test_citations_are_whitelisted_and_names_sanitized(self):
        result = ask.grounded_answer({"answer": "@alice.bsky.social sees a wider knee. https://example.test/user/alice",
                                      "cited_uris": [URI]}, [SOURCE])
        self.assertEqual(result["status"], "ok")
        self.assertNotIn("alice", result["answer"])
        self.assertNotIn("example.test", result["answer"])
        self.assertIn(URI, result["answer"])
        self.assertEqual(result["sources"], [SOURCE])

    def test_unknown_citation_is_rejected(self):
        with self.assertRaises(ValueError):
            ask.grounded_answer({"answer": "A claim", "cited_uris": [OTHER]}, [SOURCE])

    def test_unknown_inline_citation_is_rejected(self):
        with self.assertRaises(ValueError):
            ask.grounded_answer({"answer": f"A claim {OTHER}", "cited_uris": [URI]}, [SOURCE])

    def test_missing_citation_is_rejected(self):
        for value in ([], None, "not a list"):
            with self.assertRaises(ValueError):
                ask.grounded_answer({"answer": "A claim", "cited_uris": value}, [SOURCE])

    def test_no_match_does_not_call_claude(self):
        with mock.patch.object(ask, "retrieve_sources", return_value=[]), mock.patch.object(ask, "call_claude") as model:
            self.assertEqual(ask.ask("denim")["status"], "no_match")
            model.assert_not_called()

    def test_error_does_not_expose_provider_text(self):
        with mock.patch.object(ask, "retrieve_sources", side_effect=RuntimeError("private configuration")):
            result = ask.ask("denim")
        self.assertEqual(result["status"], "unavailable")
        self.assertNotIn("private", result["answer"])

    def test_snippets_strip_links_mentions_and_cap(self):
        text = "@alice.bsky.social https://bsky.app/profile/alice.example/post/x " + "a" * 500
        result = ask.private_text(text)
        self.assertEqual(len(result), 160)
        self.assertNotIn("alice", result)
        self.assertNotIn("https", result)

    def test_bare_custom_domain_handle_is_removed(self):
        result = ask.private_text("alice.example likes denim, while @bob.example likes tees")
        self.assertNotIn("alice.example", result)
        self.assertNotIn("bob.example", result)

    def test_retrieval_gates_scores_and_attaches_community_names(self):
        collection = mock.Mock()
        collection.find.return_value = [{"author_hash": "hash1", "name": "Denim @alice.bsky.social"}]
        hits = [{"uri": URI, "author_hash": "hash1", "similarity": 0.7, "text": "hello @alice.example"},
                {"uri": URI, "author_hash": "hash1", "similarity": 0.6, "text": "duplicate"},
                {"uri": OTHER, "author_hash": "hash2", "similarity": 0.1, "text": "unrelated"}]
        with mock.patch.object(ask.social, "embed_query", return_value=[1.0] * 768), \
                mock.patch.object(ask.social, "vector_search_posts", return_value=hits) as retrieval, \
                mock.patch.object(ask, "database", return_value={"social_communities": collection}):
            sources = ask.retrieve_sources("question", term="barrel jeans", since="2026-01-01T00:00:00Z")
        self.assertEqual(len(sources), 1)
        self.assertNotIn("alice", sources[0]["snippet"])
        self.assertNotIn("alice", sources[0]["community_name"])
        self.assertEqual(retrieval.call_args.kwargs["term"], "barrel jeans")
        self.assertEqual(retrieval.call_args.kwargs["since"], "2026-01-01T00:00:00Z")


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(router.router)
        self.client = TestClient(self.app)

    def test_ask_requires_authentication(self):
        response = self.client.post("/trend-ask", json={"question": "What is fading?"})
        self.assertEqual(response.status_code, 401)

    def test_validated_request_passes_filters(self):
        self.app.dependency_overrides[current_user] = lambda: {"_id": "test"}
        with mock.patch.object(ask, "ask", return_value={"status": "ok", "answer": URI, "sources": [SOURCE]}) as fn:
            result = self.client.post("/trend-ask", json={"question": " denim? ", "term": "barrel jeans",
                         "since": "2026-01-01T00:00:00Z", "until": "2026-11-01T00:00:00Z"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(fn.call_args.args, ("denim?",))
        self.assertEqual(fn.call_args.kwargs["since"], datetime(2026, 1, 1, tzinfo=timezone.utc))

    def test_blank_reversed_or_naive_dates_rejected(self):
        self.app.dependency_overrides[current_user] = lambda: {"_id": "test"}
        for body in ({"question": "  "}, {"question": "x", "since": "2026-01-01"},
                     {"question": "x", "since": "2026-11-01T00:00:00Z", "until": "2026-01-01T00:00:00Z"}):
            self.assertEqual(self.client.post("/trend-ask", json=body).status_code, 422)

    def test_empty_graph_returns_clear_not_found(self):
        collection = mock.Mock()
        collection.find_one.return_value = None
        with mock.patch.object(ask, "database", return_value={"social_graph_meta": collection}):
            result = self.client.get("/social-graph")
        self.assertEqual(result.status_code, 404)

    def test_graph_read_is_public_and_uses_graph_document(self):
        graph = {"nodes": [], "edges": [], "communities": [], "stats": {}}
        collection = mock.Mock()
        collection.find_one.return_value = {"graph": graph}
        with mock.patch.object(ask, "database", return_value={"social_graph_meta": collection}):
            result = self.client.get("/social-graph")
        self.assertEqual(result.json(), graph)
        self.assertEqual(collection.find_one.call_args.args[0], {"_id": "graph"})

    def test_public_trends_sorted_and_failures_scrubbed(self):
        collection = mock.Mock()
        collection.find.return_value = [{"term": "z"}, {"term": "a"}]
        with mock.patch.object(ask, "database", return_value={"social_trends": collection}):
            result = self.client.get("/social-trends")
        self.assertEqual([r["term"] for r in result.json()["trends"]], ["a", "z"])
        with mock.patch.object(ask, "database", side_effect=RuntimeError("sensitive")):
            result = self.client.get("/social-trends")
        self.assertEqual(result.status_code, 503)
        self.assertNotIn("sensitive", result.text)


if __name__ == "__main__":
    unittest.main()
