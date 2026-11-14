"""Graph tests are deterministic and never contact Bluesky, Atlas, or models."""
import unittest
from unittest.mock import Mock

import networkx as nx

from scripts import social_graph as graph


def post(uri, author, **fields):
    return {"uri": uri, "author_hash": author, "term": "cargo pants", **fields}


class GraphTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
