# Social embeddings and author graph

The scripts use PyMongo 4.20.0, google-genai 3.3.0, anthropic 2.8.0, and NetworkX 3.7. Context7 was unavailable in this session. Implementation was checked against the official [Gemini embedding documentation](https://ai.google.dev/gemini-api/docs/embeddings), [Atlas vector search stage](https://www.mongodb.com/docs/atlas/atlas-vector-search/vector-search-stage/), [PyMongo indexes](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/indexes/), and [NetworkX Louvain documentation](https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.community.louvain.louvain_communities.html).

## Commands

Run from the repository root with the virtual environment active:

```sh
python scripts/social_embed.py --dry-run
python scripts/social_embed.py --limit 1000 --workers 4 --create-index
python scripts/social_graph.py --name-limit 50
python -m unittest tests.test_social_embed tests.test_social_graph
```

`--limit` is a maximum number of posts attempted in that embedding run. Repeat the command to continue. Each successful embedding is stored immediately, with model and timestamp. A failed post remains eligible. Each text receives its own Gemini call, with 768 output dimensions and normalization. This model can merge multiple input texts into one vector, so sending a batch as one input is incorrect. Batches here bound the number of concurrent independent calls and writes.

Creating the Atlas index is optional and explicit. `--index-only` creates or updates it without embedding. The index is named `social_posts_vector`, with cosine on `embedding`, 768 dimensions, and filter fields `term` and `created_at`. A build waits up to five minutes. Scripts never delete another index to free capacity. Failed embeddings are retried four times with 2, 4, and 8 second waits. CLI error reports contain only the exception class, never connection strings or provider error payloads.

At the current 35,000-post cap, embedding requires at most 35,000 successful model calls plus retry attempts. Graph enrichment requires one Atlas neighbor query per embedded post, at most 35,000 queries. `--name-limit` explicitly caps additional Claude calls. The default is zero; use a positive budget for generated names. Dollar cost was not measured and depends on current account pricing and input token counts. No model calls or Atlas writes were made during the offline unit tests.

## Graph interpretation

Nodes are HMAC author hashes, including external authors named by normalized reply, mention, or quote metadata. Raw account handles and raw DIDs are not node attributes. Self-links are ignored. URI deduplication prevents repeated ingestion from duplicating real interaction edges.

There is one observed edge type, `interaction`, with `kind` equal to `reply`, `quote`, or `mention`. Weights are 1.0, 0.8, and 0.5 respectively. Distinct observed posts can contribute multiple edges between the same author pair. Reposts do not produce account edges because search counts do not identify reposting accounts. Follows are not used.

Semantic edges are marked `type: similarity`, `kind: similarity`, weight 0.2. For each embedded post, Atlas `$vectorSearch` returns eleven candidates so the source can be removed, leaving up to ten nearest other posts. This never calculates all pairwise similarities locally. Multiple similar post pairs collapse to one similarity edge per author pair, keeping the total inferred contribution for that pair at 0.2. Similarity indicates related text, not an observed interaction or an established community. The graph is undirected for clustering.

Louvain uses seed 42. Nodes and weighted endpoint pairs are sorted lexicographically by hash before the algorithm runs. A fixed seed alone does not ensure repeatability when insertion order changes. Community IDs are hashes of sorted membership, so unchanged communities reuse their names. An edgeless graph produces singleton communities instead of dividing by zero.

Both interaction-only and enriched graphs report the largest connected component share and isolated author count, using all graph authors as the denominator. Those statistics describe the full graph before export caps. Export selects largest communities first and highest-degree authors within them, capped at 1,500 nodes. A separate cap of 15,000 exported edges prioritizes observed interactions. Export statistics disclose both caps. An optional `--interaction-only` run explicitly records that similarity enrichment was disabled.

## Storage and names

`social_communities` stores `author_hash`, `community_id`, `community_size`, `name`, and `graph_run_id`, with a unique author index. It does not store sample text. Old mappings for authors no longer in a later corpus are retained; `graph_run_id` identifies the current run.

`social_graph_meta` stores successful Claude names under `_id: name:<community_id>`. One call uses top terms and at most five sanitized snippets, with no raw handles or author IDs. The prompt treats snippets as untrusted data. Cached names are reused. A missing budget, failed call, or unsafe response leaves a transparent term-based fallback that can be named on a later run. Successful generated names are cached once per membership fingerprint.

The capped export is stored as `{_id: "graph", graph: {...}}` in `social_graph_meta`, and written to ignored `.social_runs/social_graph.json`. No post snippets, post URIs, or text appear in the graph export.

## Verification and limits

Offline tests cover independent embedding calls, retry isolation, normalization, a zero request budget, invalid vectors, index filters, interaction weights, URI deduplication, similarity lookup and pair cap, empty graphs, insertion-order repeatability, export caps, naming cache, and identifier sanitization.

Live processing must wait for at least 3,000 ingested posts. `--allow-small-corpus` is available only for explicit user approval below that threshold. Writing and testing the code does not establish live index readiness, model availability, embedding quality, community validity, or measured graph statistics. Those results must be reported from an actual run after the volume gate passes.

## Contract deviations

- Exported edges have an additional 15,000 cap to bound browser payload size. The contract explicitly permits an edge cap.
- Community naming is bounded with `--name-limit` and defaults to term-based fallback names until model naming is requested. Only successful Claude names are cached.
- Similarity multiplicity is collapsed by author pair to keep inferred weight at 0.2 rather than amplify authors with repeated near-duplicate posts.
- Additive metadata fields are `embedded_at`, `graph_run_id`, and export coverage statistics. No existing response fields were changed.
