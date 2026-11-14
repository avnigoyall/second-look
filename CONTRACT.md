# Second Look: CONTRACT.md

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
- Models: Gemini for attributes, text structuring and embeddings (`gemini-embedding-2`, 768 dims). Claude for the trend agent (Claude web search, tool type `web_search_20260318`) and, from Part 5, the verdict call.
- Gemini 503s are frequent. Every Gemini call retries up to 4 attempts (2, 4, 8 s waits) and every caller has a fallback (section 8).
- Demo login: `demo@secondlook.test`. Auth is JWT (`/auth/signup`, `/auth/login`, `/auth/me`).

## 2. Capture (Part 1) `[BUILT]`

The extension builds an `Item` and sends it to `/analyze`.

```json
Item { "name": "string", "image_url": "string|null", "price": 19.99, "store": "string|null", "url": "string|null" }
```
- `price` is dollars, `>= 0`, or null.
- The extension keeps price source (dom, jsonld, meta, Gemini via `/read-page`) in its own logs. It is not part of the API.
- `POST /read-page` takes `{url, text (max 50000, first 8000 used), image_url?}` and returns `{name?, price?, image_url?}`. Used when the DOM price tier fails.

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
  "attribute_string": "string, empty when attributes_failed"
}
```
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
- Closet text embeddings must come from `attribute_string`. After changing that code, run `python scripts/reembed_closet.py` (supports `--dry-run`, writes nothing if any call fails).

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

Candidate (`CandidateItem`): `id, origin photo|video|text, name, attributes, description, confidence 0..1, low_confidence, image_index, frame, box_2d [ymin,xmin,ymax,xmax] 0..1000, brand_visible, seen_count, thumbnail, likely_duplicate_of {id, name, score}`. `likely_duplicate_of` exists on candidates only, it is not stored.

Ingest pipeline: ffmpeg frames at about 1 fps (cap about 40), Gemini batches of about 10 images at 1024 px, text parsing, Gemini merge-duplicates call, embedding comparison to the existing closet, Pillow crops.

Buy anyway: `POST /action` with `bought` triggers a background auto-add (`status incoming`, `source auto_add`) after the response, unless `settings.auto_add_purchases` is false. The `/action` response does not change.

Matching (`backend/services/closet.py`)
- `find_matches(user_id, attrs, k=5)` returns `[{score, id, name, attributes, status}]`, best first. `score` is plain cosine. Atlas reports `(1 + cos) / 2`, converted by `atlas_to_cosine`. Text embedding only, filtered to the same user and category. It never returns image_data or embeddings.
- `is_look_match(match, attrs)` applies `config/thresholds.json` and the color check. Color is decided from `color_family` in code, because text embeddings barely separate colors (blue vs black jeans scored 0.7996).
- `config/thresholds.json`: `{"look_similarity": 0.80, "require_color_match": true, "duplicate_similarity": 0.93}`. `duplicate_similarity` is untuned (a re-photographed tee scored 0.8585).
- Atlas index `closet_vector`, filters `user_id` and `attributes.category`. Create with `python scripts/create_indexes.py`. Free tier allows 3 search indexes.

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
  "alternatives": [{ "type": "...", "why_it_lasts": "...", "where_to_look": "store kind, not a link", "url": "only if in sources" }],
  "origin": "seed | agent",
  "search_suggestions_html": "agent only. Leftover from the dropped Gemini grounding path, unused"
}
```
- Seed file: `seed/trends_seed.json` (loaded by `scripts/seed_trends.py`). Agent: `scripts/run_trend_agent.py` (Claude web search, then structuring). A real run waits for the seeds.
- Guards in code: no category means discard, sources older than 12 months dropped, lifespan nulled when sources disagree, dedupe on category plus feature signature (not name alone), existing names passed into the structuring call.
- Lifespan numbers are shown to users only with the cited source.
- No human review of agent trends. Guards are the safety net.
- Trend alternatives are text suggestions. The verdict `Alternative` shape below is different (section 7), so Part 5 maps one to the other.

## 7. Verdicts (Part 5)

### Current code `[STUB]`
`POST /analyze` takes `{item: Item}` and cycles through the three verdicts by the user's verdict count. It never calls Parts 2, 3 or 4. Matched closet item is the user's oldest item, matched trend is the first trend by `_id`. `attributes` is stored as null.

Response (`AnalyzeResponse`, `[BUILT]`, the extension depends on it):
```json
{ "status": "ok", "verdict_id": "string",
  "verdict": "have_this_look | micro_trend | good_buy", "reason": "string",
  "matched_closet_item": "ClosetItem|null", "matched_trend": "Trend|null",
  "alternatives": [{ "name": "string", "url": "string|null", "price": 48.0, "reason": "string|null" }],
  "outfits": [{ "name": "string", "closet_item_ids": [] }],
  "expected_wears": "int|null", "cost_per_wear": "float|null" }
```
Stored verdict doc: `user_id, item, attributes, verdict, reason, matched_closet_item_id, matched_trend_id, alternatives, expected_wears, action none|skipped|bought, feedback none|up|down, created_at`.

