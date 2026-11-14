# Second Look

A Chrome extension that checks an item a shopper adds to an online cart against their closet and a trend list, then shows one of three verdicts: `have_this_look`, `micro_trend`, or `good_buy`.

Built so far:

- **Part 0: Foundation.** API, database, auth, extension messaging, and dashboard. Verdicts are still stubs with canned data.
- **Part 1: Capture.** The extension reads the product a shopper adds to their cart, shows the verdict card on the page, tracks the items checked in this session, and notices when one is removed from the cart.
- **Part 2: Item understanding.** `POST /attributes` sends one item (image plus text) to Gemini and returns attributes from `attributes.json`, plus the `attribute_string` that Part 3 embeds.
- **Part 3: Closet.** Closet intake from photos, a video, and free text (review page at `/closet-ui`), Gemini embeddings with Atlas Vector Search matching, the ownership question for small closets, and silent auto-add of "Buy anyway" purchases.
- **Part 4: Trends.** Curated seed trends plus an agent (Claude web search, then Gemini structuring) that adds sourced trends. `GET /trends` and `get_trends_for_prompt()` expose them for the verdict prompt.

Not built yet: real verdict logic, trend matching against items, Nessie, and the cart summary UI.

## Layout

| Folder | What it is |
| --- | --- |
| `backend/` | FastAPI app (Python 3.10+). Allowed attribute values live in `backend/config/attributes.json` |
| `extension/` | Chrome Manifest V3 extension, plain JavaScript, no build step |
| `dashboard/` | One static page, served by the backend at `/dashboard-ui` |
| `docs/` | `store-test-results.md`, the table for testing real stores by hand |
| `seed/` | `seed.py` plus a `photos/` folder for the demo user's closet |
| `scripts/` | `check_keys.py` checks each external service key. `eval_attributes.py` scores Part 2. `create_indexes.py` builds the Atlas indexes, `tune_matching.py` scores closet matching and `reembed_closet.py` recomputes closet embeddings. `seed_trends.py` and `run_trend_agent.py` fill the trends collection |
| `eval/` | `attributes_eval.csv`, labeled items for the Part 2 eval, and `match_pairs.csv` for matching |
| `tests/` | Offline Python tests: `python -m unittest discover tests` |

## Setup

You need Python 3.10 or newer, MongoDB (local or Atlas), and Chrome.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env        # then fill in the values
```

`.env` variables:

| Variable | Used for |
| --- | --- |
| `MONGODB_URI` | MongoDB connection. Local: `mongodb://localhost:27017` |
| `JWT_SECRET` | Signs login tokens. Any long random string, for example from `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `ANTHROPIC_API_KEY` | Claude (later parts) |
| `GEMINI_API_KEY` | Gemini (later parts) |
| `NESSIE_API_KEY` | Capital One Nessie (later parts) |
| `CLAUDE_MODEL` | Model id for the Claude key check. Use a small, cheap model, for example `claude-haiku-5-5` |
| `GEMINI_MODEL` | Model id for `/read-page` and the Gemini key checks. Use a fast model, for example `gemini-3.5-flash-lite` (see Assumptions) |
| `TREND_AGENT_MODEL` | Optional. Gemini model id for the structuring call in `run_trend_agent.py`. Default `gemini-3.5-flash`. The search call uses `CLAUDE_MODEL` |

## Run

All commands run from the project root with the virtual environment active.

```bash
python seed/seed.py                          # safe to run again
python scripts/seed_trends.py                # curated trends from seed/trends_seed.json, safe to run again
uvicorn backend.main:app --reload --port 8000
python scripts/check_keys.py                 # one PASS or FAIL line per service
```

- API docs (the API contract): http://localhost:8000/docs
- Dashboard: http://localhost:8000/dashboard-ui
- Seeded logins: `demo@secondlook.test` / `demo1234` and `new@secondlook.test` / `new1234`

To give the demo user a closet, put `.jpg`, `.jpeg`, `.png` or `.webp` photos in `seed/photos/` and run the seed script again. The file name becomes the item name (`blue_denim-jacket.jpg` becomes "Blue denim jacket").

## Load the extension in Chrome

1. Start the backend (see Run).
2. Open `chrome://extensions`.
3. Turn on **Developer mode** (top right).
4. Click **Load unpacked** and select the `extension/` folder.
5. Pin Second Look from the puzzle-piece menu so its icon is visible.

After you change extension code, click the reload icon on the Second Look card in `chrome://extensions`, then reload any open tab you test on.

The popup has three parts: **Check this item**, **Session items**, and **Store testing** (Diagnose).

### Test Part 1 by hand in Chrome

