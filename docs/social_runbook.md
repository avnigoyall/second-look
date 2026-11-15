# Social Trend Radar

Bluesky Radar adds sampled clothing chatter to purchase analysis. It preserves the existing verdict rules and adds no bank calls. Counts describe search index samples, not platform totals. The first post in the 12-month observation window is not an origin date.

## Setup

Run commands from the repository root on feat/social-radar. Keep the current local .env file private. The required configuration is MONGODB_URI, ANTHROPIC_API_KEY, CLAUDE_MODEL and the Vultr variables VULTR_API_KEY, VULTR_BASE_URL and VULTR_EMBED_MODEL (they may live in the git-ignored backend/.env). SOCIAL_EMBED_PROVIDER selects the Social embedder: vultr (default) or gemini, which needs GEMINI_API_KEY. The ingest script creates SOCIAL_HASH_SALT if it is missing. Preserve that salt across resumptions. STORE_TEXT defaults true. SOCIAL_RADAR_ENABLED defaults false until the pipeline is ready.

```sh
.venv/bin/python -m pip install -r backend/requirements.txt
.venv/bin/python scripts/social_ingest.py --probe-only
.venv/bin/python scripts/social_ingest.py --max-minutes 90
```

The probe has a cumulative 20-request budget. Read docs/social_probe.md before changing an endpoint or retrying a failed pagination mode. Ingest stores resumable state in ignored .social_runs/. It runs at one request per second, backs off on HTTP 429 and stops at 35000 unique posts. The first pass spreads requests across terms and weekly windows. Thread enrichment adds observed interactions and is excluded from frequency metrics. Non-press collection reserves 10000 slots for the ten press seeds, including during thread enrichment.

To recover missing first-page metadata and confirm old windows without adding posts:

```sh
.venv/bin/python scripts/social_ingest.py --audit-first-pages --confirm-only --new-post-budget 0 --skip-threads --max-minutes 40
```

Confirmation is permitted at the cap. New unseen posts cannot be silently skipped: those windows keep their continuation position. Timestamp overlap includes the oldest post plus one microsecond and uses URI deduplication. An empty or safely deduplicated confirmation can finish a window. A short page alone cannot. Repeated full duplicate pages remain unresolved.

When the exact press rows are supplied, preserve their press_status and sources in the terms file. First collect one page per press term/window with --press-only --first-pages-only --skip-threads. Then deepen saturated windows for those exact terms and quarter zip with repeatable --term arguments, --deepen-saturated --reserve-posts 0 --skip-threads. The shared cap remains 35000. Missing seeds are not invented.

Thread enrichment fetches the top 200 searched posts by reply count within a cumulative 400-request budget. At most five new posts per thread are stored, reserving space for all 200 threads under the corpus cap. The quality report counts truncated threads and skipped posts. To resume only enrichment after a safely stopped search, run:

```sh
.venv/bin/python scripts/social_ingest.py --threads-only --max-minutes 15
```

Read docs/social_quality.md after collection. If the corpus has fewer than 3000 posts, wait for the user's response before running the graph command. Do not use --allow-small-corpus without explicit approval.

For data collected by an earlier variable-precision timestamp build, normalize once after ingest has stopped, before date-filtered retrieval:

```sh
.venv/bin/python scripts/social_ingest.py --normalize-times
```

New ingestion uses six fractional digits and UTC Z on every stored timestamp. The normalization command updates only social post timestamps and prints counts.

## Prepare retrieval and communities

```sh
.venv/bin/python scripts/social_embed.py --dry-run
.venv/bin/python scripts/social_embed.py --limit 35000 --workers 16 --request-size 8 --create-index
.venv/bin/python scripts/social_graph.py --name-limit 50
.venv/bin/python scripts/social_metrics.py --calibrate
.venv/bin/python scripts/eval_social.py --prepare-only
.venv/bin/python scripts/eval_social.py
```

With Vultr, each request carries a list of posts (--request-size) and --workers requests run in parallel. The model is qwen3-embedding-4b with dimensions=1024; native output is 2560. Vectors go to embedding_vultr with embedding_model and dims, and the index is social_posts_vultr_vector. Query embedding, retrieval, graph neighbors and evaluation all use the same provider, so models are never mixed. Transient errors back off exponentially and honor Retry-After. HTTP 401, 402 and 403 stop the job. A rejected request (400, 413 or 422) is split into single posts so one bad text does not stop the corpus. Writes are resumable: repeating the command skips posts that already have a vector for the current provider, model and dims. Vultr vectors are stored as packed float32 binary (about 4 KB each) to fit the Atlas free tier; `--pack-existing` converts any legacy array vectors without model calls. The index filters term and created_at. Old Gemini social vectors were removed to save space, so the gemini provider needs a fresh embedding run. Every Vultr call is charged first to the spending ledger in .social_runs/vultr_usage.json and blocked past VULTR_BUDGET_USD (default 100). Index creation can fail if Atlas has no search index capacity; the script never deletes another index.

