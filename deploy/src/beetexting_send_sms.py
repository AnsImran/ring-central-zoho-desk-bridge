"""
Send an SMS via BEEtexting API.

Usage:
    python src/beetexting_send_sms.py --from "+19498777179" --to "+1xxxxxxxxxx" --text "Hello"
"""

import argparse
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

from beetexting_auth import get_access_token

load_dotenv(Path(__file__).parent.parent / ".env")

SEND_SMS_URL = "https://connect.beetexting.com/prod/message/sendsms"


def send_sms(from_number: str, to_number: str, text: str) -> dict:
    api_key = os.getenv("BEETEXTING_API_KEY")
    if not api_key:
        sys.exit("Error: BEETEXTING_API_KEY missing from .env")

    token = get_access_token()

    response = requests.post(
        SEND_SMS_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "x-api-key": api_key,
        },
        params={
            "from": from_number,
            "to": to_number,
            "text": text,
        },
    )

    if not response.ok:
        print(f"Error {response.status_code}: {response.text}", file=sys.stderr)
        response.raise_for_status()

    return response.json()


def main():
    parser = argparse.ArgumentParser(description="Send SMS via BEEtexting")
    parser.add_argument("--from", dest="from_number", required=True, help="Sender phone number (e.g. +19498777179)")
    parser.add_argument("--to", dest="to_number", required=True, help="Recipient phone number (e.g. +1xxxxxxxxxx)")
    parser.add_argument("--text", required=True, help="Message body")
    args = parser.parse_args()

    print(f"Sending SMS from {args.from_number} to {args.to_number}...")
    result = send_sms(args.from_number, args.to_number, args.text)
    print(f"Success: {result}")


if __name__ == "__main__":
    main()
