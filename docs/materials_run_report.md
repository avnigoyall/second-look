# Photo material estimate: run report

Branch `feat/materials`, worktree `../phacks-materials`. Run on Nov 14, 2026, 22:16 to 22:45 America/New_York.

## Status

| Step | Status | Evidence |
|---|---|---|
| 1. Load the model | done | FashionCLIP loads lazily on first use. Numbers below |
| 2. Estimator | done | `backend/services/materials_photo.py`, flag `MATERIALS_PHOTO_ENABLED` (default off) |
| 3. item_materials store | done | `backend/services/item_materials_store.py`, offline tests pass |
| 4. Stage 1 (10 items) | done, on other retailers | Uniqlo and Zara block automated access (details below). At the user's request, 19 Shopify retailers that allow it were used. 7/10 correct |
| 5. Stage 2 (30 items) | done | `docs/eval_materials.md`: 17/30 correct (56.7%), 4/30 unknown |

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

## Stage 1 results (10 items, run_id `stage1-seed7`)

Source change: Uniqlo and Zara block automated access (above). At the user's request other retailers were tried. 19 Shopify apparel retailers allow `/products.json` in robots.txt and served it; their public product JSON gives the product photo and the description with the stated composition. Probed and not used: rhone.com and sezane.com (robots.txt 403), bombas.com (robots.txt 429), unitedbyblue.com (products.json 403), buckmason.com, quince.com, asket.com, vuoriclothing.com (products.json 404), allbirds.com, cuyana.com, marinelayer.com, rouje.com, nisolo.com (no parseable percentage composition), wearpact.com (non-JSON response).

Sample: seeded (seed 7) stratified random draw, round-robin across retailers, plan cotton_denim 2, wool_knit 2, synthetic 2, linen 2, silk_satin 1, leather_or_faux 1. Non-wearable product types (home, gift, ornament and similar) are left out before sampling; that rule was added after the first draw picked a dog ornament, and the sample was redrawn with the same seed before any scan. The estimator saw only the first product photo (640 px wide).