Before you start: the backend is running, the extension is loaded (or reloaded after pulling new code), and you are logged in from the popup as `demo@secondlook.test` / `demo1234`. Reload any store tab that was open before you loaded or reloaded the extension.

**1. Add-to-cart on a page with JSON-LD.** Open a product page on a store such as Uniqlo or H&M. Choose a size and click the store's add-to-cart button. A card appears in the top right corner: first "Second Look is checking...", then a verdict with Skip it, Buy anyway, thumbs up, and thumbs down. The store still adds the item to your cart. Clicking add-to-cart again on the same page within 3 seconds does nothing.

**2. A page with no product tags.** Use a store page where Diagnose reports tier 3, or any page with a product name and price in its text. Click add-to-cart. Gemini reads the page and the card shows the verdict. If no product can be read at all, the card shows **Paste the product link** instead.

**3. Paste a product link.** In the paste-a-link card, paste the full link to a product page (copy it from another tab) and click **Check**. The button shows a spinner while Second Look opens the link in a hidden background tab, reads it, and closes the tab. The card then shows the verdict. A bad link shows a short error under the field.

**4. Removal.** Add two different products to your cart (each one gets a verdict card). Open the store's cart and remove one of them. A small notice says "Removed from your Second Look list: ...". Open the popup: under Session items, that item shows **Removed** and the other does not. If a store's remove button is not detected, use the × next to the item in the popup instead.

**5. Diagnose.** On any product page, open the popup and click **Diagnose this page**. A one-line summary shows the tier, which fields were found, and how many add-to-cart buttons were detected. Click **Copy JSON** and paste it into `docs/store-test-results.md` notes or a message. The same report is logged in the page's DevTools console with the prefix `[SecondLook]`.

Record real store results in `docs/store-test-results.md`.

## How item capture works

**Reading the item (four tiers, in order).** `extension/src/content/reader/`:

1. `jsonld.js` reads schema.org `Product` or `ProductGroup` data from JSON-LD scripts, including `@graph` containers, offer arrays, and `AggregateOffer`.
2. `meta.js` reads Open Graph and product meta tags (`og:title`, `og:image`, `product:price:amount`) and microdata (`itemprop`). It strips a trailing store name from titles ("Linen Shirt | Zara" becomes "Linen Shirt").
3. If tier 1 or 2 found a name but no price, `domPrice.js` reads the price off the page before any Gemini call: `itemprop="price"`, then `[data-price]`, then short currency text inside the main product container (the element that holds both the `<h1>` and the add-to-cart button). It skips struck-through or "was / original / old" prices, so a sale shows its current price. A range takes the lower value.
4. If the name or image is still missing, or the price is still missing, `readItem.js` sends the trimmed visible page text (up to 8000 characters) and an image URL to `POST /read-page`, which asks Gemini for `{name, price, image_url}`.
5. If no name is found, the card shows paste-a-link.

Every read logs `[SecondLook] price source: jsonld | meta | dom | gemini | none` in the page console, and Diagnose reports `price_source` plus what the DOM tier found (`tiers.dom.via`).

`price.js` parses prices with currency symbols or codes, thousands separators, decimal commas (`€39,90`), and ranges or "was / now" text (the lowest number wins). If no price is found, the card asks for it before checking.

**Triggering.** `trigger.js` attaches one capture-phase click listener to the document, so it keeps working after single-page-app navigation and runs before the store's own handler without blocking it. One check per URL every 3 seconds. Nothing runs on page load. `matchers.js` decides what counts as add-to-cart:

- Text, `aria-label`, `title`, or `value` matching `add (to) (shopping) bag|cart|basket`, `añadir|agregar a (la) bolsa|cesta|carrito`, `ajouter au panier`, or `in den warenkorb`.
- `data-qa-action`, `data-test`, `data-qa`, `data-testid`, `id`, or a class token matching `add-to-cart|bag|basket` (hyphen, underscore, space, or nothing between words).
- A button whose text is exactly "ADD" (Zara) counts only when a `data-*` attribute also says add-to-cart.
- Never a wishlist or list button: anything mentioning wishlist, favorite, save, or "lista" is excluded, even if another attribute says add-to-cart.
- Never an add button inside a product tile for a different product (recommendations). A tile is the nearest small container with a linked image. If the tile links to the current page, or the button has no tile, it triggers. A container that holds the page's `<h1>` is the main product, not a tile.

**Removal.** `removal.js` listens for clicks on controls whose text, `aria-label`, or `title` contain remove, delete, or trash. It saves the text of the control's ancestors right away (before the store deletes the row), then finds the closest ancestor whose words cover at least 60% of a session item's name.

