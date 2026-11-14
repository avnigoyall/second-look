# Second Look: CONTRACT.md

Single source of truth for data shapes, endpoints and rules shared across parts.
Read this before touching anything another part also touches.

## 0. How to use this file

**Status tags on every section**
- `[BUILT]`: a part's build report says this exists and passed its own tests.
- `[DECIDED]`: Hiresh and Avni agreed on it, no code yet.
- `[PROPOSED]`: my default, not yet confirmed by Hiresh. Treat as changeable.
- `[VERIFY]`: I wrote this from memory of reports. Check against the code.

**Reconcile rule (main agent, do this first)**
This file was written from design decisions and build reports, not from reading the code.
1. For every `[BUILT]` and `[VERIFY]` item, diff this file against the code (models.py, db.py, routers, config/*.json).
2. Where they differ, the code wins. Fix this file, and add a line to section 12 (Reconciliation log).
3. Tell Hiresh about every difference that changes a field name, type or endpoint.

**Change rule**
- Only the main agent edits this file.
- Sub-agents never edit it. If a spec needs a different shape, the sub-agent finishes with the shape it needs listed under "Contract deviations" in its report.
- No em dashes anywhere in code comments, UI strings, docs or commit messages.

## 1. Stack and hosting `[BUILT]`

- Extension: Chrome MV3, plain JS. Content scripts render a Shadow DOM card. The service worker makes all backend calls.
- Backend: FastAPI, runs on Hiresh's laptop (port 8000). `host_permissions` and CORS allow the extension origin and localhost.
- DB: MongoDB Atlas (closet vector search needs Atlas). `MONGODB_URI` in `.env`. Never commit `.env`, keys or the URI.
- Models:
  - Gemini: item attributes, text structuring, embeddings (`gemini-embedding-2`, 768 dims).
  - Claude: verdict (tool use, JSON out) and trend agent (`CLAUDE_MODEL`, currently `claude-haiku-5-5`).
  - Trend agent search tool: Claude web search, type `web_search_20260318`, `allowed_callers: ["direct"]`, max_uses 5.
- Retries: Gemini 503s are common. Retry up to 4 attempts with 2, 4, 8 s waits. Every Gemini or Claude call needs a fallback path (see section 8).
- Demo account: `demo@secondlook.test`. Agents use their own `user_id` or DB name (section 11).

## 2. Capture events (Part 1) `[BUILT]` `[VERIFY]`

The extension emits two events to the backend.

**Item read** (on add to cart, within about 1 second)
```json
{
  "name": "string",
  "price": 19.99,
  "currency": "USD",
  "store": "uniqlo",
  "url": "https://...",
  "image_url": "https://...",
  "price_source": "dom | jsonld | meta | gemini",
  "captured_at": "ISO-8601"
}
```
Rules that must stay true:
- Price is the displayed non-member price for the selected variant. H&M ProductGroup pages are resolved by URL variant code, sku or color, never "first variant".
- Zara, Uniqlo, H&M and Pacsun have no JSON-LD Product price in meta. Price comes from DOM or Gemini.
- Add-to-cart detection is multilingual (Hollister can render Spanish) and ignores recommendation tiles unless the tile belongs to the current product.

**Removal** (item leaves the cart)
- Fire only after any confirm dialog resolves (Uniqlo "Remove" opens one).
- Removed-from-cart does NOT count toward money kept. Only `Skip it` counts.

Exact field names above are `[VERIFY]`.

## 3. Attributes (Part 2) `[BUILT]` `[VERIFY]`

`POST /attributes` takes `{name, image_url?, image_b64?, store?}` and returns:
```json
{
  "category": "enum, includes 'other'",
  "color_family": "enum",
  "cut": "string or enum",
  "fabric": "string or enum",
  "fabric_confidence": "enum (see note)",
  "style": "string",
  "pattern": "optional string",
  "description": "string",
  "attribute_string": "see template"
}
```
- Vocabulary source of truth: `config/attributes.json`, served by `GET /config/attributes`. Do not copy enum values into other files.
- `fabric_confidence`: Part 0 used a number, Part 2 uses an enum. Readers must accept both. Writers use the enum.
- `attribute_string` template (fields skipped when `unknown` or `other`):
  `{color_family} {cut} {pattern?} {fabric} {category}, {style} style. {description}`
  Pattern goes right after cut when present.
- There is exactly one implementation of `attribute_string` (in `backend/services/item_understanding.py`). No other module may build its own.
- Typical latency 1.0 to 1.4 s per call.

## 4. Embeddings `[BUILT]`

- `embed_text(attribute_string) -> list[float]` (768 dims, `gemini-embedding-2`). One call per text. Never batch several texts into one call (it merges them into one vector).
- Anything that is embedded must be embedded from `attribute_string` produced by section 3. If the attribute code changes, run `scripts/reembed_closet.py`.

## 5. Closet (Part 3) `[BUILT]` `[VERIFY]`

**Closet item document**
```json
{
  "_id": "...",
  "user_id": "string",
  "name": "string",
  "attributes": { "...section 3 fields..." },
  "attribute_string": "string",
  "embedding": [768 floats],
  "thumbnail_b64": "600px JPEG, base64",
  "status": "owned | incoming",
  "source": "ingest_photo | ingest_video | ingest_text | auto_add",
  "likely_duplicate_of": "id or null",
  "created_at": "ISO-8601"
}
```

**Endpoints**
| Method and path | Purpose |
| --- | --- |
| `POST /closet/ingest` | multipart, max 15 images and 1 video, plus optional text. Creates an `ingest_jobs` doc. |
| `GET /closet/ingest/{job_id}` | job state and candidates |
| `POST /closet/ingest/{job_id}/save` | save reviewed candidates. Second save of the same job returns 409. |
| `GET /closet?status=owned\|incoming` | list items |
| ownership endpoints | answer the ownership question when closet has fewer than 10 items |
| `POST /action` | buy anyway / skip it, see section 9 |
| `GET/PUT /settings` | `auto_add_purchases` (default `true`) |
| `GET /closet-ui` | review page (grid with editable dropdowns, "Save all") |

**Ingest pipeline**: ffmpeg frames at about 1 fps (cap about 40), Gemini batches of about 10 images at about 1024 px with `box_2d` ([ymin, xmin, ymax, xmax], scaled 0 to 1000), text parsing, Gemini merge-duplicates call, embedding comparison to existing closet (sets `likely_duplicate_of`), Pillow crop to 600 px thumbnails.

**Buy anyway**: silent auto-add to closet with `status: incoming`, `source: auto_add`, no prompt, no notification. Controlled by `settings.auto_add_purchases`.

**Matching: `find_matches(user_id, attributes_or_embedding, k)`**
Returns, best first:
```json
[{ "score": 0.8585, "item_id": "...", "name": "Black tee", "status": "owned",
   "color_family": "black", "category": "tee", "look_match": true }]
```
- `score` is plain cosine. Atlas reports `(1 + cos) / 2`, convert before returning.
- Atlas index `closet_vector`, filters on `user_id` and `attributes.category`. Free tier allows 3 search indexes total.
- Color is decided in code from `color_family`, because text embeddings barely separate colors (blue vs black jeans scored 0.7996).
- `config/thresholds.json` today: `{"look_similarity": 0.80, "require_color_match": true, "duplicate_similarity": 0.93}`. `duplicate_similarity` is untuned (a re-photographed tee scored 0.8585). Leave it until `tune_matching.py` has real pairs.

## 6. Trends (Part 4) `[BUILT]` `[VERIFY]`

**Trend document**
```json
{
  "name": "string",
  "category": "must map to a category in attributes.json",
  "features": { "cut": "...", "color_family": "...", "fabric": "..." },
  "why": "string",
  "sources": [{ "title": "...", "url": "...", "published": "date or null" }],
  "lifespan_bucket": "short | medium | long | null",
  "lifespan_weeks_est": "number or null",
  "lifespan_source_url": "required when lifespan_weeks_est is set",
  "alternatives": [{ "name": "...", "why": "...", "source_url": "..." }],
  "origin": "seed | agent",
  "created_at": "ISO-8601"
}
```
- `GET /trends` lists them. `get_trends_for_prompt()` returns the compact form used in the verdict prompt.
- Rules (hardening, status `[DECIDED]`): no trend without a category, a trend is a specific garment or accessory style (not a color or an aesthetic), sources older than 12 months are dropped, lifespan fields are null when sources disagree, dedupe on category plus features, not name alone.
- Lifespan numbers are shown to users only with a cited source.
- No human review of agent trends. Guards live in code.
- No document whose name starts with `TEST` may exist in a shared DB.

## 7. Verdicts (Part 5) `[DECIDED]` `[PROPOSED]`

Agreed by Hiresh: V1, V5, V8, and hybrid authority (V10). Everything else here is my default.

**Verdict values**: `have_this_look`, `micro_trend`, `good_buy`. One verdict per item.
**Priority**: `have_this_look` > `micro_trend` > `good_buy`.

**Authority is hybrid**
- Code computes signals: closet matches (section 5), trend match, price context.
- One Claude call, JSON via tool use, judges only the gray band.

**Zones for `have_this_look`** (per category `t_c`, starts at `look_similarity` 0.80)
| Score vs `t_c` | Who decides |
| --- | --- |
| at or above `t_c + margin` | code forces `have_this_look` |
| below `t_c - margin` | code forbids `have_this_look` |
| in between | Claude decides |

Thumbs-down raises `t_c`, thumbs-up lowers it. Step size is set in Part 7. Margin value: `[PROPOSED]` 0.04.

**Images**: Claude receives text only, except in the gray band. There it also gets the item photo plus the top 1 or 2 matched closet photos.

**Small closet** (fewer than 10 items): when nothing matches, say "I don't see this in your closet". Never claim the user does not own it.

**micro_trend** requires a matched trend with `lifespan_bucket == "short"`.
**Alternatives** come from the matched trend's source-based alternatives only.

**Verdict output**
```json
{
  "verdict": "have_this_look | micro_trend | good_buy",
  "reason": "one or two sentences, shown on the card",
  "matched_closet_item_id": "id or null",
  "matched_trend": { "name": "...", "lifespan_bucket": "...", "source_url": "..." },
  "alternatives": [{ "name": "...", "why": "...", "source_url": "..." }],
  "decided_by": "code | claude | fallback",
  "override": { "code_said": "...", "claude_said": "..." }
}
```
- `override` is logged in the stored verdict doc whenever Claude disagrees with code inside the allowed zone.
- Failure fallback: retry the Claude call once, then fall back to a code-only verdict with a template reason and `decided_by: "fallback"`.
- `POST /analyze` takes the section 2 item read and returns the verdict plus the item's attributes. It must always return something within the card's time budget (target under 3 s typical, hard cap decided in Part 5).

## 8. Failure behavior (all parts) `[DECIDED]`

- The card must never block the store. If the backend is down or slow, show "Second look unavailable" and let the click through.
- Gemini failure: retry with backoff, then degrade (no attributes means code-only verdict `good_buy`).
- Claude failure: section 7 fallback.
- Atlas failure on `find_matches`: no match, and the card says nothing about closet ownership.

## 9. Actions and money (Parts 6 and 8) `[DECIDED]` `[PROPOSED]`

- Card actions: `Skip it`, `Buy anyway`, thumbs up/down (Part 7).
- `Skip it` counts toward money kept (price of the item at skip time).
- Items removed from the cart do not count toward money kept.
- `Buy anyway` logs a purchase and triggers the auto-add rule in section 5.
- Stored per action: `{user_id, item read, verdict, action, ts}`. Collection name as in `db.py`.

## 10. Feedback (Part 7) `[PROPOSED]`

- Thumbs on a verdict adjusts `t_c` for the item's category (section 7). Per-user, per-category value stored on the user doc.
- Step size and bounds are set in Part 7. Bounds `[PROPOSED]`: `t_c` between 0.70 and 0.92.

## 11. Testing and isolation rules

- Each agent or sub-agent uses its own `user_id` (for example `test_part5`), or its own DB name (`secondlook_test_<part>`). Never write test data to the demo user.
- Test fixtures for the extension live in `extension/tests/fixtures`. Real-store retests are done by Hiresh or the browser agent, never faked.
- Before any merge to main: all suites pass (backend, trends, extension), then a smoke test of the chain: `/attributes` then embed, then `find_matches`, then trends lookup, then `/analyze`.
- Tag before big merges: `git tag pre-integration main`.

## 12. Reconciliation log (main agent fills this in)

| Date | Section | Contract said | Code says | Fixed which |
| --- | --- | --- | --- | --- |
| | | | | |

## 13. Open items

- Part 5 defaults V2 to V4, V6, V7 and V9 to V15 not confirmed.
- Whether the contract patch (`pattern`, `category: "other"`) landed.
- Whether `lifespan_bucket` landed on trends in the code.
- 10 seed trends from Hiresh (`seed/trends_seed.json` is empty until then).
- Demo closet seeding (closet walkthrough video).
- `duplicate_similarity` tuning.
