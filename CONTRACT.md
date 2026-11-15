# Second Look: CONTRACT.md

## Social Trend Radar shared contract [DECIDED]

### Vultr Social-only provider amendment (latest)

Social Radar defaults to SOCIAL_EMBED_PROVIDER=vultr, with gemini retained as an explicit alternative. backend/services/social_embeddings.py owns the Social-only provider abstraction and reads VULTR_API_KEY, VULTR_EMBED_MODEL and VULTR_BASE_URL from ignored backend/.env. No credential values may be logged or committed. Keep backend/services/embeddings.py and every closet path identical to main. Vultr base is https://api.vultrinference.com/v1, POST /embeddings, model qwen3-embedding-4b; GET /embeddings/models and live dimension/batch probes establish supported parameters. Choose a verified 768- or 1024-dimensional output if supported; otherwise record the real dimension and limitation.

Vultr vectors use embedding_vultr, embedding_model (actual model), and dims (actual dimensions). Preserve old Gemini vectors in embedding. A separate social_posts_vultr_vector Atlas index uses embedding_vultr and the selected dimensions, with term and created_at filters. Query embedding, retrieval, graph neighbors and evaluation must select the same provider/field/index/dimensions, never mix models. Gemini compatibility checks must inspect vector-field provenance rather than assume the shared embedding_model still denotes Gemini.

Runtime social_context has an overall deadline below 0.5 seconds and returns null on timeout or provider failure. Cache behavior remains ten minutes. Native Vultr batching uses a list of inputs only after a live probe verifies one result per input, order, dimensions and normalization. Batch and rate limits must be reported as verified, documented or unknown; absence of rate headers does not establish unlimited quota. Respect Retry-After and bounded exponential backoff for transient errors. Authentication failures stop promptly.

Main probes the service without exposing keys, records the live dimensions, tests throughput on 100 posts and estimates full-corpus duration before starting the resumable full job. This model/provider change is an explicit Contract deviation from the original Gemini-only Social plan; closet embeddings remain unchanged.

[BUILT] Measured Vultr results (Nov 14). The dimensions parameter is supported: 768 and 1024 both return unit-length vectors. The Radar uses 1024 dims (native output is 2560). A list input returns one vector per input, ordered by index and identical to single calls (cosine 1.0). The responses had no rate headers, so rate limits are unknown. Throughput: 18.1 posts/s on 100 posts (8 workers, 16 per request); 23 posts/s on 400 posts (16 workers, 8 per request); the full job averaged 16.3 posts/s (21,929 posts, 0 failed, about 12 percent retried requests). Vector size is 1024 x 4 bytes per post, about 4 KB, or about 140 MB raw for 35,000 posts. Index social_posts_vultr_vector is READY. Runtime latency on 20 product queries: Vultr query embedding p50 1288 ms and p95 2364 ms; Atlas top-30 p50 24 ms and p95 44 ms. A cold social_context therefore returns null at the 0.45 s deadline (lookup_timeout_seconds lowered from 0.9). The background lookup completes and fills the caches. Deviation: an in-process LRU of query vectors (2048 entries, keyed by provider, model, dims and text, failures never cached) outlives the ten-minute result cache, so a repeat item costs only Atlas retrieval.

Deviation: packed vector storage (user-approved Nov 14 after the Atlas free-tier quota was reached). embedding_vultr is stored as a BSON float32 binary vector (4,098 bytes at 1024 dims instead of about 13,239 bytes as a double array), which Atlas Vector Search indexes natively. Readers decode it through social_embeddings.unpack; pending and embedded filters use field existence plus embedding_model and dims, never array index paths. Measured dataSize fell from 435 MB to 156 MB (the free-tier quota counts dataSize plus indexSize). The 681 legacy Gemini social vectors and the social_posts_vector index were removed with user approval. SOCIAL_EMBED_PROVIDER=gemini remains supported but would need re-embedding.

Deviation: evaluation query embedding uses a 10 s timeout (runtime keeps 3 s for background cache fills and 0.45 s for the caller). With 3 s, one or two of 30 queries failed per run on Vultr latency alone. The report states real_only first, lists terms whose coverage or counts are too low, and adds a press agreement column for published momentum and the read-only preview. Measured on 30 held-out Claude paraphrases over three runs at 10 s: 30/30 successful, recall@5, recall@10 and MRR@30 all 1.0; lookup p50 varied from 228 to 826 ms and p95 from 1629 to 3131 ms, driven by Vultr. "balloon pants" was added on user request (label unknown, first pages only, 253 posts, no saturated windows).

Deviation: spending guard. Every Vultr request, including retries, first reserves an overestimated token count (2 characters per token plus 8 per input) in the git-ignored ledger .social_runs/vultr_usage.json under a cross-process file lock. Provider-reported usage replaces the reservation. A call is refused when the ledger would exceed VULTR_BUDGET_USD (default 100) divided by VULTR_PRICE_PER_MTOK (default 2.75, the highest Vultr text-model rate found; no embedding price is published). A budget stop is fatal for the embed job. A corrupt ledger blocks calls rather than resetting to zero. Calls made before the ledger existed were seeded as a conservative estimate.

### Latest window-audit amendment (supersedes earlier sampling limits)

The user's latest cap is 35000 unique posts, superseding the initial 25000 and the intervening request for 40000. Reserve remaining capacity for ten user-provided press_status seeds before resuming other collection. press_status and press sources/dates are evaluation metadata, never classifier inputs.

[BUILT] Press seeds received Nov 14 (no sources or dates supplied): rising = straight leg jeans, quarter zip, poplin shirt, slim sneakers, soft suiting; fading = balloon jeans, skinny jeans, dad sneakers, mini bag, high heels. New seed terms carry label unknown. First pages were collected for the nine new terms, then saturated windows were deepened until the 35000 cap. The user then chose to trim: the 7800 deepened high heels and skinny jeans posts were deleted (their first-page posts remain), and the 35 affected windows are marked partial and unresolved with stop_reason trimmed_to_first_page, so their counts are lower bounds. The user also chose twelve more specific evaluation terms (label unknown, no press_status), collected as first pages only: kitten heels, block heels, stiletto heels, platform heels, slingback heels, low rise jeans, mid rise jeans, high waisted jeans, bootcut jeans, flare jeans, chunky sneakers, micro bag. The user stated that a smaller corpus is acceptable for the demo.

Persist first_page_count and saturated (true when the first page returns 20) on each term/window. A short page alone never establishes exhaustion. Cap-exempt confirmation requests may run when no insertion quota remains; if they return unseen URIs, preserve the continuation position and mark the window unresolved rather than losing those posts or exceeding the cap. Timestamp pagination uses oldest createdAt plus one microsecond with URI overlap deduplication. Legacy equal_timestamp_boundary_risk windows must be revisited, not silently relabeled complete. Repeated full pages with no progress remain unresolved because tied timestamps can exceed the page size.

Metrics annotate saturated weeks as lower-bound counts. More than 25 percent saturated weeks caps confidence at medium. coverage_note discloses saturation and any incomplete coverage. Strict momentum coverage policy remains explicit; changes must be documented and tested. Report completed_windows and saturated_windows per term, plus counts of non-unknown social_trends momentum. After collecting the press seeds, deepen saturated windows for those seeds and quarter zip within the global 35000 cap.

Saturation uses all observation weekly windows, including partial boundary weeks. Missing first-page saturation metadata also caps confidence at medium until audited. The recent eight-week momentum preview is read-only, reports proposed sample labels separately, and does not change published momentum or verdict evidence. Raw counts may be lower bounds; normalized sample shares are estimates rather than mathematical lower bounds.

The embedding CLI normally fails fast on HTTP 403. An explicit transient-403-retries option permits at most two bounded retries only for the measured intermittent case; HTTP 401 remains immediate failure and persistent 403 stops the batch. Provider diagnostics contain only categorical or allowlisted values, never response text or keys. Evaluation query preparation caches 30 genuine Claude paraphrases and preserves successful queries across targeted retries. Exact array lengths are validated locally because Claude strict tool schemas do not support maxItems.

This additive feature uses only public Bluesky data from api.bsky.app. Counts are sampled search index counts, never platform totals. The observation window is 12 months; first_seen_in_window is not an origin date. No Nessie requests. No dashboard/index.html edits. Only the main agent edits this contract. Labels micro, staple, unknown are evaluation annotations and never classifier inputs.

### Collections and indexes

