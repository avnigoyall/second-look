# Second Look

A Chrome extension that checks an item a shopper adds to an online cart against their closet and a trend list, then shows one of three verdicts: `have_this_look`, `micro_trend`, or `good_buy`.

Built so far:

- **Part 0: Foundation.** API, database, auth, extension messaging, and dashboard. Verdicts are still stubs with canned data.
- **Part 1: Capture.** The extension reads the product a shopper adds to their cart, shows the verdict card on the page, tracks the items checked in this session, and notices when one is removed from the cart.

Not built yet: real verdict logic, closet matching, Nessie, trend logic, and the cart summary UI.

## Layout

| Folder | What it is |
| --- | --- |
| `backend/` | FastAPI app (Python 3.10+). Allowed attribute values live in `backend/config/attributes.json` |
| `extension/` | Chrome Manifest V3 extension, plain JavaScript, no build step |
| `dashboard/` | One static page, served by the backend at `/dashboard-ui` |
| `docs/` | `store-test-results.md`, the table for testing real stores by hand |
| `seed/` | `seed.py` plus a `photos/` folder for the demo user's closet |
| `scripts/` | `check_keys.py` checks each external service key |

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
3. If the name or image is still missing, or only the price is missing, `readItem.js` sends the trimmed visible page text (up to 8000 characters) and an image URL to `POST /read-page`, which asks Gemini for `{name, price, image_url}`.
4. If no name is found, the card shows paste-a-link.

`price.js` parses prices with currency symbols or codes, thousands separators, decimal commas (`€39,90`), and ranges or "was / now" text (the lowest number wins). If no price is found, the card asks for it before checking.

**Triggering.** `trigger.js` attaches one capture-phase click listener to the document, so it keeps working after single-page-app navigation and runs before the store's own handler without blocking it. It matches buttons and links whose text, `aria-label`, `title`, `value`, or `data-testid` match `add (to) (shopping) bag|cart|basket`. One check per URL every 3 seconds. Nothing runs on page load.

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
