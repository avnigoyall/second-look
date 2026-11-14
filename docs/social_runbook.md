# Social Trend Radar

Bluesky Radar adds sampled clothing chatter to purchase analysis. It preserves the existing verdict rules and adds no bank calls. Counts describe search index samples, not platform totals. The first post in the 12-month observation window is not an origin date.

## Setup

Run commands from the repository root on feat/social-radar. Keep the current local .env file private. The required configuration is MONGODB_URI, GEMINI_API_KEY, ANTHROPIC_API_KEY and CLAUDE_MODEL. The ingest script creates SOCIAL_HASH_SALT if it is missing. Preserve that salt across resumptions. STORE_TEXT defaults true. SOCIAL_RADAR_ENABLED defaults false until the pipeline is ready.

```sh
.venv/bin/python -m pip install -r backend/requirements.txt
.venv/bin/python scripts/social_ingest.py --probe-only
.venv/bin/python scripts/social_ingest.py --max-minutes 90
```

The probe has a cumulative 20-request budget. Read docs/social_probe.md before changing an endpoint or retrying a failed pagination mode. Ingest stores resumable state in ignored .social_runs/. It runs at one request per second, backs off on HTTP 429 and stops at 25000 unique posts. The first pass spreads requests across terms and weekly windows. Thread enrichment adds observed interactions and is excluded from frequency metrics.

Read docs/social_quality.md after collection. If the corpus has fewer than 3000 posts, wait for the user's response before running the graph command. Do not use --allow-small-corpus without explicit approval.

For data collected by an earlier variable-precision timestamp build, normalize once after ingest has stopped, before date-filtered retrieval:

```sh
.venv/bin/python scripts/social_ingest.py --normalize-times
```

New ingestion uses six fractional digits and UTC Z on every stored timestamp. The normalization command updates only social post timestamps and prints counts.

## Prepare retrieval and communities

```sh
.venv/bin/python scripts/social_embed.py --dry-run
.venv/bin/python scripts/social_embed.py --limit 25000 --workers 4 --create-index
.venv/bin/python scripts/social_graph.py --name-limit 50
.venv/bin/python scripts/social_metrics.py --calibrate
.venv/bin/python scripts/eval_social.py
```

Embedding makes one model request per post, with bounded concurrent calls and resumable writes. Repeating the command skips successful embeddings. The vector index uses 768 dimensions and filters term and created_at. Index creation can fail if Atlas has no search index capacity; the script never deletes another index.

Graph edges distinguish observed interactions from inferred semantic similarity. Communities are estimates, not Bluesky-provided groups. The export contains at most 1500 authors and 15000 edges. Community naming is cached and bounded by --name-limit. Read docs/social_graph.md for graph interpretation.

Metrics keep empty and incomplete weeks distinct. Missing or truncated query coverage suppresses confident momentum and decay. Evaluation labels tune numeric thresholds only when enough annotated terms are eligible. They are never classifier inputs. Evaluation writes docs/eval_social.md and ignored local JSON artifacts. The report marks missing measurements explicitly.

## Run the backend

```sh
SOCIAL_RADAR_ENABLED=1 NESSIE_API_KEY='' SECONDLOOK_WARMUP=0 .venv/bin/uvicorn backend.main:app --reload --port 8000
```

This command keeps Nessie disabled and avoids startup model warmup calls. The optional /analyze social context has a ten-minute cache and a short lookup deadline. A failure or no match returns null and leaves the verdict behavior unchanged. Confident peaking or fading can support an already allowed micro_trend; it cannot create eligibility. Fast fashion remains a store-rating label.

GET /social-trends and GET /social-graph return aggregate sample data. POST /trend-ask requires a normal authentication bearer token and accepts question plus optional term, since and until. Date filters require an explicit UTC offset. Answers cite retrieved post URIs and omit account handles. Sources have snippets of at most 160 characters.

If dashboard/trends.html exists, /trends-ui shows the community graph and term curve. The extension card adds one estimated Bluesky chatter line, with a link only when that page exists. No new action buttons are added. dashboard/index.html is unchanged.

## Verify offline

```sh
MONGODB_URI=mongodb://127.0.0.1:27017 NESSIE_API_KEY='' SECONDLOOK_WARMUP=0 SOCIAL_RADAR_ENABLED=0 .venv/bin/python -m unittest discover tests
node extension/tests/price.test.js
PW_PATH=/path/to/playwright node extension/tests/card.harness.cjs
PW_PATH=/path/to/playwright node extension/tests/social.harness.cjs
```

The local Mongo URI prevents Atlas DNS resolution during test imports. Test cases replace database operations and provider requests with fakes. Playwright uses the real card code with a mocked service worker. Set PW_PATH to an installed Playwright package if it is not on Node's module path.

## Text retention

Set STORE_TEXT=false before ingest to omit newly collected text. This prevents embedding new posts without another text source. To inspect a potential wipe, run:

```sh
.venv/bin/python scripts/social_wipe_text.py
```

Only after an explicit decision to erase stored text, add --apply. A wipe leaves embeddings in place, but snippets will be empty. It does not delete post metadata or stored model outputs. Never print .env values or copy them into a report.