- social_posts: uri (unique), author_hash (HMAC-SHA256 DID using SOCIAL_HASH_SALT), created_at (UTC ISO string with Z), text (empty when STORE_TEXT=false), term (canonical phrase), reply_parent_uri (nullable), reply_parent_author_hash (nullable), mention_author_hashes (array), quote_uri (nullable), like_count, repost_count, ingested_at (UTC ISO string). Additive fields: embedding (768 floats), embedding_model, quote_author_hash, reply_count, terms (all matched canonical phrases). Do not persist raw DIDs or handles outside post URIs. Search dedupe is by uri; preserve multiple matched terms in terms.
- social_ingest_state: resumable per term/window cursor, completion and request counts, probe results. Local state files may be used instead, documented by ingest. Cap 35000 unique posts globally, one request per second, backoff on 429. Probe at most 20 requests; thread enrichment at most 400 requests for top 200 reply counts. Probe results gate optional endpoint use.
- social_posts_vector: Atlas vectorSearch index on embedding, 768 dimensions, cosine, filter fields term and created_at. Use $vectorSearch for neighbors, never all-pairs. Embedding model gemini-embedding-2; separate text input per embedding.
- social_communities: one document per author with author_hash (unique), community_id (string), community_size, name. Naming once per community is cached and uses only top terms and five sanitized snippets. Labels and raw handles are excluded. Metadata may live in social_graph_meta.
- social_trends: term (unique), weekly_counts (array of {week: UTC Monday date YYYY-MM-DD, count: integer, corpus_count: integer, normalized: count/corpus_count or 0}), first_seen_in_window (nullable UTC timestamp), peak_week (nullable week), decay_ratio (nullable number: last four complete weeks / maximum four-week normalized sum), breadth (communities with at least three posts), concentration (largest community post share, nullable), weeks_to_breadth_3 (nullable integer), authors (integer), post_count, confidence (low|medium|high; under 100 posts always low), momentum (rising|peaking|fading|steady|unknown), computed_at. Include empty weeks. Config thresholds live in config/social_momentum.json and must not consume evaluation labels at classification time.

### Runtime and new endpoints

- backend.services.social.social_context(item) returns null on no match or error; otherwise {term, similarity (cosine), first_seen_in_window, weekly_counts (last 26 weeks), peak_week, decay_ratio, breadth, concentration, momentum, confidence, examples: [{uri, url, snippet (at most 160 characters)}] (at most two), estimate: true}. Top 30 retrieval votes by term; minimum vote share 0.4 plus configured minimum cosine. Cache 600 seconds. Target p95 below one second, report measured results rather than asserting it.
- /analyze adds optional social_context only. The context may add trends_ui_url, set only when the local UI file exists. Existing allowed verdict set remains unchanged. Confident peaking or fading is supporting evidence for an already allowed micro_trend only. Fast fashion remains a store-rating label. No match or failure preserves behavior. SOCIAL_RADAR_ENABLED=1 explicitly activates runtime retrieval after pipeline preparation.
- POST /trend-ask accepts {question: nonempty string, term?: string, since?: UTC timestamp, until?: UTC timestamp}. Returns {answer: string, sources: [{uri, url, snippet (at most 160 characters), community_name: string|null}], status: ok|no_match|unavailable}. Claude answers cite only retrieved post URIs. Never expose handles in snippets or answer. Auth follows existing current_user dependency. Backend only.
- GET /social-trends returns {trends: social_trends[]}, excluding embeddings and internal Mongo identifiers. GET /social-graph returns {nodes: [{id: author_hash, community_id, community_name, degree}], edges: [{source, target, type: interaction|similarity, kind: reply|mention|quote|similarity, weight}], communities: [{id, name, size}], stats: {largest_component_share_interaction, largest_component_share_with_similarity, isolated_authors_interaction, isolated_authors_with_similarity}}. Export cap 1500 nodes; explicit edge cap may be added for browser performance. Interaction weights reply=1.0, quote=0.8, mention=0.5; similarity=0.2, ten nearest posts per post. Fixed Louvain seed.
- [BUILT] /graph-ui serves dashboard/graph.html, the Style threads explorer (sigma.js 4.0.6 and graphology copied into dashboard/vendor with their MIT licenses). GET /social-atlas returns the precomputed public aggregate atlas from scripts/social_atlas.py: styles, communities, style-community and community-community links, every author as a 10-character hash prefix with community, post count and degree, and every full-graph edge as author indexes by kind. GET /social-atlas/author/{10 hex characters} returns one author's top styles, likes and at most three sanitized snippets (160 characters, handles and links removed) with DID-based post URLs. Both are unauthenticated public aggregate sample data like GET /social-graph. Deviation (user decision, Nov 15): the two atlas files are committed under data/social_atlas/ so teammates can run the explorer from a clone; they contain public post snippets (sanitized, at most 160 characters), DID-based post URLs and 10-character author hash prefixes, and the repository is public. scripts/social_graph.py gains --edges-out to write the full edge list the atlas needs. Deviation: hash prefixes are 10 hex characters, so collisions are possible but unlikely at about 26,000 authors.
- Optional /trends-ui serves dashboard/trends.html only if implemented. Extension card adds one estimated Bluesky chatter line and no buttons. Only link the term when /trends-ui exists.

### Ownership and execution gates

- Agent A owns scripts/social_ingest.py, scripts/social_wipe_text.py, config/social_terms.json, docs/social_probe.md, docs/social_quality.md, tests/test_social_ingest.py, .env.example and the SOCIAL_HASH_SALT entry in ignored .env.
- Agent B owns scripts/social_embed.py, scripts/social_graph.py, tests/test_social_graph.py, tests/test_social_embed.py, docs/social_graph.md. Report required dependencies to main agent. Build code immediately; do not run graph processing until ingest confirms at least 3000 posts or the user explicitly approves lower volume.
- Agent C owns backend/services/social.py, config/social_momentum.json, backend/services/verdict.py, backend/models.py, scripts/social_metrics.py, tests/test_social.py, tests/test_social_verdict.py. Main agent owns extension card integration.
- Agent D owns backend/services/social_ask.py, backend/routers/social.py, scripts/eval_social.py, docs/eval_social.md, tests/test_social_ask.py. Main agent registers router in backend/main.py, installs dependencies and integrates other shared files.
- Main agent alone owns CONTRACT.md, backend/main.py, backend/db.py, backend/requirements.txt, extension card integration, integration docs and optional UI. Agents do not commit or edit other owners' files. Every agent reports Contract deviations and unverified results.
- Thread enrichment adds source=thread; these posts add graph edges but are excluded from search frequency metrics. Weekly counts may add complete and coverage diagnostics. Similarity author pairs are collapsed to one edge at weight 0.2 to avoid repeated-post amplification. Graph export additionally caps edges at 15000. Explicit naming budgets bound Claude calls; only successful names are cached.
- Per-week query_coverage is complete, partial, unvisited or unknown; corpus_query_coverage is complete or incomplete. Missing or incomplete full-week coverage keeps confidence low, momentum unknown and decay_ratio null. Live metrics use the exact ingest window. Context may add query_coverage_complete and coverage_note.
- Live cursor probes returned HTTP 403 after successful HTTP 200 first pages. Failed cursor requests must not be reused. A separately verified since/until keyset fallback is permitted and must be reported as a Contract deviation; otherwise collect first pages only and mark capped windows partial. Probe attempts remain capped at 20 cumulatively.
- Social timestamps use six fractional digits and UTC Z for chronological Atlas string filtering. Legacy social documents can be normalized with social_ingest.py --normalize-times after ingest stops. Resume requeues request-error windows while preserving their saved pagination position.
- POST /trend-ask requires existing user authentication; GET graph and term metrics are public aggregate sample data for the optional UI. Evaluation reports MRR@30, with provider failures counted as misses and excluded from successful latency quantiles.
- Explicit interaction-only graph runs set the unmeasured with-similarity statistics to null and similarity_enabled=false. They report named_community_count and fallback_community_count. A default graph run requires actual embeddings. Fallback labels are transparent term-based labels, not claimed as generated community names.
- Enrichment fetches stable top-200 searched-post candidates and stores at most five new posts per thread to fit the global cap. It records truncation, skipped posts and cumulative physical requests; --threads-only resumes enrichment without search. This sampling cap is a reported Contract deviation. Evaluation may prepare 30 cached Claude paraphrases independently of embeddings, with prepared and evaluated counts reported separately.
- Ingest stops after at most 90 minutes and reports volume and coverage. Below 3000 posts requires user response before graph processing. Evaluation measures 30 Claude-paraphrased held-out posts, recall@5, recall@10, MRR, cluster purity, staple/micro separation, lookup p50/p95. Missing data or credentials must produce explicit unmeasured results, never invented numbers.

Single source of truth for data shapes, endpoints and rules shared across parts.
Read this before touching anything another part also touches.
Reconciled against the code on main (Nov 14, after Parts 1 to 4 merged). Where this file and the code differ, the code wins and this file gets fixed.

## 0. Status tags and change rules

- `[BUILT]`: exists in the code on main and passed its part's tests.
- `[STUB]`: exists but returns fake data.
- `[DECIDED]`: agreed by Hiresh and Avni, no code yet.
- `[PROPOSED]`: default, not yet confirmed. Changeable.

