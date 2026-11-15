# Photo material estimate: run report

Branch `feat/materials`, worktree `../phacks-materials`. Run on Nov 14, 2026, 22:16 to 22:23 America/New_York.

## Status

| Step | Status | Evidence |
|---|---|---|
| 1. Load the model | done | FashionCLIP loads lazily on first use. Numbers below |
| 2. Estimator | done | `backend/services/materials_photo.py`, flag `MATERIALS_PHOTO_ENABLED` (default off) |
| 3. item_materials store | done | `backend/services/item_materials_store.py`, offline tests pass |
| 4. Uniqlo stage 1 | **stopped: blocked** | Uniqlo resets the connection before any HTTP response, including robots.txt. Details below |
| 5. Stage 2 | not started | Depends on stage 1 |

## Step 1: model measurements

Machine: Apple M4 Pro. Python 3.13.3, torch 2.15.0, transformers 5.23.0, in a separate venv in the worktree (`.venv`, git-ignored). The main tree's venv was not changed.

| Measure | Value |
|---|---|
| Model | `patrickjohncyh/fashion-clip` |
| Device | `mps` |
| Download | `model.safetensors` 605.2 MB, plus tokenizer and config files about 3.6 MB |
| First load, including download | 25.45 s |
| Load from local cache | 2.89 s |
| Per-image latency, warm | median 11.0 ms, mean 11.1 ms, min 10.7 ms, max 12.8 ms, over 21 calls (7 images x 3) |

The latency images are the 7 local test fixtures in `extension/tests/e2e/fixtures/img/`. They have no stated composition, so they are a latency check only, not an accuracy check. Their outputs, for the record: black.jpg cotton_denim (0.47, low), cardigan.jpg cotton_denim (0.54, low), jeans.jpg cotton_denim (0.92, medium), navy.jpg cotton_denim (0.78, medium), og.jpg unknown (0.44), shirt.jpg cotton_denim (0.55, low), white.jpg cotton_denim (0.76, medium). The cardigan labeled cotton_denim suggests the estimator leans toward cotton_denim. That is one observation, not a measurement.

The Hugging Face download did not fail. A failed download returns group `unknown` with status `model_unavailable` and the exact error (covered by `ModelUnavailableTests`).

## Step 2 and 3: what was built

- `config/materials_labels.json`: 6 groups with 5 prompts each, the composition-to-group rule (majority fiber above 50 percent, using the existing `backend/services/composition.py` parser) and the ambiguous cases. Thresholds are marked PROVISIONAL: `min_score` 0.45, `min_margin` 0.15, `medium_score` 0.70, `medium_margin` 0.35. They are placeholders chosen before any measurement.
- Scoring: text embeddings are normalized, averaged per group and normalized again. The image embedding times `logit_scale`, softmax over the 6 groups. `score` is the top probability, `margin` is top minus second. Confidence is at most `medium`.
- `estimate_for_item` returns None without calling the model when the flag is off, when `materials` text exists or when the description states a composition. `photo_line` produces the single line, for example "Estimated from photo: likely wool or knit, low confidence". Nothing in verdict code calls this module.
- `item_materials` store: unique index on `item_key`, upsert per item, resume rule, and jsonl fallback at `.social_runs/item_materials.jsonl` (git-ignored). No document has been written to Mongo yet. The collection and its index are created on the first real upsert. See `docs/contract_proposal_item_materials.md`.
- `requirements-agent.txt` (new, repo root): `torch==2.15.0`, `transformers==5.23.0`. The spec named this file but it did not exist, so it was created.

Tests: `MONGODB_URI=mongodb://127.0.0.1:27017 NESSIE_API_KEY='' SECONDLOOK_WARMUP=0 SOCIAL_RADAR_ENABLED=0 .venv/bin/python -m unittest discover tests` gives `Ran 460 tests ... OK` (440 existing plus 20 new). The 20 new tests also pass in the main tree's venv, which has no torch.

## Step 4: why it stopped

Every request to `www.uniqlo.com` is reset by the server before it sends any HTTP status. This includes `robots.txt`, so the robots rules cannot be read and honored. User agent `Mozilla/5.0 (compatible; research-bot)`, at least 1 s between requests. 6 requests in total (5 to robots.txt, 1 to the home page), all failed the same way. Representative rows:

| URL | Protocol | Result |
|---|---|---|
| `https://www.uniqlo.com/robots.txt` | HTTP/2 | curl exit 92, `HTTP/2 stream 1 was not closed cleanly: INTERNAL_ERROR (err 2)`, no headers |
| `https://www.uniqlo.com/robots.txt` | HTTP/1.1 | curl exit 56 (connection reset), no headers |
| `https://www.uniqlo.com/robots.txt` | HTTP/2 | curl exit 92 |
| `https://www.uniqlo.com/us/en/` | HTTP/2 | curl exit 92 |

TLS completes (certificate `CN=www.uniqlo.com`, Fast Retailing), then the edge server drops the stream. This pattern is typical of bot protection that rejects non-browser clients. Per the run rules it was not worked around: no browser user agent, no headless browser, no third-party mirror or API. No items were collected and no labels were invented.

## Zara attempt (Nov 14, 22:25 America/New_York)

Tried at the user's request, with the same rules (same user agent, at least 1 s between requests, no workaround). 2 requests, both blocked by the Akamai edge server with an "Access Denied" page:

| URL | Result |
|---|---|
| `https://www.zara.com/robots.txt` | HTTP 403, "Access Denied" (Akamai reference page from errors.edgesuite.net) |
| `https://www.zara.com/us/en/` | HTTP 403, "Access Denied" |

robots.txt cannot be read, so no Zara items were collected.

## What is needed to continue

One of these:

1. 10 to 30 Uniqlo product page URLs that you opened in a browser, each with the composition text copied from the page, and the product image saved locally. Put them in a folder under `.social_runs/` (git-ignored) with a small JSON manifest: `{url, image_path, stated_composition}` per item.
2. Permission to use a different public retailer whose robots.txt allows product pages.
3. Permission to drive a real browser (Playwright) on Uniqlo pages at 1 request per second. This is closer to a workaround of their bot protection, so it needs your explicit decision.

With option 1, the scan uses the store's resume and fallback rules, and stage 1 can run in a few minutes.
