# Second Look over iMessage (Photon)

A shopper texts a photo of a clothing item to the agent's iMessage number. The agent replies with:

1. What the item is, its likely material and a quality read (Claude vision).
2. Whether it looks like something already in their closet (Gemini image embeddings + MongoDB Atlas Vector Search).
3. Whether it matches a micro trend from the curated trend library, and the trend's stage.

It then asks "Do you already own this?". A "yes" adds the item to that person's closet.

This folder is a standalone Node.js app. It does not depend on `backend/`.

## Flow

```
iMessage photo -> Photon -> poll.mjs -> handle-message.mjs
  -> image.mjs      convert HEIC/AVIF/WebP to JPEG, resize
  -> analyze.mjs    Claude: attributes JSON + bounding box of the garment
  -> embed.mjs      Gemini embedding of the cropped garment (1536 dims)
  -> db.mjs         $vectorSearch: owned closet items + trend examples
  -> respond.mjs    Claude writes the reply from attributes + candidates
  -> photon.mjs     send reply
```

## Setup

Requires Node.js 24+.

1. Set the variables in `.env.example` in your shell (for example in `~/.bash_profile`). Do not commit real values.
2. Install dependencies and create the MongoDB indexes:

   ```bash
   cd imessage
   npm install
   node scripts/setup-mongo.mjs
   ```

3. In the Photon dashboard, add the iMessage number you will text from and assign a shared-pool number to the project. Text the agent first. A shared line cannot send the first message.

## Run

```bash
node src/poll.mjs                         # the agent: polls Photon every 3 s
node src/analyze.mjs photo.jpg            # Claude analysis of one local photo
node src/embed.mjs photo.jpg              # Gemini embedding of one local photo
node test-imessage.mjs "hello"            # send one test message
```

## Trend library

```bash
node scripts/add-trend.mjs "Barrel-leg jeans" peak "Curved, cocoon-shaped denim" a.jpg b.jpg
```

Stages: `emerging`, `peak`, `fading`, `staple`. Optional env: `TREND_CATEGORY`, `TREND_STARTED`, `TREND_PEAKED` (YYYY-MM-DD).

## Maintenance

- `node scripts/reembed.mjs --all` re-embeds every item and trend example. Run it after changing `EMBED_MODEL` or `EMBED_DIMS`, after `node scripts/setup-mongo.mjs` (which resizes the vector indexes).

## Data (MongoDB database `second_look`)

- `items`: one document per texted photo. `status` is `pending`, `owned` or `considered`. Holds `attributes`, `image_embedding`, `text_embedding`, `photo_id`.
- `trends`, `trend_examples`: the curated micro-trend library.
- GridFS bucket `photos`: the original photos.

## Known limits

- Polling is for local development. Production should use a Photon webhook.
- Matching scores are not calibrated on a labeled set. Claude makes the final same/similar call from the top 3 candidates.