Change rules
- Only the main agent edits this file. Sub-agents list "Contract deviations" in their report instead.
- Response shapes already used by the extension may only grow (add optional fields). Never rename or remove.
- No em dashes in code comments, UI strings, docs or commit messages.

## 1. Stack and hosting `[BUILT]`

- Extension: Chrome MV3, plain JS. Content scripts render a Shadow DOM card. The service worker makes all backend calls.
- Backend: FastAPI on Hiresh's laptop, port 8000. `host_permissions` and CORS allow the extension and localhost. Run from main with `uvicorn backend.main:app --reload --port 8000`.
- DB: MongoDB Atlas, database name `secondlook` (hardcoded in `backend/settings.py`, `DB_NAME`). Collections: `users`, `closet_items`, `trends`, `verdicts`, `ingest_jobs`. Closet vector search needs Atlas (`$vectorSearch`).
- Secrets: `.env` only (`MONGODB_URI`, `JWT_SECRET`, API keys). Never commit them or print them.
- Models: Gemini for attributes, text structuring and embeddings (`gemini-embedding-2`, 768 dims). Claude for the trend agent (Claude web search, tool type `web_search_20260318`), the Part 5 verdict call, and the final price tier (`/read-price`).
- Gemini 503s are frequent. Every Gemini call retries up to 4 attempts (2, 4, 8 s waits) and every caller has a fallback (section 8).
- Demo login: `demo@secondlook.test`. Auth is JWT (`/auth/signup`, `/auth/login`, `/auth/me`).
- Times: Mongo is opened with `tz_aware=True` (UTC), and every API datetime is serialized with a UTC offset ("Z") through `backend/timeutil.py`, so clients show local time.
- Timing: every response has `X-Server-Ms`; `/analyze` adds `Server-Timing` with stage times. The backend warms the Claude verdict and price tools, the Gemini clients and Mongo in the background at startup (`backend/warmup.py`, two small Haiku calls; `SECONDLOOK_WARMUP=0` turns it off).

## 2. Capture (Part 1) `[BUILT]`

The extension builds an `Item` and sends it to `/analyze`.

```json
Item { "name": "string", "image_url": "string|null", "price": 19.99, "store": "string|null", "url": "string|null" }
```
- `price` is dollars, `>= 0`, or null.
- The extension keeps price source (dom, jsonld, meta, Gemini via `/read-page`) in its own logs. It is not part of the API.
- `POST /read-page` takes `{url, text (max 50000, first 8000 used), image_url?}` and returns `{name?, price?, image_url?}`. Used when the DOM price tier fails.
- `POST /read-price` (final price tier, Claude) takes `{url (max 4000), name?: string (max 300), windows: [string]}` (1 to 24 windows, 2000 chars each, 8000 total, at least one not blank, else 422) and returns `{price: number|null, price_text: string|null}`. It never answers 5xx: any Claude failure gives nulls.
- Price tiers in order: JSON-LD, meta, DOM price, `/read-page` (Gemini, first 8000 chars), then `/read-price` (Claude) only when a name exists and price is still null. Price sources: `jsonld | meta | dom | gemini | claude`.
- Claude tier rules: the extension sends windows of the full visible text: the add-to-cart area (largest container under 1500 chars) plus 120 chars around every amount (`$`, `€`, `£`, or `USD`, `EUR`, `GBP`, `CAD` after), merged, capped at 20 windows and 6000 chars, never cutting an amount. Claude returns `price_text` through a strict forced tool. The backend accepts it only if it appears verbatim in the windows (not inside a longer number) and parses with the same rules as `price.js`. Timeouts: Claude 4 s (hard cutoff 4.2 s), extension wait 5 s, then the card asks for the price as before. Diagnose shows `tiers.claude`. When the name is known and only the price is missing, `/read-page` and `/read-price` run at the same time; Gemini's price wins, Claude's is used when Gemini finds none or has not answered within 4 s. Each check logs one `[SecondLook] timing ...` line.

Rules that must stay true
- Price is the displayed non-member price for the selected variant. H&M ProductGroup pages resolve the variant by URL code, sku or color, never the first variant.
- Zara, Uniqlo, H&M, Pacsun have no JSON-LD price in meta. Price comes from the DOM or `/read-page`.
- Add-to-cart detection is multilingual (Hollister can render Spanish) and ignores recommendation tiles unless the tile is the current product.
- Removal capture (`extension/src/content/removal.js`) fires only after any confirm dialog resolves. Items removed from the cart do not count toward money kept. There is no backend endpoint for removal.

## 3. Attributes (Part 2) `[BUILT]`

`POST /attributes`
```json
request  { "name": "string (min 1)", "image_url": "string|null", "description": "<=20000", "breadcrumb": "<=2000", "text": "<=50000 (first 4000 used)" }
response ItemAttributesResponse {
  "category", "color_family", "fabric", "cut", "style": "string|null (allowed values below)",
  "fabric_confidence": "high | medium | low | null",
  "description": "<= 25 words",
  "is_apparel": "bool|null",
  "status": "ok | attributes_failed",
  "attribute_string": "string, empty when attributes_failed",
  "target_box_2d": "[ymin, xmin, ymax, xmax] 0 to 1000 around the target item | null (optional, Fix A)",
  "other_garments_visible": "bool | null (optional, Fix A)",
  "category_check": "agreed | fixed_by_crop | fixed_by_text | forced_from_name | no_name_category | null (optional, Fix A)",
  "target_crop": "JPEG data URL of the target item, 600 px longest side | null (optional, Fix A)"
}
```
- Target item only `[BUILT]` (Fix A): the prompt labels only the named item and ignores other clothes in an outfit shot. Code maps name and breadcrumb to a category (`CATEGORY_KEYWORDS`, built to cover every category in attributes.json, last garment noun wins, so "denim jacket" is jacket). On disagreement: one retry on the crop of `target_box_2d` (text only when there is no box), then the name category is forced, image-only attributes are nulled and the conflict is logged. Behavior change: when forced, the category can differ from Gemini's and the description is null.
- Entry points for Part 5: `understand_target_item()` (fetch, label, crop) and `verdict_doc_fields()` returning `{attributes, target_crop}` for the verdict doc. `understand_item` is now an alias, so Buy anyway auto-add also gets the name check.
- Allowed values: `backend/config/attributes.json` (`GET /config/attributes`). Do not copy enums elsewhere.
  - category: tee, shirt, sweater, hoodie, jacket, coat, jeans, pants, shorts, skirt, dress, shoes, bag, accessory
  - color_family: black, white, gray, beige, brown, red, orange, yellow, green, blue, purple, pink, multi
  - fabric: cotton, denim, wool, polyester, nylon, linen, leather, blend, unknown
  - cut: cropped, fitted, regular, relaxed, oversized, wide, straight, slim
  - style: basic, streetwear, preppy, athleisure, y2k, minimalist, boho, formal, other
- Not built: `pattern` field, and `other` in category. A non-apparel item gets `is_apparel: false`. `[PROPOSED]` to add `pattern` only if Part 5 needs it.
- `Attributes` (stored model) accepts `fabric_confidence` as a 0 to 1 number or high/medium/low. Writers use the enum.
- `attribute_string(attrs)` in `backend/services/item_understanding.py` is the only implementation. Template: `{color_family} {cut} {fabric} {category}, {style} style. {description}`. Null, `unknown` and `other` values are skipped with their words. `closet.embedding_text()` wraps it and falls back to the item name when there are no attributes.
- Latency about 1.0 to 1.4 s per call.

## 4. Embeddings `[BUILT]`

- `embed_text(text)` and `embed_image(jpeg_bytes)` in `backend/services/embeddings.py`. 768 dims, normalized. `embed_many` runs one call per text (8 in parallel). Never put several texts into one call, which merges them into one vector.
- Closet text embeddings must come from `attribute_string`. After changing that code, run `python scripts/reembed_closet.py` (supports `--dry-run`, writes nothing if any call fails). It also sets `has_description`. Demo closet re-embedded Nov 14.

## 5. Closet (Part 3) `[BUILT]`

Stored document (`closet_items`)
```json
{
  "user_id": "ObjectId",
  "source": "snap | auto_add | ownership_answer | quick_add | ingest_photo | ingest_video | ingest_text",
  "status": "owned | incoming",   // a document with no status counts as owned
  "name": "string",
  "image_data": "JPEG data URL (600px longest side) | null",
  "attributes": { "category", "color_family", "fabric", "fabric_confidence", "cut", "style" } ,
  "description": "string",
  "embedding": "768 floats | null",
  "image_embedding": "768 floats | null (optional, a failure does not block the save)",
  "source_verdict_id": "set on auto_add only, unique sparse index (one auto-add per verdict)",
  "created_at": "datetime"
}
```
API returns `ClosetItem` without `embedding` or `image_embedding`. `GET /closet` omits `image_data` unless `include_images` is set.