### Part 5 rules `[DECIDED]` (V1, V5, V8 and hybrid authority agreed) and `[PROPOSED]` (the rest)
- Priority: `have_this_look` > `micro_trend` > `good_buy`. One verdict per item.
- Hybrid authority: code computes signals (closet matches, trend match, price). One Claude call, JSON via tool use, judges only the gray band.
- Zones for `have_this_look`, per category threshold `t_c` (starts at `look_similarity`):
  - score at or above `t_c + margin`: code forces `have_this_look`
  - below `t_c - margin`: code forbids it
  - in between: Claude decides
  - `margin` `[PROPOSED]` 0.04. Thumbs-down raises `t_c`, thumbs-up lowers it. Part 7 sets the step. Bounds `[PROPOSED]` 0.70 to 0.92.
- Claude sees text only, except in the gray band, where it also gets the item photo and the top 1 or 2 matched closet photos.
- Closet under 10 items and no match: say "I don't see this in your closet". Never claim the user lacks it.
- `micro_trend` needs a matched trend with `lifespan_bucket == "short"`. Alternatives come from the matched trend's source-based alternatives, mapped into the `Alternative` shape.
- New optional response and stored fields (additive only): `decided_by: code | claude | fallback`, `override: {code_said, claude_said}` logged whenever Claude overrides code inside its zone.
- Failure: retry the Claude call once, then a code-only verdict with a template reason and `decided_by: "fallback"`.
- `/analyze` must always return within the card's time budget (target under 3 s typical, hard cap set in Part 5).
- Attributes for the item come from Part 2 (`/attributes` logic called in-process), and are stored on the verdict doc.

## 8. Failure behavior (all parts) `[DECIDED]`

- The card never blocks the store. If the backend is down or slow, show "Second look unavailable" and let the click through.
- Gemini failure: retry with backoff, then degrade (no attributes means a code-only verdict).
- Claude failure: section 7 fallback.
- Atlas failure in `find_matches`: no match, and the card says nothing about closet ownership.

## 9. Actions, money, dashboard `[BUILT]` (placeholders noted)

- `POST /action` takes `{verdict_id, action: skipped|bought}` and returns `{ok, balances}`. Balances are a placeholder (`placeholder: true`, Nessie not wired).
- `GET /dashboard` returns `{money_kept, waste_avoided, verdicts (last 50), cost_per_wear}`. `money_kept` is the sum of `item.price` over verdicts with `action == skipped`. `waste_avoided` and `cost_per_wear` are null for now.
- `GET /cart-summary` is a placeholder (all zero, `placeholder: true`). `GET /dashboard-ui` serves the page.
- Only Skip it counts toward money kept.

## 10. Feedback (Part 7) `[PROPOSED]`

- `POST /feedback` takes `{verdict_id, value: up|down}` and stores it on the verdict `[BUILT]`. It does not yet change any threshold.
- Part 7 adds: per-user, per-category `t_c`, step size and bounds (section 7).

## 11. Testing and isolation

- `DB_NAME` is hardcoded to `secondlook`, so agents isolate by test user: sign up `test_<part>@secondlook.test` and use that account. Never write test data to the demo user. `[PROPOSED]` small change: read `DB_NAME` from env, so a sub-agent can use its own database.
- Last known results on the integration merge: extension E2E 54/54, trends unittest 22/22, price parser 23 checks, backend endpoints `/health`, `/attributes`, `/trends`, `/closet` return 200.
- Before any merge to main: all suites pass, then a smoke test of the chain `/attributes`, then embed, then `find_matches`, then trends lookup, then `/analyze`.
- Tag before big merges: `git tag pre-integration main`.
- `python scripts/check_keys.py` verifies MongoDB, Claude, Claude web search (about $0.01), Gemini and Nessie.

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

## 13. Open items

- Part 5 not built. Defaults V2 to V4, V6, V7, V9 to V15 not confirmed.
- 10 seed trends from Hiresh. `seed/trends_seed.json` holds only one `EXAMPLE barrel-leg jeans` entry. The 3 placeholder trends that `GET /trends` returns come from Part 0 data already in the database.
- Demo closet seeding (closet walkthrough video).
- `duplicate_similarity` tuning with real re-photographed pairs.
- Remove the 3 Part 0 placeholder trends and any `EXAMPLE` or `TEST` trends when the real seeds load, since they can trigger stub micro_trend verdicts.
