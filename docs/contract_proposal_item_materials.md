# Contract proposal: `item_materials` collection

Status: proposal for CONTRACT.md. Not merged. CONTRACT.md was not edited.

## Purpose

One document per scanned product, written by the photo material estimate work (`backend/services/item_materials_store.py`). It holds the stated composition (eval label) and the photo estimate so results can be evaluated without rescanning.

It is not read by the verdict engine, the dashboard or the extension.

## Index

- `item_key` unique (`item_key_unique`). Created by the store on its first write, not in `backend/db.py`.

## Document

| Field | Type | Notes |
|---|---|---|
| `item_key` | string | sha256 hex of the canonical product URL (lowercase scheme and host, no query, no fragment, no trailing slash). sha256 of the image bytes when there is no URL |
| `source` | string | `"uniqlo_page"` for this work |
| `source_url` | string or null | Product page URL |
| `stated_composition` | string or null | Composition text from the page |
| `stated_group` | string or null | Group from `config/materials_labels.json` `composition_mapping`, or `"ambiguous"` |
| `estimate` | object | `{group, score, margin, confidence}`. `group` is one of the 6 groups or `"unknown"`. `confidence` is `"low"`, `"medium"` or null |
| `model_id` | string | `patrickjohncyh/fashion-clip` |
| `labels_config_hash` | string | sha256 of model id plus groups and prompts |
| `thresholds_hash` | string | sha256 of the thresholds |
| `scanned_at` | datetime (UTC) | |
| `flags` | object | `{hand_labeled: bool, composition_hidden_for_eval: bool}` |
| `run_id` | string | |
| `status` | string | `"ok"`, `"model_unavailable"` or `"image_fetch_failed"` |

Image bytes are never stored. Failed writes go to `.social_runs/item_materials.jsonl` (git-ignored) and are synced at the next start.

## Rules

- Written only by offline scan and eval scripts. No API route writes to it.
- The estimate never changes a verdict and never enters a numeric rule.
