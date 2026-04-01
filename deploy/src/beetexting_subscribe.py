"""
Register or list BEEtexting webhook subscriptions for inbound messages.

Run beetexting_user_auth.py first to get a user-level token.

Usage:
    python src/beetexting_subscribe.py --list
    python src/beetexting_subscribe.py --create --webhook-url "https://your-server.com/webhook"
    python src/beetexting_subscribe.py --delete --id <subscription_id>
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

from beetexting_user_auth import get_access_token_from_refresh

load_dotenv(Path(__file__).parent.parent / ".env")

BASE_URL = "https://connect.beetexting.com/prod"

# 90-day expiry in milliseconds from now
def expiry_ms(days: int = 90) -> int:
    return int((time.time() + days * 86400) * 1000)


def headers() -> dict:
    api_key = os.getenv("BEETEXTING_USER_API_KEY")
    token   = get_access_token_from_refresh()
    return {
        "Authorization": f"Bearer {token}",
        "x-api-key":     api_key,
        "Content-Type":  "application/json",
    }


def list_subscriptions():
    r = requests.get(f"{BASE_URL}/webhooksubscription/all", headers=headers())
    if not r.ok:
        print(f"Error {r.status_code}: {r.text}", file=sys.stderr)
        r.raise_for_status()
    data = r.json()
    items = data.get("content", [])
    if not items:
        print("No subscriptions found.")
        return
    for s in items:
        print(f"ID      : {s['id']}")
        print(f"Status  : {s['status']}")
        print(f"URI     : {s['uri']}")
        print(f"Filters : {s['eventFilters']}")
        print(f"Expires : {s.get('expirationTime')}")
        print()


def get_org_and_department() -> tuple[str, str]:
    """Read orgId and departmentId from environment variables."""
    org_id  = os.getenv("BEETEXTING_ORG_ID")
    dept_id = os.getenv("BEETEXTING_DEPT_ID")
    if org_id and dept_id:
        return org_id, dept_id
    sys.exit("BEETEXTING_ORG_ID and BEETEXTING_DEPT_ID must be set in .env")


def create_subscription(webhook_url: str):
    org_id, dept_id = get_org_and_department()

    event_filter = f"/orgId/{org_id}/department/{dept_id}/message?direction=inbound"
    payload = {
        "uri":                  webhook_url,
        "eventFilters":         [event_filter],
        "expiresDateInMillies": expiry_ms(90),
    }

    print(f"Creating subscription...")
    print(f"  Event filter : {event_filter}")
    print(f"  Webhook URL  : {webhook_url}")

    r = requests.post(
        f"{BASE_URL}/webhooksubscription",
        headers=headers(),
        json=payload,
    )
    if not r.ok:
        print(f"Error {r.status_code}: {r.text}", file=sys.stderr)
        r.raise_for_status()

    data = r.json()
    print(f"\nSubscription created successfully!")
    print(json.dumps(data, indent=2))

    # Save subscription ID for future reference
    sub_id_file = Path(__file__).parent.parent / "beetexting_subscription_id.txt"
    sub_id_file.write_text(data["id"])
    print(f"\nSubscription ID saved to: {sub_id_file.name}")


def delete_subscription(sub_id: str):
    r = requests.delete(f"{BASE_URL}/webhooksubscription/{sub_id}", headers=headers())
    if not r.ok:
        print(f"Error {r.status_code}: {r.text}", file=sys.stderr)
        r.raise_for_status()
    print(f"Subscription {sub_id} deleted.")


def main():
    parser = argparse.ArgumentParser(description="Manage BEEtexting webhook subscriptions")
    group  = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list",   action="store_true", help="List all subscriptions")
    group.add_argument("--create", action="store_true", help="Create inbound message subscription")
    group.add_argument("--delete", action="store_true", help="Delete a subscription")
    parser.add_argument("--webhook-url", help="Your public webhook URL (required for --create)")
    parser.add_argument("--id",          help="Subscription ID (required for --delete)")
    args = parser.parse_args()

    if args.list:
        list_subscriptions()
    elif args.create:
        if not args.webhook_url:
            sys.exit("--webhook-url is required when using --create")
        create_subscription(args.webhook_url)
    elif args.delete:
        if not args.id:
            sys.exit("--id is required when using --delete")
        delete_subscription(args.id)


if __name__ == "__main__":
    main()