| # | Retailer | Product | Stated composition (main fabric) | Stated group | Estimate | Score | Margin | Confidence | Top-1 ignoring thresholds | Correct |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | lunya.co | [The Graphic Tee LUNYA x LACMA](https://lunya.co/products/lunya-lacma-graphic-tee) | 100% cotton | cotton_denim | cotton_denim | 0.694 | 0.517 | low | cotton_denim | yes |
| 2 | colorfulstandard.com | [Organic Oversized Hood - Oyster Grey](https://colorfulstandard.com/products/organic-oversized-hood-oyster-grey-female) | 100% cotton | cotton_denim | synthetic | 0.703 | 0.488 | medium | synthetic | no |
| 3 | aimeleondore.com | [Long-Sleeve Merino Polo](https://aimeleondore.com/products/long-sleeve-merino-polo-1) | 100% wool | wool_knit | wool_knit | 0.999 | 0.998 | medium | wool_knit | yes |
| 4 | naadam.co | [Super Luxe Cashmere Double Faced Utility Jacket](https://naadam.co/products/super-luxe-cashmere-double-faced-utility-jacket) | 100% cashmere | wool_knit | wool_knit | 0.942 | 0.920 | medium | wool_knit | yes |
| 5 | everlane.com | [The ReNew 13" Transit Backpack \| Navy Blazer](https://everlane.com/products/u-bpck-nyle-flp-lg-nvy) | 100% polyester | synthetic | unknown | 0.528 | 0.087 | - | cotton_denim | unknown |
| 6 | ministryofsupply.com | [Men's AeroZero Dress Shirt - Chambray Blue (MPS)](https://ministryofsupply.com/products/mens-aerozero-dress-shirt-chambray-blue-mps) | 100% polyester | synthetic | cotton_denim | 0.743 | 0.518 | medium | cotton_denim | no |
| 7 | magiclinen.com | [Women's waffle robe in Thin striped](https://magiclinen.com/products/womens-waffle-robe-in-thin-striped) | 53% linen, 47% cotton | linen | linen | 0.915 | 0.853 | medium | linen | yes |
| 8 | frankandoak.com | [LINEN SHIRT - SHORT SLEEVE](https://frankandoak.com/products/mens-woven-shirt-black-2mwc006fe-bla) | 100% linen | linen | linen | 0.746 | 0.507 | medium | linen | yes |
| 9 | christydawn.com | [The Helen Bias Skirt \| Emerald Jacquard](https://christydawn.com/products/the-helen-bias-skirt-emerald-jacquard) | 100% silk | silk_satin | silk_satin | 0.985 | 0.976 | medium | silk_satin | yes |
| 10 | fahertybrand.com | [Outpost Leather Belt - Multi](https://fahertybrand.com/products/outpost-leather-belt-multi) | 100% leather | leather_or_faux | leather_or_faux | 0.491 | 0.246 | low | leather_or_faux | yes |

- n = 10 (status ok, unambiguous stated group)
- Accuracy (unknown counts as wrong): 7/10 = 70.0%
- Unknown rate: 1/10 = 10.0%
- Accuracy on answered items: 7/9 = 77.8%
- Top-1 group ignoring thresholds: 7/10 = 70.0%
- Majority-class baseline (always 'cotton_denim'): 20.0%
- Per-image latency, warm: median 16.0 ms, mean 19.6 ms
- Per-class counts: {'cotton_denim': 2, 'wool_knit': 2, 'synthetic': 2, 'linen': 2, 'silk_satin': 1, 'leather_or_faux': 1}

| Stated \ Estimate | cotton_denim | wool_knit | synthetic | leather_or_faux | silk_satin | linen | unknown |
|---|---|---|---|---|---|---|---|
| cotton_denim | 1 | 0 | 1 | 0 | 0 | 0 | 0 |
| wool_knit | 0 | 2 | 0 | 0 | 0 | 0 | 0 |
| synthetic | 1 | 0 | 0 | 0 | 0 | 0 | 1 |
| leather_or_faux | 0 | 0 | 0 | 1 | 0 | 0 | 0 |
| silk_satin | 0 | 0 | 0 | 0 | 1 | 0 | 0 |
| linen | 0 | 0 | 0 | 0 | 0 | 2 | 0 |

**n = 10 cannot support a general accuracy claim.** One item changes accuracy by 10 points. The sample is stratified, so the 20% majority-class baseline is far below what always guessing cotton would score on a real shopping mix (cotton_denim is 1,083 of 1,808 unambiguous candidates in the pool, 59.9%).

Misses: a cotton hoodie read as synthetic (0.703, medium), a polyester dress shirt read as cotton_denim (0.743, medium), a nylon backpack returned unknown (margin 0.087). The two medium-confidence misses show that medium confidence is not reliable at these provisional thresholds.

## Stage 2 results (30 items, run_ids `stage1-seed7` and `stage2-seed8`)

Full tables: `docs/eval_materials.md` (generated from the stored documents, no rescans).

Before stage 2 was scanned, the draw was corrected three times. No item had been scanned at that point, and every fix is a general rule, not an item-specific edit:
1. A suede jacket was labeled synthetic because its only percentage was "100% polyester taffeta yoke and sleeve lining". New rule in `stated_group`: drop unlabeled clauses that mention lining, liner, yoke, pocket or trim. No stage 1 label changed.
2. A decorative bow and a table runner were drawn. More non-wearable product types were added to the filter (decor, table, napkin, kitchen, curtain, sheet, duvet, rug). No stage 1 item matches.
3. One pair of gloves was drawn twice (two colorways). Products are now deduplicated by retailer and title before sampling.

| Measure | Value |
|---|---|
| n | 30 (5 per group) |
| Accuracy, unknown counts as wrong | 17/30 = 56.7% |
| Unknown rate | 4/30 = 13.3% |
| Accuracy on answered items | 17/26 = 65.4% |
| Correct when confidence is medium | 14/21 = 66.7% |
| Correct when confidence is low | 3/5 |
| Majority-class baseline, this stratified sample | 16.7% |
| Majority-class share in the candidate pool (cotton_denim) | 1,083 of 1,808 = 59.9% (from the stage 1 pool) |
| Per-image latency, warm | median 17.2 ms, mean 21.1 ms |

Per group: wool_knit 5/5, linen 4/5, leather_or_faux 4/5, silk_satin 2/5 (2 unknown), cotton_denim 1/5, synthetic 1/5.

Observed pattern: the model labels textured or knit-looking items wool_knit whatever their fiber. A cotton cocoon jacket, a cotton rugby polo, a polyester fleece jacket and a nylon-blend sock were all wool_knit at medium confidence. Cotton and synthetic, the two groups that matter most in a real shopping mix, are the weakest (1/5 each).

## Conclusion and recommendation

- Accuracy (56.7%) is above the 16.7% baseline of this sample, but that baseline is low because the sample is balanced by design. It is **not** above the 59.9% that always answering cotton_denim would score on the candidate pool's real mix. On cotton and synthetic items, the most common ones, it got 2 of 10 right.
- Medium confidence was wrong 7 of 21 times at the provisional thresholds, so a "medium confidence" line would mislead users about a third of the time.
- Recommendation: keep `MATERIALS_PHOTO_ENABLED` off for the demo. It is off by default, and no demo config turns it on. If it is shown at all, show only `wool_knit`, `linen` and `leather_or_faux` estimates and always say "low confidence". That is a guess from 30 items, not a measured rule.
- n = 30 from 19 mostly US Shopify brands, coarse groups and fiber-based labels: this cannot support a general accuracy claim.
