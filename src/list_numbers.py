"""
List all phone numbers available on the RingCentral account,
filtered to those with SMS capability.

Usage:
    python src/list_numbers.py
    python src/list_numbers.py --all       # include non-SMS numbers too
"""

import argparse
import json
import sys
from pathlib import Path

import requests

RINGCENTRAL_BASE_URL = "https://platform.ringcentral.com"
PHONE_NUMBERS_ENDPOINT = "/restapi/v1.0/account/~/phone-number"

ACCESS_TOKEN_FILE = Path(__file__).parent.parent / "access_token.txt"


def load_access_token() -> str:
    if not ACCESS_TOKEN_FILE.exists():
        sys.exit(f"Error: access_token.txt not found at {ACCESS_TOKEN_FILE}")
    token = ACCESS_TOKEN_FILE.read_text().strip()
    if not token:
        sys.exit("Error: access_token.txt is empty")
    return token


def fetch_phone_numbers() -> list[dict]:
    token = load_access_token()
    headers = {"Authorization": f"Bearer {token}"}

    numbers = []
    page = 1

    while True:
        response = requests.get(
            RINGCENTRAL_BASE_URL + PHONE_NUMBERS_ENDPOINT,
            headers=headers,
            params={"page": page, "perPage": 100},
        )

        if not response.ok:
            print(f"Error {response.status_code}: {response.text}", file=sys.stderr)
            response.raise_for_status()

        data = response.json()
        records = data.get("records", [])
        numbers.extend(records)

        paging = data.get("paging", {})
        if paging.get("page", 1) >= paging.get("totalPages", 1):
            break
        page += 1

    return numbers


def format_number(record: dict) -> dict:
    features = record.get("features", [])
    return {
        "phoneNumber": record.get("phoneNumber"),
        "label": record.get("label"),
        "type": record.get("type"),
        "usageType": record.get("usageType"),
        "features": features,
        "smsEnabled": "SmsSender" in features,
        "extension": (record.get("extension") or {}).get("extensionNumber"),
    }


def main():
    parser = argparse.ArgumentParser(description="List RingCentral phone numbers")
    parser.parse_args()

    print("Fetching phone numbers from RingCentral...\n")
    raw_numbers = fetch_phone_numbers()
    numbers = [format_number(r) for r in raw_numbers]

    if not numbers:
        print("No numbers found.")
        return

    print(f"{'='*60}")
    print(f"  All phone numbers ({len(numbers)} found)")
    print(f"{'='*60}")
    for n in numbers:
        label = n["label"] or n["usageType"] or ""
        ext = f"  ext. {n['extension']}" if n["extension"] else ""
        sms_tag = "  [SMS]" if n["smsEnabled"] else ""
        print(f"  {n['phoneNumber']:<20} {label}{ext}{sms_tag}")

    if all(not n["smsEnabled"] for n in numbers):
        print(
            "\n  Note: no numbers show the [SMS] feature flag. This is normal if the\n"
            "  'SMS' scope hasn't been added to your RC app yet, or if SMS is routed\n"
            "  via a third-party (e.g. BEEtexting). Try sending anyway — the API will\n"
            "  tell you if a number isn't permitted."
        )

    print(f"\nFull details saved to: numbers.json")
    Path("numbers.json").write_text(json.dumps(numbers, indent=2))


if __name__ == "__main__":
    main()
