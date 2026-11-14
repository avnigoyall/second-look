# Second Look

A Chrome extension that checks an item a shopper adds to an online cart against their closet and a trend list, then shows one of three verdicts: `have_this_look`, `micro_trend`, or `good_buy`.

This is **Part 0: Foundation**. The API, database, auth, extension messaging, and dashboard work end to end. Every verdict is a stub with canned data. There is no AI, vector search, Nessie transfer, or trend logic yet.

## Layout

| Folder | What it is |
| --- | --- |
| `backend/` | FastAPI app (Python 3.10+). Allowed attribute values live in `backend/config/attributes.json` |
| `extension/` | Chrome Manifest V3 extension, plain JavaScript, no build step |
| `dashboard/` | One static page, served by the backend at `/dashboard-ui` |
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
| `CLAUDE_MODEL` | Model id for the Claude key check. Use a small, cheap model |
| `GEMINI_MODEL` | Model id for the Gemini key checks |

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

### Test the popup flow by hand

1. Open a normal website in a new tab, for example https://example.com. The toast cannot appear on `chrome://` pages, the Chrome Web Store, or tabs that were open before you loaded the extension (reload those first).
2. Click the Second Look icon. Log in with `demo@secondlook.test` / `demo1234`.
3. The popup shows "Logged in as demo@secondlook.test" and a **Send test item** button.
4. Click **Send test item**. A toast appears in the bottom right corner of the page with the verdict, the reason, and four buttons.
5. Click **Skip it**. The toast says "Skipped. Saved." Click thumbs up. The toast confirms the feedback.
6. Click **Send test item** two more times. The verdict cycles: You have this look, Micro trend, Good buy.
7. Open http://localhost:8000/dashboard-ui and log in as the same user. "Money kept by skipping" shows $59.50 for each skipped test item, and the verdict list shows your actions and feedback.

If something fails, the popup or toast shows the error. Common causes: the backend is not running, or the tab needs a reload.

## How the extension talks to the backend

Content scripts on https pages cannot call `http://localhost`, so every backend call goes through the service worker (`extension/src/background/index.js`). The popup and content script send it messages: `LOGIN`, `LOGOUT`, `GET_STATE`, `SEND_TEST_ITEM`, `ANALYZE_ITEM`, `SEND_ACTION`, `SEND_FEEDBACK`. The service worker tells the page to draw the toast with `SHOW_VERDICT`. The toast renders inside a Shadow DOM so the store's CSS cannot change it.

`shadowHost.js` and the storage pattern in `src/shared/state.js` are adapted from the earlier Syft extension.

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
