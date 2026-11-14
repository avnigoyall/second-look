# Second Look over iMessage (Photon)

A shopper texts a photo of a clothing item to the agent's iMessage number. The agent sends the photo to the
Second Look backend, which runs the same verdict engine as the extension (`decide` in
`backend/services/verdict.py`), and texts the verdict back: `have_this_look`, `micro_trend` or `good_buy`.

Any other text ("what jeans do I have?", "how much have I saved?", "what did you say about that dress?") is
answered from that shopper's own account: closet, money kept and CO2e, Nessie balances when linked, the last
10 verdicts, and the last 8 iMessage turns, so follow-ups work. Every turn of a linked phone is stored per
account in `imessage_messages`.

This folder holds only the iMessage channel. All shopping logic (attributes, closet matching, trends,
verdicts) lives in `backend/`. The agent talks to it through `backend/routers/imessage.py`.

## Flow

```
iMessage photo -> Photon -> poll.mjs -> handle-message.mjs
  -> image.mjs        HEIC/AVIF/WebP -> JPEG (the backend's Pillow cannot read HEIC or AVIF)
  -> backend.mjs      POST /imessage/analyze {phone, image_base64, text}
       backend        phone -> users.phone -> decide(user, item) -> verdict doc (channel "imessage")
  -> photon.mjs       text back `message`, plus "Do you already own this?" when ask_ownership is true
"yes" / "no"  ->      POST /imessage/own  -> closet_items (source ownership_answer, image from target_crop)
an email      ->      POST /imessage/link -> sets users.phone for that account
any other text ->     POST /imessage/ask {phone, text} -> {message}, Claude answering from this account only
```

Routing lives in `src/route.mjs` (pure, tested by `npm test`, no install needed):

- attachment: photo flow (the caption rides along).
- email: link.
- yes/no answer: `/imessage/own`, but only when the text is short (6 words or fewer) and has no `?`. So
  "yes", "nope, I don't" answer the ownership question, while "no, what about jeans?" is a question.
- anything else, including "hi" and "help": `/imessage/ask`. The fixed HELP reply is gone; `/ask` explains
  what it can do when a question is out of scope.
- unlinked phone (404 `phone_not_linked`) on any of these: the link prompt.

`/imessage/ask` (`backend/services/account_context.py` builds the context, `backend/services/imessage_ask.py`
calls Claude): plain text, at most about 3 sentences, never invents items or numbers, money in dollars. One
Claude call with an 8 s timeout; any failure answers a friendly fallback message, never a 5xx. Measured
about 1 to 2.5 s.

A phone must be linked to a Second Look account once, in either of two ways:

- At sign-up: the extension popup's "Create an account" form has an optional phone field. `POST /auth/signup`
  stores it as E.164 (`backend/phone.py`), the same form Photon reports for the sender, so it links right away.
- Over iMessage: the first photo from an unknown number gets "reply with the email you use for Second Look".
  This path trusts the texter (fine for the demo, see Known limits).

## Setup

Requires Node.js 24+ and the backend (see the root README).

1. Root `.env` (shared with the backend) needs, besides the backend's own keys:
   - `IMESSAGE_AGENT_SECRET`: any long random string. The backend and the agent must use the same value.
   - `GEMINI_MODEL` and `CLAUDE_MODEL`: the backend needs both for verdicts (team values: `gemini-3.5-flash-lite`, `claude-haiku-5-5`).
2. Photon variables from `imessage/.env.example`, in your shell or the root `.env`. The backend reads
   `PHOTON_PROJECT_ID` and `PHOTON_API_KEY` too, to assign lines.
3. Lines are assigned automatically. When an account has a phone (set at sign-up, or with "Connect iMessage"
   in the popup), the backend asks Photon for a shared line for that phone
   (`POST /v1/projects/{id}/platforms/imessage/assignments`, one seat each) and the popup shows
   "Text Second Look at <line> to start". The shopper must text that line first; a shared line cannot send the
   first message. Each shopper may get a different line.
   Manual fallback: in the Photon dashboard, add the number you will text from and assign it a shared-pool line,
   then text that line.

The agent replies from the line that received each message (`receivedBy.id` on the Photon message, see
`replySenderId` in `src/route.mjs`). `PHOTON_AGENT_HANDLE` is only the fallback sender when Photon leaves
`receivedBy` out, and the line `test-imessage.mjs` sends from.

```bash
cd imessage && npm install
```

## Run

Two processes, from the repo root:

```bash
# 1. backend (on macOS with the python.org Python, point it at certifi's CA bundle or Atlas TLS fails)
SSL_CERT_FILE="$(.venv/bin/python -m certifi)" .venv/bin/uvicorn backend.main:app --port 8000

# 2. agent
cd imessage && node src/poll.mjs
```

`SECOND_LOOK_API` (default `http://localhost:8000`) points the agent at another backend.
`node test-imessage.mjs "hello"` sends one test message.

## Backend additions (for CONTRACT.md)

- `backend/routers/imessage.py`: `POST /imessage/link`, `/imessage/analyze`, `/imessage/own`, `/imessage/ask`.
  Header `X-Agent-Secret`.
- `imessage_messages`: `{user_id, phone, direction in|out, kind text|photo|verdict|ownership|link|answer, text
  (max 2000 chars), verdict_id?, created_at}`, index `(user_id, created_at desc)`. Written by link (on success),
  analyze, own and ask for linked phones only. No image bytes.
- `users.phone`: optional, E.164, unique sparse index (created in `ensure_indexes()`).
- `POST /auth/signup` takes optional `phone` (any common format, 10 digits read as US). 422 when it is not a
  phone number, 409 "This phone number is already linked to another account". `User` responses gain `phone`.
- Extension popup: "Create an account" form (email, password, optional phone); background handler `SIGNUP`;
  `phone` kept in the stored login state.
- Verdict docs from iMessage get `channel: "imessage"` and `ownership: "yes" | "no"` after the answer.
  `item.image_url` is set to null after saving (the photo is kept as `target_crop`).
- `fetch_image()` also accepts `data:image/...` URLs.
- `python-multipart` added to `backend/requirements.txt` (the closet router needs it; it was missing).
- Automatic lines (`backend/services/photon_lines.py`): `POST /imessage/connect {phone?}` and
  `GET /imessage/status` (JWT, for the extension) return `{status: none|assigning|pending_optin|connected|error,
  line, phone, message}`. Signup with a phone assigns in a background task after the response. Users gain
  `photon_assignment_id`, `photon_line`, `photon_handle`, `photon_opt_in`, `photon_assigned_at`,
  `photon_checked_at`, `photon_error`, `photon_assigning_at`; `User` responses gain `imessage_line`.

## Known limits

- Polling is for local development. Production should use a Photon webhook.
- Linking a phone trusts the texter to name their own email. Replace with a confirmation code before real users.
- A closet item added by "yes" takes a few seconds to appear in Atlas Vector Search. A photo of the same
  item sent right after can miss it.