**Messages.** Every backend call goes through the service worker with a 15 second timeout: `LOGIN`, `LOGOUT`, `GET_STATE`, `CHECK_ACTIVE_TAB`, `DIAGNOSE`, `ANALYZE_ITEM`, `READ_PAGE`, `READ_URL`, `SEND_ACTION`, `SEND_FEEDBACK`. Errors carry a code. Not logged in or server unreachable shows the error state. Every other failure shows paste-a-link.

**Session list.** `extension/src/shared/sessionStore.js` keeps up to 50 entries under `chrome.storage.local` key `secondLookSession`: `{id, name, price, image_url, store, url, verdict_id, verdict, status, ts}`. Status is checked, skipped, bought, or removed.

## Trends (Part 4)

Two layers fill the `trends` collection. Both run offline from the project root.

**1. Seed trends.** Put the curated trends in `seed/trends_seed.json` (the `_template` key in that file lists every field), then run `python scripts/seed_trends.py`. Entries with `"_placeholder": true` are skipped. The script checks every entry first and writes nothing if one is invalid. It upserts by name (case-insensitive) and sets `origin: "seed"`.

**2. Trend agent.** `python scripts/run_trend_agent.py` adds up to 5 new trends with `origin: "agent"`.

```bash
python scripts/run_trend_agent.py                         # default Gen Z and college queries
python scripts/run_trend_agent.py -q "..." -q "..."       # your own focus queries
python scripts/run_trend_agent.py --dry-run               # print what would be added, write nothing
python scripts/run_trend_agent.py --from-grounded .trend_runs/grounded-<time>.json   # reuse a saved search
```

- **Call (a), web search:** Claude (`CLAUDE_MODEL`) with the server-side web search tool (`web_search_20260318`, at most 5 searches) writes a cited research report. The script keeps the text, every cited source's title and URL, and which sentences cite which source, prints the source list, and saves it all to `.trend_runs/` (git-ignored).
- **Call (b), structuring:** Gemini with no tools turns the report into trend documents. The report is marked with source ids (`[S3]`) and the model cites ids only. The code maps ids back to URLs, so a URL cannot be invented.
- **Guards in code:**
  - A trend needs a `category` from `attributes.json`, so a color or an aesthetic ("cherry red", "quiet luxury") cannot become a trend. Both prompts also say a trend is a specific garment, shoe, bag, or accessory style.
  - A trend needs at least one source from call (a). Sources older than 12 months are dropped, judged by the search result's `page_age`, or by a `/YYYY/MM/` date in the URL when there is no `page_age`. Sources with no date are kept. A trend whose sources were all dropped is discarded.
  - `lifespan_weeks_est` and `lifespan_bucket` need a cited, recent source. Both are null when the sources disagree on direction (one says rising or lasting, another says fading). When weeks are set, the bucket is computed from them.
  - Dedupe against existing trends and within the run: by name (case-insensitive, backed by a unique index), and by the same `category` plus the same `cut`, `color`, and `fabric`. Existing trend names are also passed to the structuring call with an instruction not to repeat them.
  - Source ids such as "S3" are removed from `lifespan_label`. An alternative's link is kept only if it is a recent source from call (a). At most 5 new trends per run.
- If the Gemini structuring call gets a 429 or 5xx, it retries after 5 and 20 seconds. If it still fails, the script prints the `--from-grounded` command that retries without searching again.
- If call (a) returns no cited sources, the script prints a warning, adds nothing, and exits with code 2.
- Agent trends have `search_suggestions_html: null`. Show their `sources` links instead.

**3. Alternative links.** Every alternative the card shows is one link that opens in a new tab. Links are found offline, never at verdict time:

- `seed_trends.py` and the trend agent (step c) make one Claude web search call per trend (`backend/alternative_finder.py`, restricted to basics retailers listed in `backend/alternatives.py`) to find 1 or 2 basic product pages. About $0.01 to $0.03 per trend.
- A product URL is used only if it came back in a web search result block. The model's own text is never trusted for a URL. The URL is then checked over HTTP with a browser User-Agent: status 200, same registrable domain after redirects, and no redirect to a home, search, or error page. A 403 or 429 from bot protection keeps the link with `verified_at: null`.
- Price comes from the product page when it loads, else only from text Claude cited from that page. The name comes from the page title or the search result title.
- If no product link survives, the alternative gets a store search link (`link_kind: "search"`, for example "Search Gap for straight-leg jeans").
- `seed_trends.py` flags: `--dry-run` (find links, write nothing), `--no-links` (search links only, no cost), `--refresh-links` (search again; otherwise stored links are reused). The agent has `--no-links`.
- `python scripts/verify_alternative_links.py` re-checks every stored link and lists dead ones. It is read-only; `--write` only updates `verified_at`.
- `backend.alternatives.alternatives_for_trend(trend)` maps a trend's alternatives into the verdict `Alternative` shape (`name, url, price, reason, store, link_kind`). `url` is always set. `/dashboard` gives older verdicts with a null url a search link when it reads them.
- `extension/tests/harness/card.html` renders the real card with sample alternatives, without the extension or a backend (serve `extension/` with `python3 -m http.server`).

