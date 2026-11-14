"""Opt a user into Nessie: set nessie_enabled, create their Nessie customer, Checking and Savings, print balances.

Run from the project root:
    python scripts/setup_nessie_demo.py                      # demo@secondlook.test
    python scripts/setup_nessie_demo.py --email test_x@secondlook.test

Idempotent: a second run reuses the stored customer and accounts. Never prints the key.
Only users set up here move money on Skip it. Everyone else stays on the local path.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db import users  # noqa: E402
from backend.services import nessie  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--email", default="demo@secondlook.test")
    args = parser.parse_args()
    if not nessie.configured():
        sys.exit("NESSIE_API_KEY is not set in .env")
    user = users.find_one({"email": args.email})
    if user is None:
        sys.exit(f"No user with email {args.email}. Sign up first.")
    had_accounts = nessie.has_accounts(user)
    try:
        user = nessie.ensure_accounts(user)
        users.update_one({"_id": user["_id"]}, {"$set": {"nessie_enabled": True}})
        balances = nessie.get_balances(user)
    except nessie.NessieError as e:
        sys.exit(f"FAIL  {e}")
    print(f"{'Reused' if had_accounts else 'Created'} Nessie accounts for {args.email} (nessie_enabled: true)")
    print(f"  customer  {user['nessie_customer_id']}")
    print(f"  checking  {user['nessie_checking_id']}  {balances['checking_cents'] / 100:,.2f} USD")
    print(f"  savings   {user['nessie_savings_id']}  {balances['savings_cents'] / 100:,.2f} USD")


if __name__ == "__main__":
    main()
