# Radar integration notes (feat/radar-integration)

- **Base:** `origin/feat/nessie-popup` (Avni's latest UI), with `feat/radar-decisions` merged in (merge commit e56cad3, no conflicts).
- **Files touched (backend):** `backend/services/radar_field.py` (new), `backend/models.py` (`RadarField`, optional `radar`), `backend/services/social.py` (optional `status` dict; the return value is unchanged), `backend/services/verdict.py` (radar read runs alongside Claude), `backend/main.py` (`timeline_url`), `backend/warmup.py` (warms the demo products only when the flag is on), `CONTRACT.md`.
- **Files touched (UI, additive only):** `extension/src/content/card.js` (`radarDetails`), `extension/src/content/card.styles.js` (`.radar*` rules), `extension/tests/card.harness.cjs`. The harness's `open()` gained a `scripts` option, which changes 2 existing lines; the absent-field DOM comparison needs it. `dashboard/trends.html` gained only the Radar panel.
- **Flags:** `SOCIAL_RADAR_ENABLED=1` adds `radar` to /analyze. `MATERIALS_PHOTO_ENABLED=1` turns on the photo material line. Both are off by default; `scripts/demo_up.sh` sets them for its own session only.
- **Demo start:** `scripts/demo_up.sh` (port 8000 by default). It prewarms, then starts uvicorn. Server startup also warms the caches for `config/demo_products.json`.
- **Check:** `.venv/bin/python scripts/radar_demo_check.py` runs /analyze for the 3 demo products: once with the flag off, then twice with it on.
- **Revert:** turn the flag off. The field is then absent and the response is identical. For a full revert, `git revert -m 1 e56cad3` plus the integration commits after it.
- **Limits:** labels come only from the 35 agent decisions (run radar-main-1). No agent label is micro_trend. The 7 manual styles return unknown ("No agent decision for this style."). All numbers describe a Bluesky sample, not the market.
- **Limits, lookups:** a cold lookup misses the 0.45 s budget, and the field is then omitted. A changed Gemini attribute read (for example, no category) changes the query and misses the warm cache. Non-apparel items skip the lookup, so they get no field.