**Reading trends.** `GET /trends` (JWT) returns every trend without `example_images`; each trend has `example_image_count` instead. `backend.trends.get_trends_for_prompt()` returns a compact list for the verdict prompt: `name`, `category`, `features`, `lifespan_bucket`, `lifespan_label`, and `source_titles`.

## Tests

```bash
node extension/tests/price.test.js       # price parser unit tests
python -m unittest discover tests        # trend guards, seed rules, alternative links, offline
```

### Extension E2E test

`extension/tests/e2e/e2e.cjs` loads the real unpacked extension into Chromium with Playwright and walks the Part 1 acceptance list against local fixture store pages in `extension/tests/e2e/fixtures/`. It makes one real Gemini call per run (about 1 second).

```bash
# terminal 1: backend
uvicorn backend.main:app --port 8000
# terminal 2: fixture store pages
cd extension/tests/e2e/fixtures && python3 -m http.server 8765 --bind 127.0.0.1
# terminal 3: needs the playwright npm package and its Chromium (npx playwright install chromium)
PW_PATH=/path/to/node_modules/playwright node extension/tests/e2e/e2e.cjs
```

Set `CHROME_PATH` to use a specific Chromium build. Screenshots go to `extension/tests/e2e/shots/` (git-ignored). Each run signs up a throwaway `e2e<timestamp>@example.com` user.

### Part 2 attribute eval

`eval/attributes_eval.csv` has the columns `source` (image URL, or image path relative to the project root), `name`, `expected_category`, and `expected_color_family`. It calls the service directly, so no server or login is needed. Target: 90% on category and color_family.

```bash
python scripts/eval_attributes.py                  # or pass another CSV path
```

## Closet (Part 3)

Part 3 needs **MongoDB Atlas** (the free M0 tier is enough). A plain local `mongod` cannot run `$vectorSearch`: closet intake still works there, but duplicates are not flagged and `find_matches` fails. Videos need ffmpeg (`brew install ffmpeg`).

All commands run from the project root with the virtual environment active.

**1. Create the vector index (once per cluster).** Lists the search indexes that already exist (the free tier allows 3 in total), creates `closet_vector` only if it is missing, and waits until it is queryable (about 1 minute):

```bash
python scripts/create_indexes.py
```

**2. Start the backend and open the review page.**

```bash
uvicorn backend.main:app --reload --port 8000
```

Open http://localhost:8000/closet-ui and log in (for example `demo@secondlook.test` / `demo1234`). Drop photos or a video, and/or describe clothes ("two white tees and black jeans"), then click **Find my clothes**. The progress bar follows the job. On the review grid, fix any dropdown, remove what is not yours, add a missing item by text, and click **Save all**. Your saved closet shows below.

**3. Ingest from the command line instead (same API the page uses).**

```bash
B=http://localhost:8000
TOKEN=$(curl -s -X POST $B/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"demo@secondlook.test","password":"demo1234"}' | python -c "import sys,json;print(json.load(sys.stdin)['token'])")
H="Authorization: Bearer $TOKEN"
# repeat -F files=@... once per file (up to 15 photos and 1 video)
JOB=$(curl -s -H "$H" -X POST $B/closet/ingest $(for f in seed/photos/*.jpg; do printf -- '-F files=@%s ' "$f"; done) \
  -F "text=two white tees and black jeans" | python -c "import sys,json;print(json.load(sys.stdin)['job_id'])")
curl -s -H "$H" $B/closet/ingest/$JOB          # poll until "status": "done", then review the items
```

Save with `POST /closet/ingest/{job_id}/save` and a body of `{"items": [{"candidate_id", "name", "attributes", "description"}]}` (leave out `candidate_id` for items added by text).

**4. See matches for one item.**

```bash
python -c "
from backend.db import users; from backend.services.closet import find_matches, is_look_match
u = users.find_one({'email': 'demo@secondlook.test'})
attrs = {'category': 'tee', 'color_family': 'white', 'cut': 'regular', 'fabric': 'cotton', 'style': 'basic'}
for m in find_matches(u['_id'], attrs): print(m['score'], is_look_match(m, attrs), m['name'], m['status'])"
```

