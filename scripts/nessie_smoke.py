"""Nessie v2 smoke test: create a customer, a Checking and a Savings account, deposit, transfer, read back.

Run from the project root:  python scripts/nessie_smoke.py
Creates 1 customer, 2 accounts and 2 transactions under the key in .env on every run. Prints the real
response shapes. Never prints the key.
"""
import json
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

BASE = "https://v2-prod-api.nessieisreal.com"
KEY = os.getenv("NESSIE_API_KEY", "")


def scrub(text: str) -> str:
    return text.replace(KEY, "***") if KEY else text


def show(label: str, data):
    print(f"\n== {label}")
    print(scrub(json.dumps(data, indent=2, default=str)))


def call(client: httpx.Client, method: str, path: str, body=None):
    r = client.request(method, path, json=body)
    if r.status_code >= 300:
        print(f"FAIL {method} {path} -> HTTP {r.status_code}: {scrub(r.text[:400])}")
        sys.exit(1)
    return r.json()


def main():
    if not KEY:
        sys.exit("NESSIE_API_KEY is not set")
    started = time.monotonic()
    with httpx.Client(base_url=BASE, headers={"Authorization": f"Bearer {KEY}"}, timeout=10) as c:
        customer = call(c, "POST", "/customers", {
            "first_name": "Smoke", "last_name": "Test",
            "address": {"street_number": "1", "street_name": "Nassau St", "city": "Princeton",
                        "state": "NJ", "zip": "08542"}})
        show("POST /customers", customer)
        checking = call(c, "POST", f"/customers/{customer['id']}/accounts",
                        {"type": "Checking", "nickname": "Smoke Checking", "balance_cents": 150000})
        show("POST /customers/{id}/accounts (Checking)", checking)
        savings = call(c, "POST", f"/customers/{customer['id']}/accounts",
                       {"type": "Savings", "nickname": "Smoke Savings", "balance_cents": 0})
        deposit = call(c, "POST", f"/accounts/{checking['id']}/deposits",
                       {"amount_cents": 2500, "description": "Smoke deposit"})
        show("POST /accounts/{id}/deposits", deposit)
        t0 = time.monotonic()
        transfer = call(c, "POST", f"/accounts/{checking['id']}/transfers",
                        {"payee_id": savings["id"], "amount_cents": 1999, "description": "Smoke transfer"})
        print(f"\ntransfer POST took {time.monotonic() - t0:.2f} s")
        show("POST /accounts/{id}/transfers", transfer)
        show("GET /accounts/{id} checking (transfer pending)", call(c, "GET", f"/accounts/{checking['id']}"))
        for tx in (deposit, transfer):
            settled = call(c, "POST", f"/transactions/{tx['id']}/settle")
        show("POST /transactions/{id}/settle (transfer)", settled)
        show("GET /accounts/{id} checking (settled)", call(c, "GET", f"/accounts/{checking['id']}"))
        show("GET /accounts/{id} savings (settled)", call(c, "GET", f"/accounts/{savings['id']}"))
        show("GET /accounts/{id}/transactions?limit=5 (checking)",
             call(c, "GET", f"/accounts/{checking['id']}/transactions?limit=5"))
    print(f"\nPASS  smoke test in {time.monotonic() - started:.1f} s")


if __name__ == "__main__":
    main()