Endpoints
| Method and path | Purpose |
| --- | --- |
| `POST /closet/ingest` | multipart, up to 15 images and 1 video, plus optional text. Returns `{job_id}`. |
| `GET /closet/ingest/{job_id}` | `status queued|running|done|failed|saved`, `progress`, candidate `items`, `warnings`, `error` |
| `POST /closet/ingest/{job_id}/save` | `{items: SaveItem[]}` (max 100). Returns `{saved, ids}`. A second save returns 409. |
| `POST /closet/parse-text` | `{text}` returns candidates, no save |
| `POST /closet` | quick add: `{name?, image_base64?, attributes?, source}` |
| `GET /closet?status=owned|incoming` | list |
| `POST /closet/ownership-options` | `{name, attributes}` returns `{show, options, staple_idea}`. `show` is true only while the closet has fewer than 10 items. |
| `POST /closet/ownership-answer` | `{attributes, name?}` adds an owned item |
| `GET /settings`, `PATCH /settings` | `{auto_add_purchases: bool}` default `true` |
| `GET /closet-ui` | review page (grid, editable dropdowns, Save all) |
| `DELETE /closet/{item_id}` | delete one of your own closet items (Avni, Nov 14). Returns `{ok}` |

Candidate (`CandidateItem`): `id, origin photo|video|text, name, attributes, description, confidence 0..1, low_confidence, image_index, frame, box_2d [ymin,xmin,ymax,xmax] 0..1000, brand_visible, seen_count, thumbnail, likely_duplicate_of {id, name, score}`. `likely_duplicate_of` exists on candidates only, it is not stored.

Ingest pipeline: ffmpeg frames at about 1 fps (cap about 40), Gemini batches of about 10 images at 1024 px, text parsing, Gemini merge-duplicates call, embedding comparison to the existing closet, Pillow crops.

Buy anyway: `POST /action` with `bought` triggers a background auto-add (`status incoming`, `source auto_add`) after the response, unless `settings.auto_add_purchases` is false. The `/action` response does not change.

Matching (`backend/services/closet.py`)
- `find_matches(user_id, attrs, k=5)` returns `[{score, id, name, attributes, status}]`, best first. Hand-added items (no description: quick add, ownership answer, iMessage "yes") store `has_description: false` and are scored against the shopped item's attributes-only text; items with a description use the full text. Two embeddings and two vector searches run in parallel and merge by score (no added latency, about 250 ms). Measured: identical attributes 1.0, one differing attribute 0.80 to 0.87, two differing 0.50 to 0.65. `score` is plain cosine. Atlas reports `(1 + cos) / 2`, converted by `atlas_to_cosine`. Text embedding only, filtered to the same user and category. It never returns image_data or embeddings.
- `is_look_match(match, attrs)` applies `config/thresholds.json` and the color check. Color is decided from `color_family` in code, because text embeddings barely separate colors (blue vs black jeans scored 0.7996).
- `config/thresholds.json`: `{"look_similarity": 0.80, "require_color_match": true, "duplicate_similarity": 0.93}`. `duplicate_similarity` is untuned (a re-photographed tee scored 0.8585).
- Atlas index `closet_vector`, filters `user_id` and `attributes.category`. Also filter `has_description` (added Nov 14). Create with `python scripts/create_indexes.py`. Free tier allows 3 search indexes.

## 6. Trends (Part 4) `[BUILT]`

`GET /trends` returns `{trends: TrendSummary[]}` (example images reduced to `example_image_count`). Prompt helper: `backend/trends.py: get_trends_for_prompt()`.

Stored document (`TrendDoc`, validated for both seed file and agent output)
```json
{
  "name": "string",
  "category": "string|null (listed values match best)",
  "features": { "cut": "...", "color": "...", "fabric": "..." },   // note: key is "color", not "color_family"
  "first_seen": "datetime|null",
  "lifespan_weeks_est": "number|null",
  "lifespan_bucket": "short|medium|long|null",   // short <= 16 weeks, medium <= 52, long above
  "lifespan_label": "string (required)",
  "lifespan_source_url": "must be one of sources[].url when any lifespan field is set, else null",
  "sources": [{ "title": "string", "url": "http(s) url" }],      // at least 1
  "example_images": ["url or data url"],
  "alternatives": [{ "type": "...", "why_it_lasts": "...", "where_to_look": "store kind, not a link", "url": "only if in sources",
                     "product_name": "string|null", "product_url": "http(s)|null", "price": "number >= 0|null",
                     "store": "string|null", "verified_at": "datetime|null", "link_kind": "product|search|null" }],   // product fields: Fix B, optional
  "origin": "seed | agent",
  "search_suggestions_html": "agent only. Leftover from the dropped Gemini grounding path, unused"
}
```
- Seed file: `seed/trends_seed.json` (loaded by `scripts/seed_trends.py`). Agent: `scripts/run_trend_agent.py` (Claude web search, then structuring). A real run waits for the seeds.
- Guards in code: no category means discard, sources older than 12 months dropped, lifespan nulled when sources disagree, dedupe on category plus feature signature (not name alone), existing names passed into the structuring call.
- Lifespan numbers are shown to users only with the cited source.
- No human review of agent trends. Guards are the safety net.
- Alternative links `[BUILT]` (Fix B): found with Claude web search at seed time (`seed_trends.py`, and step (c) of the trend agent), never at verdict time, from 14 basics retailers. A `product_url` must be a URL that appeared in the search results (checked in code; hand-written seed URLs are trusted but re-checked by `verify_alternative_links.py`). HTTP check: 200, same domain after redirects, no redirect to a home, search or error page. 403 and 429 keep the link with `verified_at` null. No surviving link means a store search URL with `link_kind: "search"` (stores whose search page answers: Gap, Old Navy, Banana Republic, L.L.Bean, Quince, Abercrombie, Target; not Uniqlo, which returns 403).
- `scripts/verify_alternative_links.py` re-checks every stored link, read-only by default. Run it before the demo.
- Part 5 maps trend alternatives to the verdict `Alternative` with `backend/alternatives.py: alternatives_for_trend(trend)`.

## 7. Verdicts (Part 5)