**5. Check "Buy anyway" auto-add.** Mark any verdict as bought (the extension's **Buy anyway**, or `POST /action {"verdict_id", "action": "bought"}`). A few seconds later, `GET /closet?status=incoming` lists it. Turn it off with `PATCH /settings {"auto_add_purchases": false}`.

**6. Tune the match thresholds.** Add rows to `eval/match_pairs.csv` (`description_a`, `description_b`, `same_look` yes or no), then:

```bash
python scripts/tune_matching.py
```

It prints the cosine similarity for each pair, marks which pairs the current `look_similarity` gets wrong, and suggests a cutoff. Edit `backend/config/thresholds.json`. The server rereads it on every call, so no restart is needed.

### Part 3 endpoints

| Endpoint | What it does |
| --- | --- |
| `POST /closet/ingest` | Multipart: `files` (repeat per file, up to 15 photos and 1 video) and `text`. Returns `{job_id}` at once. The work runs in a background task |
| `GET /closet/ingest/{job_id}` | `status` (queued, running, done, failed, saved), `progress {step, done, total}`, `warnings`, `error`. Candidate items with 600px crops once done |
| `POST /closet/ingest/{job_id}/save` | Embeds and saves the reviewed items. A second save returns 409 |
| `POST /closet/parse-text` | `{text}` to candidate items. No photo, no box |
| `POST /closet/ownership-options` | `{name, attributes}` to `{show, options[3], staple_idea}`. `show` is true only while the closet has fewer than 10 items |
| `POST /closet/ownership-answer` | `{attributes, name?}` saves a closet item with source `ownership_answer` |
| `GET /settings`, `PATCH /settings` | `{auto_add_purchases}`, default true |
| `GET /closet?status=owned\|incoming` | Status filter. Images still need `include_images=1`. Vectors are never returned |

## Assumptions and choices

- **Git** was already initialized and connected to GitHub, so `git init` was not run again.
- **New signups** get `profile_type: "real"`. Only the seed script creates `demo` and `new` users.
- **The `/analyze` stub** picks the verdict from how many verdicts that user already has (count mod 3), so it keeps cycling after a server restart. `have_this_look` links the user's oldest closet item if one exists. `micro_trend` links the first trend and returns one alternative. `good_buy` returns 30 expected wears, so `cost_per_wear` is price / 30.
- **Money:** `item.price` and `money_kept` are dollars. The placeholder `balances` object uses integer cents (`checking_cents`, `savings_cents`), because the Nessie v2 API uses cents.
- **Nessie:** the current API is v2 at `https://v2-prod-api.nessieisreal.com`. It takes the key as an `Authorization: Bearer` header, not a `?key=` query parameter. This was confirmed from its public OpenAPI spec. The key check calls `GET /accounts?limit=1`.
- **Closet attributes** are checked against `backend/config/attributes.json`. An unknown value returns a 422 error. `fabric_confidence` is a number from 0 to 1.
- **`POST /closet`** needs a name, an image, or both. With no name, the item is called "Untitled item". Images accept bare base64 or a data URL. Phone photo rotation (EXIF) is applied before resizing.
- **`GET /dashboard`** returns the 50 most recent verdicts.
- **`GET /cart-summary`** returns zero counts with `placeholder: true`.
- **Tokens:** the extension stores its JWT in `chrome.storage.local` under `secondLookState`. The dashboard keeps its own JWT in the page's `localStorage`.
- **Seed photos:** HEIC is not supported. Convert iPhone photos to JPEG first.
- **Local MongoDB** works for Part 0. Vector search in a later part needs MongoDB Atlas.
- **Extension icons** are simple placeholders (a green tile with a white ring).

### Part 1

- **Gemini model:** `gemini-3.5-flash` took 16 to 24 seconds per call in testing (one call failed), which cannot fit the 15 second limit. `gemini-3.5-flash-lite` returned the same extraction in about 1 second, so `.env` uses it. Gemini rejects timeouts under 10 seconds, so `/read-page` gives each attempt 10 seconds and retries a malformed reply only when the first attempt came back within 3 seconds (worst case about 13 seconds).
- **`/read-page`** uses only the first 8000 characters of page text. It keeps a Gemini image URL only if that URL appears in the text or was sent by the extension, so the model cannot invent one.
- **Missing price:** instead of checking with no price, the card first asks for one (with a "Check without a price" link). This keeps `money_kept` correct, because `/analyze` stores the price on the verdict.
- **Error state vs paste-a-link:** not logged in, session expired, or server unreachable shows the error state, because pasting a link would not help. Every other failure (no item found, timeout, server error) shows paste-a-link with the reason.
- **Host permission `<all_urls>`:** reading a pasted link with `chrome.scripting.executeScript` needs host access to that site (`activeTab` only covers the tab you clicked from). This adds no new install warning, because the content script already runs on all sites.
- **Background tab load:** waits up to 20 seconds for the page to load, then up to 4 more seconds for product data to appear (single-page apps render late).
- **"Check this item"** works on tabs that were open before the extension loaded: if the page has no content script, the service worker injects it once.
- **Removal matching** only considers items not already marked removed, ignores one-letter words, and stops climbing at 15 ancestors or when a container's text passes 5000 characters (the whole page). The best match at the closest ancestor wins.
- **The Part 0 "Send test item" button** was removed. "Check this item" replaces it.
- **Store name** comes from `og:site_name`, or the hostname without `www.`.
- **SPA staleness:** some single-page stores do not update JSON-LD or meta tags on client-side navigation. If Diagnose shows the previous product, reload the page.

### Part 1 store fixes

- **DOM price** runs only when tier 1 or 2 found a name. On pages with no name, Gemini runs anyway and fills the price.
- **`[data-price]`** is used as-is only when it reads like a price ("49.95" or "$49.95"). A whole number like "4995" might be cents, so the element's visible text is used instead.
- **Currency text** must be short (40 characters or less) and mostly price: after removing the numbers and currency, at most 15 letters may remain. "Sale price $39.90" passes, "Free shipping over $50" does not.
- **"save"** is excluded only as a whole word, so a class like `saved-items` is not affected but a button labelled "Save to wishlist" is.

### Part 2 assumptions

- **Code layout:** the service is `backend/services/item_understanding.py` and the endpoints are in `backend/routers/attributes.py`. These are new folders, because the request asked for a service module and its own router. `/attributes` request and response models are in `backend/models.py` with the other API models.
- **`fabric_confidence` conflict (not changed, needs a decision):** Part 2 returns `"high"`, `"medium"`, or `"low"`, as requested. The Part 0 `Attributes` model (used by `POST /closet` and stored on closet items and verdicts) defines it as a number from 0 to 1. Part 2 does not write to the database, so nothing breaks today, but Part 3 must pick one format before it stores Part 2 output.
- **Values with no "unknown":** `category`, `color_family`, and `cut` have no "unknown" or "other" value in `attributes.json`. For these, the schema allows `null` and the prompt says to use it when unsure. `fabric` uses "unknown" and `style` uses "other". This rule is computed from the file, so no value list is hardcoded.
- **`fabric_confidence` levels:** `high` when the text states the material ("100% cotton"), `medium` when the text only implies it ("denim jacket"), `low` when guessed from the image or with no hint. If `fabric` is "unknown" or null, the confidence is forced to `low`.
- **`is_apparel`:** true for clothing, shoes, bags, and wearable accessories (everything the category list covers). For other items Gemini returns false, and most labels come back null, "unknown", or "other".
- **Main garment:** the prompt uses the product name and breadcrumb to choose which item to label on a model photo (for example "pants" means label the pants, even if a jacket is more visible). The first prompt version labeled the jacket in eval row 2. This rule fixed it.
- **Output checks:** the reply is checked against `attributes.json` on the server too. A value outside the file counts as a malformed reply and triggers the retry. The description is cut to 25 words.
- **Retry:** each Gemini attempt has a 20 second timeout. Any API error or malformed reply is retried once. After 2 failures, every attribute is null, `status` is `"attributes_failed"`, and `attribute_string` is an empty string. `/attributes` still returns HTTP 200 in that case, so callers check `status`.
- **Latency:** with the current `GEMINI_MODEL` (`gemini-3.5-flash-lite`), three image calls to `/attributes` took 1.0 to 1.4 seconds each, image fetch included.
- **`attribute_string`:** uses the requested template. It skips null values and also "unknown" and "other", because those words add noise to embeddings. The period after "style" is kept even when there is no style, for example `"black slim pants. A pair of..."`.
- **Images:** `fetch_image` accepts only http and https URLs, follows redirects, rejects non-image content types and files over 15 MB, resizes to 1024px on the longest side, and returns JPEG bytes. Formats Pillow cannot read (for example SVG) count as a failed fetch, and the call runs text only.
- **Server-side fetch:** the backend downloads any URL a logged-in user sends. That is acceptable for a laptop demo. Do not expose this endpoint publicly without blocking private network addresses.
- **Text limits:** `description` and `text` are each cut to 4000 characters before they go to Gemini.
- **`GET /config/attributes`** needs no login, because the values are not secret.
- **Eval:** the script calls the service directly instead of HTTP. Starter rows start their name with `EXAMPLE:`. They are scored like other rows, and the prefix is stripped before the name goes to Gemini. Both starter rows use public Uniqlo product images. The name "Smart ankle pants" in row 2 was chosen by me to test main-garment selection on an outfit photo. It is not the store's own name for that product. Delete both rows when the real 20 rows are in.
- **Brand:** the prompt forbids naming a brand. The test images show a Uniqlo logo, and no description mentioned it.

### Part 3 assumptions

- **Shared contract changes (all additive, nothing removed):** `ClosetSource` gained `ingest_photo`, `ingest_video`, and `ingest_text`. `ClosetItem` gained `status` (`owned` or `incoming`, and a stored item with no status reads as `owned`). `Attributes.fabric_confidence` now accepts Part 2's `"high" | "medium" | "low"` as well as the old 0 to 1 number. This settles the conflict noted in Part 2. Part 3 stores the strings. `POST /action` takes a `BackgroundTasks` parameter, but its request and response are unchanged.
- **Database:** new collection `ingest_jobs`. New regular indexes: `closet_items (user_id, created_at)`, a unique sparse `closet_items.source_verdict_id` (one auto-add per verdict, even with a double click), and `ingest_jobs (user_id, created_at)`. Closet documents gain `status`, `image_embedding`, and optional `ingest_job_id` or `source_verdict_id`.
- **Embedding model gotcha:** `gemini-embedding-2` merges a list of inputs into ONE vector. It does not return one vector per input like older models. `embed_many` therefore makes one call per text, 8 in parallel. Its vectors already come back at unit length, and the code normalizes them anyway. If the model id fails, `EmbeddingError` is raised. There is no fallback model.
- **What is embedded:** `attribute_string(attributes + description)` from Part 2. An item with no attributes at all (an old `POST /closet` call with only a name) embeds its name instead, so it still gets a vector. Items saved before Part 3, such as seed photos, have `embedding: null` and are not matched until re-added.
- **Scores are cosine:** Atlas reports cosine as `(1 + cos) / 2`. `find_matches` and the duplicate check convert it back to plain cosine, the same number `tune_matching.py` prints, so `thresholds.json` means one thing everywhere.
- **Thresholds** (still placeholders): on the 8 starter pairs, same-look pairs scored 0.81 to 0.93 and different-look pairs scored 0.46 to 0.80. Blue vs black straight jeans scored 0.80, which is why `require_color_match` matters. `is_look_match` skips the color check when either side has no color.
- **`thresholds.json` location:** `backend/config/thresholds.json`, next to `attributes.json`, instead of a new top-level `config/` folder.
- **Extraction model:** uses `GEMINI_MODEL` (`gemini-3.5-flash-lite`). On real photos it found the right garments with tight boxes. A 10-image call takes about 3 to 30 seconds depending on Gemini load. Each call has a 120 second timeout.
- **Busy Gemini:** the model often returned `503 high demand` during testing. Any Gemini call that gets 429 or 503 is retried up to 4 times, after 2, 4, and 8 seconds. Other errors and malformed replies are retried once.
- **`fabric_confidence` and `confidence`:** the extraction uses Part 2's levels for `fabric_confidence`. `confidence` (0 to 1) is the model's own estimate. The page flags anything under 0.6. Changing fabric on the page sets `fabric_confidence` to `high`, because a person chose it.
- **`brand_visible`** is the brand text when a logo or label is readable, otherwise null. It is shown nowhere yet.
- **Pairs:** the prompt treats a pair of shoes (or socks, gloves, earrings) as one item. Without that rule, a worn outfit photo returned one entry per shoe.
- **Merging:** only photo and video candidates go to the merge call. Text items are taken as written, so "two white tees" stays two items. A code guard never merges two candidates from the same image, whatever the model returns. `seen_count` tells how many photos or frames were merged into an item.
- **Video:** frames are sampled at 1 per second. For a video longer than 40 seconds, the rate drops so the 40 frames cover the whole clip instead of only the first 40 seconds. Videos up to 300 MB. Missing ffmpeg returns HTTP 422 with "Run: brew install ffmpeg" before any work starts.
- **Uploads:** photos up to 20 MB each. Unreadable images (including HEIC) are rejected with the file name before the job starts. At most 80 candidates are kept per job.
- **Crops:** an invalid or tiny box (under 12px) falls back to the whole photo, not to no picture. Crops are only shrunk to 600px, never enlarged.
- **Names:** `"<Color_family> <category>"` with the first letter capitalized, for example "White tee". `multi` becomes "Multicolor". The name is editable on the page.
- **Duplicate check** runs on every candidate, including text ones. It flags `likely_duplicate_of {id, name, score}` and never drops anything. If vector search is unavailable, the job still finishes with a warning.
- **Jobs:** images are held in memory and the video in a temp folder until the job ends. A job still running after 20 minutes (for example after a server restart) reads as failed. Saving claims the job first, so a double click cannot save twice.
- **Ownership question:** counts every closet item, owned and incoming. With 10 or more it returns `show: false` without calling Gemini. `staple_idea` starts with "Idea: ". The answer endpoint also accepts an optional `name`.
- **Auto-add** runs after the `/action` response, so the response and its speed do not change. Name and attributes come from the stored verdict. The Part 0 stub stores no attributes, so in that case Part 2 reads the product (name and image) to get them. The product image becomes the thumbnail. If anything fails, the error is logged and nothing is shown. Changing a verdict from bought to skipped later does not remove the item.
- **Review page:** `dashboard/closet.html`, `closet.css`, and `closet.js`, served at `/closet-ui`, with assets under `/dashboard-assets/`. It shares the dashboard's login token. Polished with the `better-ui` skill, and checked at 1280px (light) and 390px (dark) with Playwright.
- **Testing so far:** on Atlas, `create_indexes.py` built `closet_vector` (READY after about 30 seconds, and safe to run again). Ingesting 2 real garment photos plus "two white tees and black jeans" gave 8 candidates and saved 8. `find_matches` ran, and a bought action added 1 incoming item. The video path, double save, settings, ownership, and parse-text were tested earlier against local MongoDB. `seed/photos/` is still empty, so the acceptance photos were 2 public Uniqlo product images, not the seed photos.
- **`duplicate_similarity` 0.93 is too strict (measured, not changed):** the same tee photographed twice got two slightly different Gemini descriptions. Their cosine was 0.8585 (Atlas score 0.9292), so it was not flagged as a duplicate, although `find_matches` ranked it first and `is_look_match` was true. Tune it with `tune_matching.py` on real re-photographed pairs before relying on duplicate flags.

### Part 4 assumptions

- **Search provider:** call (a) uses Claude web search, not Gemini Google Search grounding. Both Gemini keys tried returned `429 RESOURCE_EXHAUSTED` on every grounded call while plain calls worked. Call (b) still uses Gemini.
- **Web search tool:** `web_search_20260318` (the newest version in the Anthropic docs, Nov 2026) with `allowed_callers: ["direct"]`, so Claude calls search itself and every result and citation comes back as a plain content block. Web search costs $10 per 1,000 searches plus tokens; one run makes at most 5. If an org admin has turned web search off, the API returns a 400 and the script stops with a message saying so.
- **Sources:** the S-id list is the set of URLs Claude cited in its report, in order of first citation. Search results that Claude read but did not cite are counted, not listed.
- **Agent models:** call (a) uses `CLAUDE_MODEL` (`claude-haiku-5-5` in `.env`). Call (b) uses `gemini-3.5-flash` by default, not the `GEMINI_MODEL` used by `/read-page`, because the agent runs offline and quality matters more than speed. Override it with `--model` or `TREND_AGENT_MODEL`.
- **Lifespan rule:** a trend stores `lifespan_source_url` next to `lifespan_weeks_est` and `lifespan_bucket`. Either one may be set only when that URL is one of the trend's sources. The seed script rejects a number or bucket without it. The agent drops an uncited one to null and keeps the label.
- **lifespan_bucket:** `short` is up to 16 weeks (about one season), `medium` up to 52 weeks, `long` more than a year. When `lifespan_weeks_est` is set, the bucket must match it (the seed script fills it in if it is missing).
- **Direction conflicts** are judged by the structuring model (`lifespan_direction_conflict`); the code then sets the number and bucket to null.
- **Feature dedupe** compares exact values, nulls included. Two trends in the same category with no cut, color, or fabric count as duplicates.
- **Recency:** a source is too old when its date is more than 365 days before the run date. `page_age` text such as "April 30, 2025", "2025-04-30", or "3 weeks ago" is parsed. Text that does not parse counts as no date.
- **Source titles** for agent trends are the page titles from Claude's citations.
- **Features and category:** the agent uses only values from `backend/config/attributes.json` (color uses `color_family`), so Part 5 can match them to item attributes. Seed trends may use other words; the seed script prints a note for each one but loads it.
- **first_seen:** for seed trends, the date in the file, or the date first loaded if the file has none. For agent trends, the date of the run that found the trend.
- **search_suggestions_html:** null for agent trends from Claude web search, and never set on seed trends. The field stays in the model in case Gemini grounding is used again.
- **Example images:** the agent adds none, because web search returns no image URLs.
- **Seed overrides agent:** if a seed trend has the same name as an agent trend, the seed run replaces it, sets `origin: "seed"`, and removes `search_suggestions_html`.
- **Part 0 placeholder trends:** `seed/seed.py` no longer creates the three `PLACEHOLDER trend` documents. `seed_trends.py` deletes them if they exist, so they never reach the verdict prompt.
- **Trend model:** `sources` is now `[{title, url}]`, `alternatives` is `[{type, why_it_lasts, where_to_look, url}]`, and `lifespan_weeks_est` is a number that may have decimals. The `/analyze` stub's `matched_trend` uses this shape.
