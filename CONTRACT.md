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
- Models: Gemini for attributes, text structuring and embeddings (`gemini-embedding-2`, 768 dims). Claude for the trend agent (Claude web search, tool type `web_search_20260318`), the Part 5 verdict call, and the final price tier (`/read-price`).
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
- `POST /read-price` (final price tier, Claude) takes `{url (max 4000), name?: string (max 300), windows: [string]}` (1 to 24 windows, 2000 chars each, 8000 total, at least one not blank, else 422) and returns `{price: number|null, price_text: string|null}`. It never answers 5xx: any Claude failure gives nulls.
- Price tiers in order: JSON-LD, meta, DOM price, `/read-page` (Gemini, first 8000 chars), then `/read-price` (Claude) only when a name exists and price is still null. Price sources: `jsonld | meta | dom | gemini | claude`.
- Claude tier rules: the extension sends windows of the full visible text: the add-to-cart area (largest container under 1500 chars) plus 120 chars around every amount (`$`, `€`, `£`, or `USD`, `EUR`, `GBP`, `CAD` after), merged, capped at 20 windows and 6000 chars, never cutting an amount. Claude returns `price_text` through a strict forced tool. The backend accepts it only if it appears verbatim in the windows (not inside a longer number) and parses with the same rules as `price.js`. Timeouts: Claude 4 s (hard cutoff 4.2 s), extension wait 5 s, then the card asks for the price as before. Diagnose shows `tiers.claude`.

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
- Guardrails in code: a verdict outside the allowed set is downgraded in priority order with a template reason; matched ids not in the provided matches are dropped; have_this_look with no valid ids downgrades (in the forced zone the best match is filled in instead); micro_trend needs `matched_trend_name` in the list; reasons over 30 words become a template; a small closet with no matches must say "I don't see this in your closet". Every change is logged in `override {rule, code_said, claude_said}` (the first) and `overrides` (all).
- Timing: attempt 1 times out at 6 s, the retry at 4 s, then a code-only verdict (`decided_by: "fallback"`). Measured: 3 to 4 s typical (attributes 0.8 to 1.5 s, matches and trends about 0.3 s, Claude 1.6 to 2.4 s), above the 3 s target. Worst case: attributes time plus 10 s. The extension times out `/analyze` at 12 s and shows "Second look unavailable".
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
- `skipped` by a Nessie user: claims `verdict.transfer` atomically (a double click never moves money twice), then moves `round(price * 100)` cents from Checking to Savings with description `Second Look skip <verdict_id>`, all inside a 3 s budget. `done` means Nessie accepted the transfer (Nessie settles it about a minute later).
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
- `GET /dashboard` returns `{money_kept, waste_avoided, verdicts (last 50), cost_per_wear}`. `money_kept` is still the local sum of `item.price` over verdicts with `action == skipped` (now in `backend/services/money.py`). `waste_avoided` and `cost_per_wear` are null for now.
- `GET /cart-summary` is a placeholder (all zero, `placeholder: true`). `GET /dashboard-ui` serves the page.
- Only Skip it counts toward money kept. Known gaps: money_kept includes skips from before Part 6 (no transfer), so it can differ from Nessie Savings; Skip then Buy anyway does not reverse the transfer; the dashboard page does not show Nessie balances yet; verdict times show UTC as if local (5 hours off in EST).

### Card after an action (Part 6)
- Skip it with `transfer.status == "done"` and an amount: "Skipped. $X moved to savings" with a check. Anything else (pending, failed, no_price, no transfer field, timeout): "Skipped. Savings update pending". On a timeout the buttons stay disabled, since the skip may have landed. Buy anyway: unchanged ("Marked as bought").

## 10. Feedback (Part 7) `[PROPOSED]`

- `POST /feedback` takes `{verdict_id, value: up|down}` and stores it on the verdict `[BUILT]`. It does not yet change any threshold.
- Part 7 adds: per-user, per-category `t_c`, step size and bounds (section 7).

## 11. Testing and isolation

- `DB_NAME` is hardcoded to `secondlook`, so agents isolate by test user: sign up `test_<part>@secondlook.test` and use that account. Never write test data to the demo user. `[PROPOSED]` small change: read `DB_NAME` from env, so a sub-agent can use its own database.
- Last known results after the Claude price tier merge: unittest 147/147, price checks 23 plus 18 window checks, extension E2E 65/65 with 2 SKIP.
- Earlier: results on the Part 5 merge (A, B, engine, card, eval): unittest 114/114, price parser 23 checks, extension E2E 61/61 after the Part 6 merge (60/60 twice after the Photon merge), unittest 133/133, card harness 65/65, with 2 SKIP (alternative link checks need a short-lifespan seed trend so a real micro_trend can occur).
- Test users in the shared DB: `test_altlinks@`, `test_target@`, `test_engine@`, `test_main_ab@`, `test_nessie@` (Nessie enabled), `test_nessie_local@secondlook.test`, plus throwaway `e2e<timestamp>@example.com` users.
- E2E has a SKIP result for checks that need data the shared DB does not have yet. A SKIP is not a pass.
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
- Verdict docs from iMessage get `channel: "imessage"` and `ownership: "yes" | "no"` after the answer.
  `item.image_url` is set to null after saving (the photo is kept as `target_crop`).
- `fetch_image()` also accepts `data:image/...` URLs.
- `python-multipart` added to `backend/requirements.txt` (the closet router needs it; it was missing).

Main agent notes (merged from `feat/photon-imessage-agent` on Nov 14):
- Merged with no conflicts. Live check on a separate port with a temporary secret: wrong secret 401, unlinked phone 404, link normalizes phone formats, analyze returned a verdict and message in 3.1 s, own yes added the item.
- The Node agent lives in `imessage/` (Photon SDK, polling). It needs `PHOTON_*` values and the same `IMESSAGE_AGENT_SECRET` as the backend. The backend answers 503 on `/imessage/*` until `IMESSAGE_AGENT_SECRET` is set in the repo-root `.env`.
- Known issue: `/imessage/own` answers the latest unanswered iMessage verdict, including have_this_look verdicts that never asked (`ask_ownership: false`). A later "yes" can then add a duplicate closet item. Fix: store `ask_ownership` on the verdict doc and filter on it.
- Known issue: `/imessage/link` trusts any email (documented hackathon trust model). Needs a confirmation code before real users.
- Closet items added by "yes" have no description, so they share the quick-add matching gap (section 5).
