#!/usr/bin/env bash
# Demo start: turns on the Radar, the photo material estimate and Photon friend replies for this shell session only.
# Committed defaults stay off. Usage: scripts/demo_up.sh [port]   (default 8000)
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${1:-8000}"
export SOCIAL_RADAR_ENABLED=1
export MATERIALS_PHOTO_ENABLED=1
export PHOTON_FRIEND_MODE=1
if [ ! -f config/demo_products.json ]; then
  echo "config/demo_products.json is missing; see docs/radar_integration_notes.md" >&2
  exit 1
fi
echo "Demo products (config/demo_products.json):"
.venv/bin/python -c 'import json; [print(" -", p["name"], "->", p.get("expected_term"), "|", p.get("demo_role")) for p in json.load(open("config/demo_products.json"))["products"]]'
echo "Prewarming the Bluesky sample lookups..."
.venv/bin/python scripts/prewarm_social.py || echo "prewarm failed; continuing (the first lookup may return no radar field)"
echo "Starting the backend on port ${PORT} with SOCIAL_RADAR_ENABLED=1 MATERIALS_PHOTO_ENABLED=1 PHOTON_FRIEND_MODE=1"
exec .venv/bin/uvicorn backend.main:app --port "${PORT}"
