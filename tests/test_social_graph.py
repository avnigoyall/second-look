"""Graph tests are deterministic and never contact Bluesky, Atlas, or models."""
import unittest
import time
from threading import Barrier, Lock
from unittest.mock import Mock, patch

import networkx as nx

from scripts import social_graph as graph


def post(uri, author, **fields):
    return {"uri": uri, "author_hash": author, "term": "cargo pants", **fields}


class GraphTests(unittest.TestCase):
    def test_neighbor_queries_are_bounded_concurrent_and_results_stable(self):
        posts = [post(str(i), "a" + str(i), embedding=[float(i)] * 768) for i in range(12)]
        base = graph.interaction_graph(posts)
        lock, barrier = Lock(), Barrier(3)
        active = peak = calls = 0
        def retrieve(pipeline):
            nonlocal active, peak, calls
            index = int(pipeline[0]["$vectorSearch"]["queryVector"][0])
            # Return a lazy iterator to check that cursor consumption stays in
            # worker threads instead of serializing network reads in the caller.
            def cursor():
                nonlocal active, peak, calls
                with lock:
                    active += 1; peak = max(peak, active); calls += 1
                if index < 3:
                    barrier.wait(timeout=2)
                time.sleep((3 - index % 3) * .001)
                try:
                    yield {"uri": str(index), "author_hash": "a" + str(index)}
                    yield {"uri": str((index + 1) % 12), "author_hash": "a" + str((index + 1) % 12)}
                finally:
                    with lock:
                        active -= 1
            return cursor()
        parallel, count = graph.add_similarity(base, posts, retrieve, workers=3)
        self.assertEqual(count, 12)
        self.assertEqual(calls, 12)
        self.assertEqual(peak, 3)
        def serial_retrieve(pipeline):
            index = int(pipeline[0]["$vectorSearch"]["queryVector"][0])
            return [{"uri": str((index + 1) % 12), "author_hash": "a" + str((index + 1) % 12)}]
        serial, serial_count = graph.add_similarity(base, list(reversed(posts)), serial_retrieve, workers=1)
        self.assertEqual(serial_count, 12)
        self.assertEqual(list(parallel.edges(data=True)), list(serial.edges(data=True)))
        self.assertEqual(graph.communities(parallel), graph.communities(serial))

    def test_failed_neighbor_query_aborts_without_mutating_source(self):
        posts = [post(str(i), "a" + str(i), embedding=[float(i)] * 768) for i in range(20)]
        base = graph.interaction_graph(posts)
        retrieve = Mock(side_effect=RuntimeError("safe fake failure"))
        with self.assertRaises(RuntimeError):
            graph.add_similarity(base, posts, retrieve, workers=3)
        self.assertLessEqual(retrieve.call_count, 3)
        self.assertEqual(base.number_of_edges(), 0)

    def test_interaction_only_export_does_not_invent_similarity_measurements(self):
        source = graph.interaction_graph([post("1", "a")])
        groups = graph.communities(source)
        names = {group["id"]: "Cargo discussion" for group in groups}
        result = graph.export_graph(source, groups, names, graph.graph_stats(source), similarity_evaluated=False)
        self.assertEqual(result["stats"]["largest_component_share_interaction"], 1)
        self.assertIsNone(result["stats"]["largest_component_share_with_similarity"])
        self.assertIsNone(result["stats"]["isolated_authors_with_similarity"])

    def test_default_live_graph_rejects_missing_embeddings_before_model_calls(self):
        import sys
        from types import ModuleType
        fake = ModuleType("backend.db")
        collection = Mock()
        collection.count_documents.return_value = 3000
        collection.find.return_value = [post("1", "a")]
        fake.db = {"social_posts": collection}
        with patch.dict(sys.modules, {"backend.db": fake}), patch.object(sys, "argv", ["social_graph.py"]), \
                patch("sys.stderr"):
            with self.assertRaises(SystemExit) as stopped:
                graph.main()
        self.assertEqual(stopped.exception.code, 2)

    def test_real_kinds_weights_external_authors_and_isolates(self):
        posts = [post("1", "a", reply_parent_author_hash="b", quote_author_hash="c",
                      mention_author_hashes=["d", "d", "a"]), post("2", "z")]
        result = graph.interaction_graph(posts + posts)
        self.assertEqual(set(result.nodes), {"a", "b", "c", "d", "z"})
        kinds = {data["kind"]: data["weight"] for _, _, data in result.edges(data=True)}
        self.assertEqual(kinds, {"reply": 1.0, "quote": 0.8, "mention": 0.5})
        self.assertTrue(all(data["type"] == "interaction" for _, _, data in result.edges(data=True)))
        self.assertEqual(graph.graph_stats(result)["isolated_authors"], 1)
        self.assertEqual(graph.graph_stats(result)["largest_component_share"], 0.8)

    def test_similarity_uses_vector_search_ten_neighbors_and_pair_weight_cap(self):
        posts = [post("1", "a", embedding=[0.1] * 768), post("2", "a", embedding=[0.1] * 768)]
        base = graph.interaction_graph(posts + [post("3", "z")])
        calls = []
        def retrieve(pipeline):
            calls.append(pipeline)
            return [{"uri": "1", "author_hash": "a"}] + [
                {"uri": str(i + 10), "author_hash": "z"} for i in range(20)]
        result, queries = graph.add_similarity(base, posts, retrieve)
        self.assertEqual(queries, 2)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result.number_of_edges(), 1)
        edge = list(result.edges(data=True))[0][2]
        self.assertEqual(edge, {"type": "similarity", "kind": "similarity", "weight": 0.2})
        for pipeline in calls:
            self.assertEqual(pipeline[0]["$vectorSearch"]["limit"], 11)
            self.assertEqual(pipeline[0]["$vectorSearch"]["index"], "social_posts_vector")
        self.assertEqual(graph.graph_stats(base)["isolated_authors"], 2)
        self.assertEqual(graph.graph_stats(result)["isolated_authors"], 0)

    def test_partition_repeatable_with_reordered_inputs(self):
        source = nx.karate_club_graph()
        first, second = nx.MultiGraph(), nx.MultiGraph()
        first.add_nodes_from(str(node) for node in source)
        second.add_nodes_from(str(node) for node in reversed(list(source)))
        edges = [(str(a), str(b), {"weight": data["weight"]}) for a, b, data in source.edges(data=True)]
        first.add_edges_from(edges)
        second.add_edges_from(reversed(edges))
        self.assertEqual(graph.communities(first), graph.communities(second))

    def test_empty_and_edgeless_communities(self):
        self.assertEqual(graph.communities(nx.MultiGraph()), [])
        empty = nx.MultiGraph()
        empty.add_nodes_from(["a", "b"])
        self.assertEqual(sorted(group["size"] for group in graph.communities(empty)), [1, 1])
        self.assertEqual(graph.graph_stats(nx.MultiGraph())["largest_component_share"], 0)

    def test_export_caps_nodes_and_edges_preserves_full_stats(self):
        source = nx.MultiGraph()
        source.add_nodes_from(f"h{i:04d}" for i in range(1600))
        for i in range(1599):
            source.add_edge(f"h{i:04d}", f"h{i+1:04d}", type="interaction", kind="reply", weight=1.0)
        groups = graph.communities(source)
        names = {group["id"]: "Cargo discussion" for group in groups}
        export = graph.export_graph(source, groups, names, graph.graph_stats(source), max_nodes=9999, max_edges=10)
        self.assertEqual(len(export["nodes"]), 1500)
        self.assertEqual(len(export["edges"]), 10)
        self.assertEqual(export["stats"]["authors"], 1600)
        selected = {node["id"] for node in export["nodes"]}
        self.assertTrue(all(e["source"] in selected and e["target"] in selected for e in export["edges"]))

    def test_name_cache_skips_model_and_snippets_remove_identifiers(self):
        posts = [post("1", "a", text="@person.bsky.social likes https://example.com did:plc:secret cargo")]
        groups = graph.communities(graph.interaction_graph(posts))
        summaries = graph.community_summaries(groups, posts)
        sent = summaries[groups[0]["id"]]["snippets"][0]
        self.assertNotIn("person", sent)
        self.assertNotIn("did:", sent)
        self.assertNotIn("https:", sent)
        cache, name_one = Mock(), Mock(return_value="Cargo conversations")
        cache.find_one.return_value = None
        names, calls = graph.name_communities(groups, summaries, cache, name_one, 1)
        self.assertEqual(calls, 1)
        self.assertEqual(names[groups[0]["id"]], "Cargo conversations")
        cache.find_one.return_value = {"name": "Cargo conversations"}
        name_one.reset_mock()
        graph.name_communities(groups, summaries, cache, name_one, 1)
        name_one.assert_not_called()

    def test_unsafe_model_name_is_rejected(self):
        groups = [{"id": "g", "members": ["a"], "size": 1}]
        summaries = {"g": {"top_terms": ["cargo pants"], "snippets": []}}
        names, _ = graph.name_communities(groups, summaries, None, lambda _: "@somebody", 1)
        self.assertEqual(names["g"], "cargo pants")

    def test_bare_custom_domains_removed_from_community_summaries(self):
        posts = [post("1", "a", text="alice.example and BOB.EXAMPLE discuss cargo pants",
                      terms=["cargo pants", "alice.example"])]
        groups = graph.communities(graph.interaction_graph(posts))
        summaries = graph.community_summaries(groups, posts)
        summary = summaries[groups[0]["id"]]
        self.assertNotIn("alice.example", str(summary))
        self.assertNotIn("BOB.EXAMPLE", str(summary))
        self.assertIn("[account]", str(summary))

    def test_bare_custom_domain_model_names_are_not_cached(self):
        groups = [{"id": "g", "members": ["a"], "size": 1}]
        summaries = {"g": {"top_terms": ["cargo pants"], "snippets": ["alice.example likes cargo"]}}
        cache, name_one = Mock(), Mock(return_value="alice.example discussion")
        cache.find_one.return_value = None
        names, calls = graph.name_communities(groups, summaries, cache, name_one, 1)
        self.assertEqual(names["g"], "cargo pants")
        self.assertEqual(calls, 1)
        self.assertNotIn("alice.example", str(name_one.call_args))
        cache.update_one.assert_not_called()

    def test_unsafe_cached_domain_name_is_regenerated_or_falls_back(self):
        groups = [{"id": "g", "members": ["a"], "size": 1}]
        summaries = {"g": {"top_terms": ["cargo pants"], "snippets": []}}
        cache = Mock()
        cache.find_one.return_value = {"name": "alice.example fan club"}
        names, calls = graph.name_communities(groups, summaries, cache, None, 0)
        self.assertEqual(names["g"], "cargo pants")
        self.assertEqual(calls, 0)
        names, calls = graph.name_communities(groups, summaries, cache, lambda _: "Cargo discussion", 1)
        self.assertEqual(names["g"], "Cargo discussion")
        self.assertEqual(calls, 1)
        self.assertEqual(cache.update_one.call_args.args[1]["$set"]["name"], "Cargo discussion")

    def test_graph_export_rejects_identifier_names_as_defense_in_depth(self):
        source = graph.interaction_graph([post("1", "a")])
        groups = graph.communities(source)
        names = {groups[0]["id"]: "alice.example"}
        export = graph.export_graph(source, groups, names, graph.graph_stats(source))
        self.assertEqual(export["communities"][0]["name"], "Unclassified discussion")
        self.assertEqual(export["nodes"][0]["community_name"], "Unclassified discussion")

    def test_preloaded_cache_uses_one_read_and_generates_only_missing_safe_names(self):
        groups = [{"id": key, "members": [key], "size": 1} for key in ("a", "b", "c")]
        summaries = {key: {"top_terms": ["cargo pants"], "snippets": []} for key in ("a", "b", "c")}
        cache, name_one = Mock(), Mock(return_value="Cargo conversations")
        cache.find.return_value = [{"_id": "name:a", "name": "Cargo regulars", "source": "claude"},
                                   {"_id": "name:b", "name": "alice.example club", "source": "claude"}]
        loaded = graph.load_name_cache(cache, groups)
        names, calls = graph.name_communities(groups, summaries, cache, name_one, 1, cached_names=loaded)
        cache.find.assert_called_once()
        cache.find_one.assert_not_called()
        name_one.assert_called_once()
        self.assertEqual(calls, 1)
        self.assertEqual(names, {"a": "Cargo regulars", "b": "Cargo conversations", "c": "cargo pants"})
        self.assertEqual(graph.count_named_communities(groups, names, loaded), 2)

    def test_named_count_excludes_unsafe_stale_and_non_model_names(self):
        groups = [{"id": key} for key in ("safe", "unsafe", "fallback", "changed")]
        names = {"safe": "Cargo discussion", "unsafe": "cargo pants", "fallback": "Plain basics", "changed": "New label"}
        loaded = {"name:safe": {"name": "Cargo discussion", "source": "claude"},
                  "name:unsafe": {"name": "alice.example", "source": "claude"},
                  "name:fallback": {"name": "Plain basics", "source": "terms"},
                  "name:changed": {"name": "Old label", "source": "claude"},
                  "name:stale": {"name": "Past group", "source": "claude"}}
        self.assertEqual(graph.count_named_communities(groups, names, loaded), 1)

    def test_bulk_membership_is_chunked_deduped_and_contains_current_fields(self):
        authors = [f"hash{i:04}" for i in range(1001)]
        groups = [{"id": "g", "members": authors + [authors[0]], "size": 1001}]
        collection = Mock()
        count = graph.persist_membership(collection, groups, {"g": "Cargo discussion"}, "run-utc")
        self.assertEqual(count, 1001)
        collection.create_index.assert_called_once_with("author_hash", unique=True)
        self.assertEqual([len(call.args[0]) for call in collection.bulk_write.call_args_list], [500, 500, 1])
        operations = [operation for call in collection.bulk_write.call_args_list for operation in call.args[0]]
        self.assertEqual(len({operation._filter["author_hash"] for operation in operations}), 1001)
        for call in collection.bulk_write.call_args_list:
            self.assertFalse(call.kwargs["ordered"])
        for operation in operations:
            self.assertTrue(operation._upsert)
            self.assertEqual(operation._doc["$set"], {"community_id": "g", "community_size": 1001,
                                                   "name": "Cargo discussion", "graph_run_id": "run-utc"})
        collection.update_one.assert_not_called()

    def test_bulk_membership_rejects_conflicting_author_assignments(self):
        collection = Mock()
        groups = [{"id": "a", "members": ["same"], "size": 1},
                  {"id": "b", "members": ["same"], "size": 1}]
        with self.assertRaises(ValueError):
            graph.persist_membership(collection, groups, {"a": "Cargo", "b": "Basics"}, "run")
        collection.bulk_write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
