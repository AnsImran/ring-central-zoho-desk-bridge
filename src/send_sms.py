"""
Send an SMS message via the RingCentral Message Store API.

Usage:
    python src/send_sms.py --from "+15551234567" --to "+15559876543" --text "Hello!"

The script reads the Bearer token from access_token.txt in the project root.
"""

import argparse
import json
import sys
from pathlib import Path

import requests

RINGCENTRAL_BASE_URL = "https://platform.ringcentral.com"
SMS_ENDPOINT = "/restapi/v1.0/account/~/extension/~/sms"

ACCESS_TOKEN_FILE = Path(__file__).parent.parent / "access_token.txt"


def load_access_token() -> str:
    if not ACCESS_TOKEN_FILE.exists():
        sys.exit(f"Error: access_token.txt not found at {ACCESS_TOKEN_FILE}")
    token = ACCESS_TOKEN_FILE.read_text().strip()
    if not token:
        sys.exit("Error: access_token.txt is empty")
    return token


def send_sms(from_number: str, to_number: str, text: str) -> dict:
    token = load_access_token()

    url = RINGCENTRAL_BASE_URL + SMS_ENDPOINT
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "from": {"phoneNumber": from_number},
        "to": [{"phoneNumber": to_number}],
        "text": text,
    }

    response = requests.post(url, headers=headers, json=payload)

    if not response.ok:
        print(f"Error {response.status_code}: {response.text}", file=sys.stderr)
        response.raise_for_status()

    return response.json()


def main():
    parser = argparse.ArgumentParser(description="Send an SMS via RingCentral")
    parser.add_argument("--from", dest="from_number", required=True, help="Sender phone number (E.164, e.g. +15551234567)")
    parser.add_argument("--to", dest="to_number", required=True, help="Recipient phone number (E.164, e.g. +15559876543)")
    parser.add_argument("--text", required=True, help="Message body")
    args = parser.parse_args()

    result = send_sms(args.from_number, args.to_number, args.text)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