### Engine `[BUILT]` (Part 5, `backend/services/verdict.py`)
`POST /analyze` takes `{item: Item}` and calls `decide(user, item, save=True)`. The stub cycling is gone. `decide(user, item, save=False)` runs the same path read-only (used by `scripts/eval_verdicts.py`).
- `Item` gains optional `description` (max 20000) and `breadcrumb` (max 2000). The extension reads them from JSON-LD Product or BreadcrumbList, with og/meta description and breadcrumb nav as fallbacks (`extension/src/content/reader/details.js`).
- Attributes: Part 2 `understand_target_item()` in-process through `backend/services/verdict_deps.py` (the engine's only link to Fix A and Fix B). `target_crop` is stored on the verdict doc.
- Signals in code: `find_matches(user, attrs, k=5)` and the category-filtered `get_trends_for_prompt()` run in parallel. Best match = highest score among color-compatible matches. `t_c = clamp(user.look_thresholds[category] or look_similarity, 0.70, 0.92)`, `gray_margin` 0.04 in `config/thresholds.json`. Zones: `forced` at `t_c + margin` or above, `gray` within the margin, `none` below.
- Allowed set in code: have_this_look only in forced or gray (forced allows nothing else). micro_trend only when a filtered trend has `lifespan_bucket == "short"`. good_buy always.
- Claude call: `CLAUDE_MODEL` (`claude-haiku-5-5`), forced tool use with `strict: true`, `output_config.effort: "low"`, thinking disabled. No temperature: the API rejects it ("Extra inputs are not permitted"). Images (target crop, top 1 or 2 closet photos) only in the gray zone. The tool enum always lists all three verdicts (a fixed strict schema avoids a one-time schema compile of about 4 s), and the allowed set is enforced in code. Forced `tool_choice` returns 400 on Opus 5.5 and Sonnet 5.5, so a model change needs `auto`.
- Guardrails in code: a verdict outside the allowed set is downgraded in priority order with a template reason; matched ids not in the provided matches are dropped; have_this_look with no valid ids downgrades (in the forced zone the best match is filled in instead); micro_trend needs `matched_trend_name` in the list; reasons over 30 words become a template; a small closet with no matches must say "I don't see this in your closet". Wording rules (Nov 14): "I don't see this in your closet" only when the category has no candidate matches; a good_buy with candidates names the closest item and how it differs; have_this_look names the matched item and never hedges (may, might, maybe, possibly, perhaps, lookalike). Violations (`not_seen_with_match`, `hedged_have_this_look`, `matched_item_not_named`, `closest_item_not_named`) are replaced by a code template and logged in `overrides`. The Claude payload includes `closest_match` (name, attributes, same_color_family, differences), even below threshold. Every change is logged in `override {rule, code_said, claude_said}` (the first) and `overrides` (all).
- Timing: attempt 1 times out at 6 s, the retry at 4 s, then a code-only verdict (`decided_by: "fallback"`). Since Nov 14 the whole engine answers by about 10.5 s: product photo fetched once (3 s cap, then text only), attributes capped at 7 s, each Claude attempt cut to the time left, an attempt with under 1.5 s left is skipped. Trends and closet size start before attributes. `AnalyzeResponse` adds optional `attributes`, so the ownership question needs no extra `/attributes` call. Measured click to verdict: price on page median 3.5 s (worst 4.6 s); no price on page median 4.0 s (was 5.9 s). Measured: 3 to 4 s typical (attributes 0.8 to 1.5 s, matches and trends about 0.3 s, Claude 1.6 to 2.4 s), above the 3 s target. Worst case: attributes time plus 10 s. The extension times out `/analyze` at 12 s and shows "Second look unavailable".
- Code-only paths (never an error to the shopper): attributes failed, embedding or Atlas error, both Claude attempts failed. Not apparel: good_buy, reason "This isn't clothing, so there's nothing to compare it with."
- Wears: `config/wears.json` category averages, marked unverified. `expected_wears`, `cost_per_wear` and `wears_estimated: true` on good_buy only. The card labels them "Estimate".
- Alternatives on micro_trend only, from `alternatives_for_trend(matched_trend)`.
- The "no number without a cited source" reason rule is in the prompt only, not enforced in code.

Response (`AnalyzeResponse`, `[BUILT]`, the extension depends on it):
```json
{ "status": "ok", "verdict_id": "string",
  "verdict": "have_this_look | micro_trend | good_buy", "reason": "string",
  "matched_closet_item": "ClosetItem|null", "matched_trend": "Trend|null",
  "alternatives": [{ "name": "string", "url": "string (required, non-empty, Fix B)", "price": 48.0, "reason": "string|null",
                     "store": "string|null", "link_kind": "product | search (default product)" }],
  "outfits": [{ "name": "string", "closet_item_ids": [] }],
  "expected_wears": "int|null", "cost_per_wear": "float|null",
  "matched_closet_items": "up to 3 ClosetItem with image_data, best first (have_this_look only). matched_closet_item is the first",
  "decided_by": "code | claude | fallback", "closet_small": "bool (fewer than 10 closet items)",
  "zone": "forced | gray | none | null (null: matching did not run, say nothing about ownership)",
  "wears_estimated": "bool" }
```
Stored verdict doc (iMessage verdicts add `channel` and `ownership`, see section 14): `user_id, item, attributes, verdict, reason, matched_closet_item_id, matched_trend_id, alternatives, expected_wears, action none|skipped|bought, feedback none|up|down, created_at`, plus optional `target_crop` (Fix A, not returned by `/dashboard`). Part 5 also stores `signals` (matches, best, t_c, margin, zone, allowed, trends, closet_size, errors, path, claude_reply, timings), `decided_by`, `override`, `overrides`, `model`, `latency_ms`, `cost_per_wear`, `matched_closet_item_ids`. `attributes` is null when Part 2 failed.
Old verdict docs with a null alternative url get a store search link when `/dashboard` reads them.

### Card `[BUILT]` (Part 5)
- have_this_look: reason and up to 3 closet photos (placeholder icon when an item has no photo). micro_trend: reason, trend name, `lifespan_label` only with a source, source link, alternatives as links (Fix B design, `renderAlternatives` delegates to `alternativesBlock`). good_buy: reason and the wears line tagged "Estimate".
- Loading copy changes after 5 s. Any `/analyze` failure except auth shows "Second look unavailable" with Try again, and the store's click always goes through.
- Ownership question (Part 3) is now wired in the extension: shown when `closet_small` and `zone == "none"` (no zone: only when nothing matched). It uses `result.attributes` if present, else one background `/attributes` call. "None of these" saves nothing.

### Still `[PROPOSED]`
- Per-user `t_c` learning from feedback (Part 7 sets the step).
- Hard response-time cap below the current worst case.

## 8. Failure behavior (all parts) `[DECIDED]`

- The card never blocks the store. If the backend is down or slow, show "Second look unavailable" and let the click through.
- Gemini failure: retry with backoff, then degrade (no attributes means a code-only verdict).
- Claude failure: section 7 fallback.
- Atlas failure in `find_matches`: no match, and the card says nothing about closet ownership.

## 9. Actions, money, dashboard, Nessie (Part 6) `[BUILT]`

### `POST /action`
- Takes `{verdict_id, action: skipped|bought}`. Returns `{ok, balances: {checking_cents, savings_cents, placeholder}, transfer: {status: done|pending|failed|no_price, amount_cents} | null}` (transfer is additive, Part 6).
- The action is saved first (existing behavior). `bought` is unchanged: no Nessie call, placeholder balances, Buy anyway auto-add (Part 3). The store cart is never touched.
- `skipped` by a Nessie user: claims `verdict.transfer` atomically (a double click never moves money twice), then moves `round(price * 100)` cents from Checking to Savings with description `Second Look skip: <item name> [<verdict_id>]` (name capped at 60 chars, brackets and control characters removed; Nessie allows 200), all inside a 3 s budget. The duplicate check matches on `[<verdict_id>]` and still recognizes the old format `Second Look skip <verdict_id>`. `done` means Nessie accepted the transfer (Nessie settles it about a minute later).
- Timeout or error: `pending`, then one background retry that first searches Checking for the exact description, so a transfer that landed is never repeated. Second failure: `failed`; a later Skip click retries with the same check. Never raises to the shopper.
- Checking below the amount: deposit the shortfall plus 150000 cents (`Second Look top-up <verdict_id>`, never repeated), answer `pending`, and transfer in the background once the deposit settles (up to 120 s). Measured: done after about 50 s.
- No price: `transfer.status: "no_price"`, no Nessie call.
- Balances are real (`placeholder: false`) only for skipped by Nessie users, as projected cents (`available_cents` plus pending incoming credits), so Savings moves right away. Otherwise null with `placeholder: true` (local users, bought, Nessie unconfigured, down or out of time).
- Measured live (demo user, $49.90 skip): 1.3 s from click to "Skipped. $49.90 moved to savings". Checking 150000 to 145010, Savings 0 to 4990.

### Who uses Nessie
- Only users with `nessie_enabled: true`, set by `python scripts/setup_nessie_demo.py [--email ...]` (idempotent). Everyone else, including the throwaway E2E users, makes zero Nessie calls and uses the local path.
- User doc fields: `nessie_enabled`, `nessie_customer_id`, `nessie_checking_id`, `nessie_savings_id`, transient `nessie_lock` (race-safe setup: Mongo claim plus process lock; existing accounts found by nickname so a crashed setup resumes). Accounts: "Second Look Checking" 150000 cents, "Second Look Savings" 0.
- Provisioning never runs on the click: an enabled user without accounts gets `pending` and setup runs in the background.
- Verdict doc: `transfer {status, id (txn id|null), amount_cents, at}`, optional `topup_id`, `error` (key scrubbed).
- Demo user is set up: customer, Checking and Savings exist (Nov 14).

### `GET /savings`
- Returns `{checking_cents: int|null, savings_cents: int, transfers: [{amount_cents, description, at}], source: "nessie" | "local"}`.
- `nessie`: transfers into Savings, newest first, at most 20. `local` (not enabled, or Nessie failed): `checking_cents` null, `savings_cents = round(money_kept * 100)`, transfers built from skipped priced verdicts.

### Nessie v2 API as observed (`scripts/nessie_smoke.py`, Nov 14)
- Base `https://v2-prod-api.nessieisreal.com`, `Authorization: Bearer NESSIE_API_KEY`. 3 s timeout per call, 10 s for setup. The key is only ever sent in that header; never logged, stored, returned or sent to the extension.
- All amounts are integer cents.
- `POST /customers` `{first_name, last_name, address: {street_number, street_name, city, state (2 letters), zip (5 digits)}}` returns `{id: "cust_...", ...}`.
- `POST /customers/{id}/accounts` `{type: "Checking" | "Savings", nickname (max 40), balance_cents}`. Account: `{id: "acct_...", customer_id, type, nickname, balance_cents, pending_debits_cents, available_cents, account_number, rewards, created_at, updated_at}`.
- `POST /accounts/{id}/deposits` `{amount_cents, description?, medium?}`. `POST /accounts/{id}/transfers` `{payee_id, amount_cents, description?}`.
- Transaction: `{id: "txn_...", kind, status: "pending", medium, amount_cents, description, payer_id, payee_id (transfers), settle_at, created_at, updated_at}`. `GET /accounts/{id}/transactions?limit=N` returns `{items: [...]}`, newest first, no cursor.
- Everything starts `pending`. `POST /transactions/{id}/settle` moves `settle_at` to now; the hosted sweep moves money 30 to 60 s later. A pending deposit cannot be spent yet (transfer gets 422 `insufficient_funds`). Errors are problem+json. No idempotency key, so a timed-out create may have landed.

### Dashboard
- `GET /dashboard` returns `{money_kept, waste_avoided, verdicts (last 50), cost_per_wear}`. `money_kept` is still the local sum of `item.price` over verdicts with `action == skipped` (now in `backend/services/money.py`). `waste_avoided` is estimated kg CO2e avoided by skipped items (Avni, `backend/impact.py`), null when no skipped item has a sourced category figure. `cost_per_wear` is null for now.
- `GET /impact` (JWT, Avni) returns `{money_kept, items_skipped, co2e_kg, co2e_items_counted, co2e_sources: [{category, kg_co2e, source, url}]}`. Figures come only from `backend/config/impact.json`, which holds sourced per-category numbers (for example jeans 33.4 kg, Levi's 501 lifecycle assessment). Categories without a sourced figure are not counted.
- `GET /cart-summary` is a placeholder (all zero, `placeholder: true`). `GET /dashboard-ui` serves the page.
- Only Skip it counts toward money kept. Known gaps: money_kept includes skips from before Part 6 (no transfer), so it can differ from Nessie Savings; Skip then Buy anyway does not reverse the transfer; the dashboard page does not show Nessie balances yet; (times fixed Nov 14: API datetimes now carry "Z"). Demo money_kept matches Nessie Savings after the 2 pre-Nessie test skips were deleted on Nov 14.

### Card after an action (Part 6)
- Buttons follow the verdict (Avni's UI refresh): good_buy highlights "Buy it" first and focuses it, with "Skip it anyway" second; have_this_look and micro_trend keep "Skip it" first, then "Buy anyway". Both skip buttons send `skipped`, both buy buttons send `bought`.
- Hold add-to-cart until Buy (Avni, merged Nov 14): the store's add-to-cart click is held while the card is open. Buy releases it to the store (`bag: "added"`, or `"missing"` when the store re-rendered its button, with a "press the add button again" notice); Skip drops it (`bag: "not_added"`). The card closes at once on either button and the page blur goes away. The action is still sent once in the background. A failed check always lets the click through.
- After Skip the result shows in the notice: "Skipped. $X moved to savings", "Skipped. Savings update pending" or "Skipped. No price, so nothing moved", plus the savings goal line. Buy shows no savings notice.
- Full analysis (Avni): `POST /analyze/details` returns `AnalysisDetails` (materials and material health from `backend/config/material_health.json` and `backend/services/composition.py`, lifespan, cost per wear, outfits; `backend/services/analysis_details.py`). The card loads it after the verdict ("Loading full analysis...") and adds a block above the buttons; failures remove the block quietly.
- Theme (Avni, closet redesign): brown and beige editorial theme with serif headings on the card, popup and closet pages; new hanger icons.
- Popup (Avni's UI refresh): hanger logo, dark purple theme. On a product page it shows the check and Diagnose tools; elsewhere it shows the savings summary from `GET /impact`, with "Check this page anyway" to reveal the tools. Signup takes an optional phone.
- Skip it with `transfer.status == "done"` and an amount: "Skipped. $X moved to savings" with a check. `no_price`: "Skipped. No price, so nothing moved". Anything else (pending, failed, no transfer field, timeout): "Skipped. Savings update pending". On a timeout the buttons stay disabled, since the skip may have landed. Buy anyway: unchanged ("Marked as bought").

## 10. Feedback (Part 7) `[PROPOSED]`

- `POST /feedback` takes `{verdict_id, value: up|down}` and stores it on the verdict `[BUILT]`. It does not yet change any threshold.
- Part 7 adds: per-user, per-category `t_c`, step size and bounds (section 7).

## 11. Testing and isolation

- `DB_NAME` is hardcoded to `secondlook`, so agents isolate by test user: sign up `test_<part>@secondlook.test` and use that account. Never write test data to the demo user. `[PROPOSED]` small change: read `DB_NAME` from env, so a sub-agent can use its own database.
- Last known results after merging Social Radar and Avni's full analysis and closet redesign (Nov 14): unittest 440/440, card harness 97/97, popup harness all pass, social harness 39/39, goal harness 70/70, iMessage Node tests 7/7. Extension E2E not run: both Gemini keys were unusable (current key disabled, previous key over its daily quota).
- Earlier: after the Photon auto-assignment merge: unittest 222/222, card harness 73/73, popup harness all pass, iMessage Node tests 7/7, extension E2E 66/66 with 2 SKIP.
- Earlier: after the Nov 14 fixes merge (polish, quick-add, wording, speed, account chat): unittest 199/199, card harness 73/73, iMessage routing 5/5, extension E2E 66/66 with 2 SKIP.
- Earlier: after merging Avni's popup refresh and phone signup: unittest 147/147, card harness 73/73, extension E2E 66/66 with 2 SKIP.
- Earlier: after the Claude price tier merge: unittest 147/147, price checks 23 plus 18 window checks, extension E2E 65/65 with 2 SKIP.
- Earlier: results on the Part 5 merge (A, B, engine, card, eval): unittest 114/114, price parser 23 checks, extension E2E 61/61 after the Part 6 merge (60/60 twice after the Photon merge), unittest 133/133, card harness 65/65, with 2 SKIP (alternative link checks need a short-lifespan seed trend so a real micro_trend can occur).
- Test users in the shared DB: `test_altlinks@`, `test_target@`, `test_engine@`, `test_main_ab@`, `test_nessie@` (Nessie enabled), `test_nessie_local@`, `test_polish@` (Nessie enabled), `test_wording@`, `test_quickadd@`, `test_speed@`, `test_imchat@secondlook.test` (fake phone +15550001111), plus throwaway `e2e<timestamp>@example.com` users.
- E2E has a SKIP result for checks that need data the shared DB does not have yet. A SKIP is not a pass.
- Before any merge to main: all suites pass, then a smoke test of the chain `/attributes`, then embed, then `find_matches`, then trends lookup, then `/analyze`.
- Tag before big merges: `git tag pre-integration main`.
- `python scripts/check_keys.py` verifies MongoDB, Claude, Claude web search (about $0.01), Gemini and Nessie.

## 15. Savings goal `[BUILT]` (shapes fixed before build, merged Nov 14)

Optional. Skipping works the same with or without a goal. One goal per user, no history, no deadlines, no stats.

### Stored
- User doc: `goal: {name, target_cents, created_at, updated_at}` or absent. `reached` is never stored; it is derived from numbers.
- Skip verdicts already store `item {name, store, price, image_url, url}`, `action: "skipped"`, `created_at`, and for Nessie users `transfer {status, id, amount_cents, at}`. iMessage verdicts have `item.image_url` null (photo kept as `target_crop`). Seeded demo verdicts add `seeded: true`.

### saved_cents rule
- `nessie_enabled` users: projected Nessie Savings balance (`available_cents` plus pending incoming credits), the same number `GET /savings` returns as `savings_cents`. Source `"nessie"`.
- Everyone else, or when Nessie is unavailable: `round(money_kept * 100)`, the local sum of skip prices. Source `"local"`.
- Nessie reads are cached 15 s per user (shared by `/goal`, `/goal/items` and `/action`'s goal field), so repeated reads stay far under the Nessie limit of 120 requests per minute per key.
- `percent = min(100, floor(saved_cents * 100 / target_cents))`. `reached = saved_cents >= target_cents`.

### Endpoints (JWT)
- `GET /goal` returns `{goal: null}` or `{goal: {name, target_cents, saved_cents, percent, reached, source: "nessie" | "local"}}`.
- `PUT /goal` body `{name, target_cents}` creates or updates. `name` trimmed, 1 to 40 characters. `target_cents` integer, 500 to 10000000. Bad input: 422. Returns the same shape as `GET /goal`.
- `DELETE /goal` removes only the goal (skips, verdicts and Savings untouched). Idempotent. Returns `{goal: null}`.
- `GET /goal/items` returns `{items: [{verdict_id, name, store, price_cents, image_url, skipped_at, moved_cents}]}`: this user's verdicts with `action == "skipped"`, newest first, at most 100. `price_cents = round(item.price * 100)` or null; `image_url` nullable; `skipped_at` ISO 8601 with "Z" (the verdict's `created_at`; a later action time is not stored); `moved_cents` is `transfer.amount_cents` when `transfer.status == "done"`, else null. Works with or without a goal.
- `POST /action` adds optional `goal: {name, target_cents, saved_cents, reached}` only when the user has a goal, computed after the skip (for a Nessie user, the projected balance including this transfer when it is done). Absent otherwise. Nothing else in the response changes.
- All datetimes serialize with "Z" (already true for every API datetime since Nov 14).

### UI
- Card (extension): after a successful skip, when `goal` is present, one extra line under the skip message: `"$340 of $2,000 for <name>"`, or `"<name>: you made it"` when reached. No goal: card unchanged. No new buttons. Since the Nov 14 merge of Avni's redesign the card closes on Skip, so the skip message and this goal line show in the notice that appears after the card closes (main line plus a smaller second line).
- Dashboard: `dashboard/goal.js` and `dashboard/goal.css`, mounted by one include in `dashboard/index.html` (Avni owns that file). No goal: one "Set a goal" button at the top. Goal set: a mini bar (`role="progressbar"`, `aria-valuenow`, `aria-valuemin`, `aria-valuemax`) with "$340 / $2,000" inside and a small edit icon beside it. The edit icon opens a small panel (name, target, Save, Delete); Delete asks "Are you sure?" inline, never a browser dialog. Clicking the bar expands a scrollable list of skipped items (photo or neutral tile, name, store, price, date). Reached: the bar fills and reads "<name>: you made it", with one small CSS celebration the first time the UI sees it in a session. A failed `/goal` call hides the component and never breaks the page.

### Built (feat/goal, Nov 14), with deviations
- Backend: `backend/services/goal.py`, `backend/routers/goal.py`. `/action` adds `goal` only on `skipped` (bought makes no Nessie call, so it never carries a goal). `target_cents` must be a strict integer (`1500.0`, `"1500"`, `true` give 422). The 15 s cache is per process, keyed by user and Savings account id, shared by `/goal` and `/action`; `/goal/items` needs no balance and makes no Nessie call. On a skip, the post-transfer Savings balance is written into the cache, so the goal includes this transfer with no extra call; the background retry drops the entry when a late transfer lands. A goal error never fails a skip.
- Skip verdicts already store name, store, price, image_url (checked read-only: 25 skipped verdicts, all with name, store and price; 2 older docs lack image_url and url). Nothing new stored.
- Card: one `goal-line` under the skip message after any skip response (done, pending, failed, no_price) when `goal` is present; set through textContent only. No line after Buy anyway, a failed skip or a timeout.
- Dashboard: `dashboard/goal.js`, `dashboard/goal.css`, mounted by one line at the end of `dashboard/index.html`: `<script src="/dashboard-assets/goal.js" defer></script>`. UI additions beyond the spec: a Cancel button in the edit panel, a small label above the bar ("Saving for <name>" with percent, or "Goal reached: <name>"), a chevron in the bar, Escape closes the panel. The bar text is drawn twice for contrast over the fill; both layers are aria-hidden and the bar carries `aria-valuetext`. Saved rounds down and target rounds up in the text.
- Seeding extras: seed items may carry `verdict` and `reason`; seeded verdicts store `seed_key`; the user doc gains `goal_demo_savings_id` and `goal_demo_previous_savings_id`. Seeded transfers use `Second Look skip <verdict_id>` (recognized by the existing matcher as the old format). The 15 seed items total $1,900.33, all with null images.
- Known demo gap: after seeding, the demo user's earlier $49.90 skip stays in the old Savings account, so the dashboard's "Money kept" (local sum, about $1,950) differs from the goal bar (new Savings, about $1,900).

### Demo seeding
- `scripts/seed_goal_demo.py` (idempotent, `--dry-run`): a fresh Nessie Savings account for the demo user ("Second Look Savings", balance 0), the user doc pointed at it, old accounts untouched; about 15 items from `seed/goal_demo_items.json` (name, store, price_cents, image_url, days_ago) totalling about $1,900, each a real Checking to Savings transfer with description `Second Look skip <verdict_id>` (exact-description search first, so reruns never move money twice) plus a skip verdict with `seeded: true` and a past `created_at`; paced under 100 Nessie requests per minute; Checking topped up if short. Run by Hiresh only, never by agents.

## 12. Reconciliation log (Nov 14)

| Section | CONTRACT said | Code says | Fixed which |
| --- | --- | --- | --- |
| 2 | Item read has `currency`, `price_source`, `captured_at` | `Item` is name, image_url, price, store, url only | Contract |
| 2 | Removal event sent to backend | No endpoint. `removal.js` runs in the extension | Contract |
| 3 | `POST /attributes` takes `image_b64`, `store` | Takes `name, image_url, description, breadcrumb, text` | Contract |
| 3 | Attributes include `pattern` and category `other` | Neither exists. Response adds `is_apparel` and `status` | Contract (pattern is optional later) |
| 3 | `fabric_confidence` enum only | Stored model accepts number or enum, `/attributes` returns the enum | Contract |
| 5 | Closet field `thumbnail_b64`, `likely_duplicate_of` stored | Field is `image_data` (data URL). `likely_duplicate_of` is on candidates only. Also stores `image_embedding`, `description`, `source_verdict_id` | Contract |
| 5 | `GET/PUT /settings` | `GET` and `PATCH` | Contract |
| 5 | `find_matches` returns `look_match` | Returns `{score,id,name,attributes,status}`. `is_look_match()` is separate | Contract |
| 5 | Source enum 4 values | 7 values (snap, auto_add, ownership_answer, quick_add, ingest_*) | Contract |
| 6 | `features.color_family`, `sources[].published`, alternatives `{name,why,source_url}` | `features.color`, no `published`, alternatives `{type, why_it_lasts, where_to_look, url}`, `lifespan_label` required | Contract |
| 6 | No leftovers | `search_suggestions_html` field still in the model, unused | Left in code, noted |
| 7 | Verdict output shape with `matched_trend {name,...}` | Existing `AnalyzeResponse` (full objects, `outfits`, `cost_per_wear`). New fields must be additive | Contract |
| 7 | `/analyze` real | `/analyze` is still a stub | Noted as `[STUB]` |
| 9 | `/action` `buy anyway` naming | Values are `skipped` and `bought`. Response has placeholder `balances` | Contract |
| 11 | Per-agent DB name | `DB_NAME` hardcoded. Use test users | Contract |
| 3 | `/attributes` response ends at `attribute_string` | Fix A adds optional `target_box_2d`, `other_garments_visible`, `category_check`, `target_crop`. Name check can force the category | Contract (additive) |
| 6 | Trend alternatives are text only | Fix B adds optional `product_name, product_url, price, store, verified_at, link_kind` | Contract (additive) |
| 7 | `Alternative.url` is `string|null` | Fix B makes it required and non-empty, adds `store` and `link_kind`. This tightens an existing response field (shipped with Change B, Nov 14) | Contract |
| 7 | Verdict doc has no crop | Optional `target_crop` on stored verdicts (Fix A) | Contract (additive) |
| 7 | `/analyze` is a stub | Part 5 engine replaces it. Stub cycling removed | Contract |
| 7 | Claude call at temperature 0 | API rejects `temperature` on `claude-haiku-5-5`, so it is omitted. Forced tool use with strict schema, effort low, thinking disabled | Contract |
| 7 | Response ends at `cost_per_wear` | Adds optional `matched_closet_items`, `decided_by`, `closet_small`, `zone`, `wears_estimated` | Contract (additive) |
| 7 | `Item` is name, image_url, price, store, url | Adds optional `description`, `breadcrumb`. Extension sends them | Contract (additive) |
| 7 | `override {code_said, claude_said}` | Also has `rule`. All overrides in `overrides` | Contract (additive) |
| 7 | `/analyze` under 3 s typical | Measured 3 to 4 s typical | Noted, open item |
| 5 | `thresholds.json` has 3 keys | Adds `gray_margin: 0.04`. New `config/wears.json` (unverified) | Contract |
| 11 | E2E 2a and 9 assume the stub returns micro_trend | Checks run on real micro_trend cards, else SKIP; non-micro_trend cards must show no alternatives | Contract |
| 14 | No iMessage channel | Avni's Photon additions (`/imessage/*`, `users.phone`, verdict `channel` and `ownership`, data URL images) merged | Contract |
| 1 | Every Gemini call retries busy replies | `/read-page` did not retry 429 or 503, which made E2E step 5 flaky. Now retries after 2 s and 4 s | Code |
| 9 | `/action` returns `{ok, balances}` with placeholder balances | Part 6: real projected balances for Nessie users, optional `transfer {status, amount_cents}`, `verdict.transfer` stored | Contract (additive) |
| 9 | No savings endpoint | `GET /savings` with `source: nessie | local` | Contract (additive) |
| 9 | Nessie for every user | Opt-in per user (`nessie_enabled`, set by `setup_nessie_demo.py`), so E2E users never create Nessie objects | Contract (design decision) |
| 9 | Nessie shapes unknown | Recorded from the live smoke test: integer cents, pending then settled by a sweep, no idempotency key | Contract |
| 2 | Price tiers end at `/read-page` (first 8000 chars) | New final tier `POST /read-price`: Claude over text windows around every amount and the add-to-cart area, verbatim check in code. Price source `claude` | Contract (additive) |
| 9 | `waste_avoided` always null; no impact endpoint | Avni: sourced CO2e estimate in `waste_avoided`, new `GET /impact` | Contract (additive) |
| 14 | Phone only through `/imessage/link` | Avni: optional phone at signup, normalized to E.164, unique (409) | Contract (additive) |
| 1 | API datetimes without offset | All API datetimes end in "Z"; dashboard shows local time | Code |
| 5 | Hand-added items match poorly (0.64) | `has_description` flag and attributes-only comparison; index filter added | Contract (additive) |
| 7 | "I don't see this in your closet" plus a near match; hedged have_this_look | Wording rules and four new guardrails with code templates | Contract |
| 7 | Worst case attributes plus 10 s | Engine deadline about 10.5 s; `attributes` in AnalyzeResponse; startup warm-up | Contract (additive) |
| 9 | Description `Second Look skip <id>`; no-price shows pending | `Second Look skip: <item> [<id>]`; no-price shows "Skipped. No price, so nothing moved" | Contract |
| 14 | Free texts got a fixed help reply | `POST /imessage/ask` with per-account context; `imessage_messages` history | Contract (additive) |
| 14 | Phones added to Photon by hand in the dashboard | Auto-assignment at signup and `POST /imessage/connect`, `GET /imessage/status`; no invite email; replies from the receiving line | Contract (additive) |
| 14 | A yes could answer an old verdict that never asked | `ask_ownership` stored; `/own` answers only the latest verdict that asked | Code |
| 15 | No savings goal | Goal endpoints, `/action` goal field (skipped only), card line, dashboard component, demo seeding script | Contract (additive) |
| 7 | Card stays open after an action; status inside the card | Avni: add-to-cart held until Buy, card closes at once, savings result and goal line in the notice | Contract |
| 7 | No full analysis | Avni: `POST /analyze/details`, card analysis block, `/imessage/details` second text | Contract (additive) |
| 5 | No closet delete | Avni: `DELETE /closet/{item_id}`, closet redesign with a wardrobe button | Contract (additive) |
| 0 | No social radar | Social Radar (Bluesky ingest, Vultr embeddings, graph, metrics, eval, `/social`, `trends-ui`); see its section at the top of this file | Contract (additive) |
| 7 | Card buttons always "Skip it" then "Buy anyway" | Avni: good_buy shows "Buy it" first and highlighted, then "Skip it anyway". Card harness and E2E updated | Contract |

## 13. Open items

- Part 5 built. Open: `/analyze` takes 3 to 4 s typical (target under 3 s); the gray band with real closet photos is untested; the 5 TODO rows in `eval/verdicts_eval.csv` need the seed trends; no eval agreement numbers yet.
- 10 seed trends from Hiresh. `seed/trends_seed.json` holds only one `EXAMPLE barrel-leg jeans` entry. The 3 placeholder trends that `GET /trends` returns come from Part 0 data already in the database.
- Demo closet seeding (closet walkthrough video).
- `duplicate_similarity` tuning with real re-photographed pairs.
- Remove the 3 Part 0 placeholder trends and any `EXAMPLE` or `TEST` trends when the real seeds load, since they can trigger stub micro_trend verdicts.

## 14. iMessage channel via Photon (Avni) `[BUILT]`

Photon connects the system to iMessage: a shopper texts a product photo, Photon passes it to the backend, and the verdict comes back as a message. Section text from Avni, as given:

Backend additions (for CONTRACT.md)

- `backend/routers/imessage.py`: `POST /imessage/link`, `/imessage/analyze`, `/imessage/own`. Header `X-Agent-Secret`.
- `users.phone`: optional, unique sparse index.
- Signup with a phone (Avni, merged Nov 14): `POST /auth/signup` takes optional `phone` (max 40). `backend/phone.py` normalizes it to E.164 (US numbers need an area code; others start with + and the country code). A phone already on another account gives 409; an invalid one gives 422. A phone set at signup links the account to iMessage without `/imessage/link`.
- Verdict docs from iMessage get `channel: "imessage"` and `ownership: "yes" | "no"` after the answer.
  `item.image_url` is set to null after saving (the photo is kept as `target_crop`).
- `fetch_image()` also accepts `data:image/...` URLs.
- `python-multipart` added to `backend/requirements.txt` (the closet router needs it; it was missing).

Main agent notes (merged from `feat/photon-imessage-agent` on Nov 14):
- Merged with no conflicts. Live check on a separate port with a temporary secret: wrong secret 401, unlinked phone 404, link normalizes phone formats, analyze returned a verdict and message in 3.1 s, own yes added the item.
- The Node agent lives in `imessage/` (Photon SDK, polling). It needs `PHOTON_*` values and the same `IMESSAGE_AGENT_SECRET` as the backend. The backend answers 503 on `/imessage/*` until `IMESSAGE_AGENT_SECRET` is set in the repo-root `.env`.
- Fixed (Nov 14, `fix/imessage-own`): `/imessage/analyze` stores `ask_ownership` on the verdict doc. `/imessage/own` answers only the latest iMessage verdict in the 24 h window, and only when it asked (`ask_ownership: true`) and has no answer yet. A newer photo replaces an older open question, a have_this_look verdict can never be answered, and a second "yes" adds nothing. Verdicts stored before the flag are not answerable. Otherwise the reply is "Send me a photo of something you're thinking about buying first."
- Known issue: `/imessage/link` trusts any email (documented hackathon trust model). Needs a confirmation code before real users.
- Closet items added by "yes" have no description, so they share the quick-add matching gap (section 5). Fixed Nov 14 by `has_description` matching.
- Account chat (Nov 14): `POST /imessage/ask {phone, text}` returns `{message}` (same secret, 404 `phone_not_linked`, never 5xx). Claude answers in plain text, at most about 3 sentences, only from a per-account context built in code (`backend/services/account_context.py`): who (email, preference note, member since, settings, Nessie linked), closet (counts by category, newest 80 items), savings (money kept, items skipped, CO2e, Nessie balances and last 5 transfers when enabled), last 10 verdicts, last 8 messages. Context reads only by this user's id. 8 s timeout with a friendly fallback.
- `imessage_messages` collection `{user_id, phone, direction: in|out, kind: text|photo|verdict|ownership|link|answer, text (max 2000), verdict_id?, created_at}`, index `(user_id, created_at)`. link, analyze, own and ask record both sides. No image bytes. Unlinked phones store nothing.
- Agent routing (`imessage/src/route.mjs`): photo goes to analyze, an email to link, a short yes/no (6 words or fewer, no "?") to own, everything else (questions, greetings, help) to ask. The fixed HELP reply is gone.
- Photon setup (Nov 14): the project has 25 shared-line seats, 1 used. `PHOTON_AGENT_HANDLE` must be the agent line's phone number (E.164), not a display name. Seats are assigned by API, built Nov 14 (below).
- Auto-assignment `[BUILT]` (`backend/services/photon_lines.py`): signup with a phone assigns the number to a shared iMessage line in a background task (signup never waits on Photon or fails because of it). The account email is NOT sent, so Photon sends no invite email; the popup shows the line instead. Idempotency key `secondlook-assign-<sha256 of body>`, so a signup racing a Connect click cannot use two seats. An existing assignment (`ASSIGNMENT_EXISTS`) is found in the list and reused. Seats full, pool exhausted, not configured, 429 and 5xx all give a user-safe message and the account keeps working. Photon key and project id are scrubbed from logs and errors.
- `POST /imessage/connect` (JWT) body `{phone?}` and `GET /imessage/status` (JWT) return `{status: none | assigning | pending_optin | connected | error, line, phone, message}`. Connect with a new phone normalizes it (422 invalid, 409 taken), 400 when the account has no phone. Status refreshes opt-in from Photon at most once a minute while pending. `User` responses gain `imessage_line`.
- User doc fields: `photon_assignment_id`, `photon_line`, `photon_handle` (the phone the line was assigned for; an assignment only counts while it equals the current phone), `photon_opt_in`, `photon_assigned_at`, `photon_checked_at`, `photon_error`, `photon_assigning_at`. Changing the phone does not release the old seat.
- Popup states: assigning, "Text Second Look at <line> to start" (an `sms:` link), "iMessage connected", a Connect button (with a phone field when the account has none), and an error line with Try again.
- Agent replies from the line that received each message (`msg.receivedBy.id`), falling back to the agent handle.
- Full analysis over iMessage (Avni): `POST /imessage/details {phone, verdict_id}` (agent secret) returns `{message}`, the full analysis as plain text for a verdict from `/imessage/analyze`. The agent sends the verdict, then the full analysis as a second text, then "Do you already own this?" last, all from the line that received the photo.