Graph edges distinguish observed interactions from inferred semantic similarity. Communities are estimates, not Bluesky-provided groups. The export contains at most 1500 authors and 15000 edges. Community naming is cached and bounded by --name-limit. Read docs/social_graph.md for graph interpretation.

If embedding credentials are unavailable, an explicit interaction-only graph can still be inspected:

```sh
.venv/bin/python scripts/social_graph.py --interaction-only --name-limit 50
```

This fallback leaves similarity comparison statistics null and labels the graph as observed interactions only. Generated and fallback community-name counts are separate. It does not complete the semantic graph or retrieval evaluation. The query preparation command can generate the 30 held-out Claude paraphrases without embeddings, but preparation is not a retrieval benchmark.

Metrics keep empty and incomplete weeks distinct. Missing or truncated query coverage suppresses confident momentum and decay. Evaluation labels tune numeric thresholds only when enough annotated terms are eligible. They are never classifier inputs. Evaluation writes docs/eval_social.md and ignored local JSON artifacts. The report marks missing measurements explicitly.

Saturated first pages are lower-bound counts. Saturation in more than 25 percent of all observation weeks caps confidence at medium, including partial boundary weeks in the denominator. Missing saturation metadata also caps confidence until audited. A coverage note explains these limits. --preview-momentum computes a recent eight-week sample comparison without database or configuration writes; it does not relax the published momentum policy.

## Run the backend

```sh
SOCIAL_RADAR_ENABLED=1 NESSIE_API_KEY='' SECONDLOOK_WARMUP=0 .venv/bin/uvicorn backend.main:app --reload --port 8000
```

This command keeps Nessie disabled and avoids startup model warmup calls. The optional /analyze social context has a ten-minute cache and a short lookup deadline. A failure or no match returns null and leaves the verdict behavior unchanged. Confident peaking or fading can support an already allowed micro_trend; it cannot create eligibility. Fast fashion remains a store-rating label.

GET /social-trends and GET /social-graph return aggregate sample data. POST /trend-ask requires a normal authentication bearer token and accepts question plus optional term, since and until. Date filters require an explicit UTC offset. Answers cite retrieved post URIs and omit account handles. Sources have snippets of at most 160 characters.

If dashboard/trends.html exists, /trends-ui shows the community graph and term curve. The extension card adds one estimated Bluesky chatter line, with a link only when that page exists. No new action buttons are added. dashboard/index.html is unchanged.

## Explore the graph

The Style threads explorer at /graph-ui shows every style, community and author as one interactive graph. Build its data after the graph:

```sh
.venv/bin/python scripts/social_graph.py --name-limit 0 --edges-out .social_runs/social_graph_edges.json
.venv/bin/python scripts/social_atlas.py
```

The first command reuses cached community names and writes every edge, including the inferred similarity edges. The second writes .social_runs/social_atlas.json (about 3.3 MB, served by GET /social-atlas) and per-author details (served one author at a time by GET /social-atlas/author/{hash prefix}). Neither command calls Vultr or Claude. Rebuild both after any new ingest, graph or metrics run, or newly collected authors will be missing communities.

The page has two views. "Styles and communities" settles a ForceAtlas2 layout of the 42 styles and 502 communities, colored by garment family. "Every author" places all authors in discs around their community and shows observed replies, quotes and mentions; similar-post edges (215,853) can be switched on. Hover a node to light its connections. Click a style, community or author to open its details: post counts, first, fifth and last post, weekly share of sampled posts, top communities or styles, and sanitized post snippets that link to Bluesky. Search accepts style names, community names and author hash prefixes. Press / to search, Escape to clear, F to fit the graph. The libraries (sigma.js 4.0.6, graphology 0.26.0, graphology-library 0.8.0) are copied into dashboard/vendor, so the page works offline.

## Verify offline

```sh
MONGODB_URI=mongodb://127.0.0.1:27017 NESSIE_API_KEY='' SECONDLOOK_WARMUP=0 SOCIAL_RADAR_ENABLED=0 .venv/bin/python -m unittest discover tests
node extension/tests/price.test.js
PW_PATH=/path/to/playwright node extension/tests/card.harness.cjs
PW_PATH=/path/to/playwright node extension/tests/social.harness.cjs
```

The local Mongo URI prevents Atlas DNS resolution during test imports. Test cases replace database operations and provider requests with fakes. Playwright uses the real card code with a mocked service worker. Set PW_PATH to an installed Playwright package if it is not on Node's module path.

## Text retention

Set STORE_TEXT=false before ingest to omit newly collected text. This prevents embedding new posts without another text source. To inspect a potential wipe, run:

```sh
.venv/bin/python scripts/social_wipe_text.py
```

Only after an explicit decision to erase stored text, add --apply. A wipe leaves embeddings in place, but snippets will be empty. It does not delete post metadata or stored model outputs. Never print .env values or copy them into a report.
