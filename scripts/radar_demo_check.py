"""Run /analyze for the demo products with SOCIAL_RADAR_ENABLED off and on, and compare.

Uses the real backend in-process (FastAPI TestClient) against the database in .env, with a throwaway
e2e<timestamp>@example.com user. Each /analyze makes the normal paid calls (Gemini attributes, Claude verdict).
Checks: flag off has no radar key; flag on adds radar and changes no other key; micro_trend appears only
when the response's matched trend is short. Exit 1 on any failure.

Run: .venv/bin/python scripts/radar_demo_check.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SECONDLOOK_WARMUP", "0")

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from fastapi.testclient import TestClient

from backend.main import app

ITEM_KEYS = ("name", "image_url", "price", "store", "url", "description", "breadcrumb", "materials")


def main() -> int:
    products = json.loads((ROOT / "config" / "demo_products.json").read_text())["products"]
    client = TestClient(app)
    email = f"e2e{int(time.time())}@example.com"
    r = client.post("/auth/signup", json={"email": email, "password": "e2e-password-123"})
    if r.status_code != 200:
        print(f"FAIL signup {r.status_code}")
        return 1
    headers = {"Authorization": f"Bearer {r.json()['token']}"}
    failures = []
    warmed = False
    for p in products:
        item = {k: p[k] for k in ITEM_KEYS if k in p}
        out = {}
        for flag in ("0", "1", "1"):
            os.environ["SOCIAL_RADAR_ENABLED"] = flag
            if flag == "1" and not warmed:
                from backend.warmup import _warm_demo_products
                print(f"server warmup step social_demo_products: {_warm_demo_products()} matched")
                warmed = True
            t0 = time.perf_counter()
            resp = client.post("/analyze", json={"item": item}, headers=headers)
            ms = round((time.perf_counter() - t0) * 1000)
            body = resp.json() if resp.status_code == 200 else {"error": resp.status_code}
            out[flag] = body
            time.sleep(1 if flag == "1" else 0)
            trend = body.get("matched_trend") or {}
            print(json.dumps({"product": p["name"], "flag": flag, "status": resp.status_code, "ms": ms,
                              "verdict": body.get("verdict"), "matched_trend": trend.get("name"),
                              "social_term": (body.get("social_context") or {}).get("term"),
                              "radar": body.get("radar", "ABSENT")}))
            if body.get("verdict") == "micro_trend" and trend.get("lifespan_bucket") != "short":
                failures.append(f"{p['name']} flag {flag}: micro_trend without a short trend")
        off, on = out["0"], out["1"]  # out["1"] holds the second (warm) flag-on call
        if "radar" in off:
            failures.append(f"{p['name']}: radar present with flag off")
        extra = set(on) - set(off)
        if not extra <= {"radar", "social_context"}:
            failures.append(f"{p['name']}: flag on added keys {sorted(extra)}")
        missing = set(off) - set(on)
        if missing:
            failures.append(f"{p['name']}: flag on removed keys {sorted(missing)}")
    os.environ["SOCIAL_RADAR_ENABLED"] = "0"
    print("ALL PASS" if not failures else "FAILED:\n- " + "\n- ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
