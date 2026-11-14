# Second Look

A Chrome extension that checks an item a shopper adds to an online cart against their closet and a trend list, then shows one of three verdicts: `have_this_look`, `micro_trend`, or `good_buy`.

Built so far:

- **Part 0: Foundation.** API, database, auth, extension messaging, and dashboard. Verdicts are still stubs with canned data.
- **Part 1: Capture.** The extension reads the product a shopper adds to their cart, shows the verdict card on the page, tracks the items checked in this session, and notices when one is removed from the cart.
- **Part 2: Item understanding.** `POST /attributes` sends one item (image plus text) to Gemini and returns attributes from `attributes.json`, plus the `attribute_string` that Part 3 embeds.

Not built yet: embeddings, real verdict logic, closet matching, Nessie, trend logic, and the cart summary UI.

## Layout

| Folder | What it is |
| --- | --- |
| `backend/` | FastAPI app (Python 3.10+). Allowed attribute values live in `backend/config/attributes.json` |
| `extension/` | Chrome Manifest V3 extension, plain JavaScript, no build step |
| `dashboard/` | One static page, served by the backend at `/dashboard-ui` |
| `docs/` | `store-test-results.md`, the table for testing real stores by hand |
| `seed/` | `seed.py` plus a `photos/` folder for the demo user's closet |
| `scripts/` | `check_keys.py` checks each external service key. `eval_attributes.py` scores Part 2 |
| `eval/` | `attributes_eval.csv`, labeled items for the Part 2 eval |

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

## Run

All commands run from the project root with the virtual environment active.

```bash
python seed/seed.py                          # safe to run again
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

## Tests

```bash
node extension/tests/price.test.js       # price parser unit tests
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
