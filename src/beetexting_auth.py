"""
BEEtexting OAuth2 authentication using client_credentials grant.
Fetches and returns a Bearer access token.
"""

import base64
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

TOKEN_URL = "https://auth.beetexting.com/oauth2/token/"
SCOPES = " ".join([
    "https://com.beetexting.scopes/ReadContact",
    "https://com.beetexting.scopes/WriteContact",
    "https://com.beetexting.scopes/SendMessage",
])


def get_access_token() -> str:
    client_id = os.getenv("BEETEXTING_CLIENT_ID")
    client_secret = os.getenv("BEETEXTING_CLIENT_SECRET")

    if not client_id or not client_secret:
        sys.exit("Error: BEETEXTING_CLIENT_ID or BEETEXTING_CLIENT_SECRET missing from .env")

    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()

    response = requests.post(
        TOKEN_URL,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "client_credentials",
            "scope": SCOPES,
        },
    )

    if not response.ok:
        print(f"Auth error {response.status_code}: {response.text}", file=sys.stderr)
        response.raise_for_status()

    return response.json()["access_token"]


if __name__ == "__main__":
    token = get_access_token()
    print(f"Access token obtained successfully.")
    print(f"Token: {token[:40]}...")
