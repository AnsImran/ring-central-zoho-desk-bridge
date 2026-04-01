"""
One-time BEEtexting authorization_code OAuth flow.

Run this script once to log in via browser and save the refresh token.
The refresh token is stored in beetexting_refresh_token.txt and used
by other scripts to get fresh access tokens without browser interaction.

Before running:
  1. In BEEtexting app → Integrations → API Connect → RENEW! client,
     make sure Callback URL is set to: https://localhost
  2. Run: python src/beetexting_user_auth.py
  3. Log in with your BEEtexting credentials in the browser that opens
  4. After login, the browser will redirect to https://localhost?code=...
     The page will fail to load (nothing is listening) — that is expected.
     Copy the full URL from the address bar and paste it into the terminal.

Usage:
    python src/beetexting_user_auth.py
"""

import base64
import json
import os
import sys
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

AUTH_URL   = "https://auth.beetexting.com/oauth2/authorize/"
TOKEN_URL  = "https://auth.beetexting.com/oauth2/token/"
CALLBACK   = "https://localhost:8080"
SCOPES     = " ".join([
    "https://com.beetexting.scopes/ReadContact",
    "https://com.beetexting.scopes/WriteContact",
    "https://com.beetexting.scopes/SendMessage",
])
TOKEN_FILE = Path(__file__).parent.parent / "beetexting_refresh_token.txt"


def get_user_tokens(prefilled_url: str | None = None) -> dict:
    client_id     = os.getenv("BEETEXTING_USER_CLIENT_ID")
    client_secret = os.getenv("BEETEXTING_USER_CLIENT_SECRET")

    if not client_id or not client_secret:
        sys.exit("Error: BEETEXTING_USER_CLIENT_ID or BEETEXTING_USER_CLIENT_SECRET missing from .env")

    auth_params = urlencode({
        "client_id":     client_id,
        "response_type": "code",
        "scope":         SCOPES,
        "redirect_uri":  CALLBACK,
    })
    auth_url = f"{AUTH_URL}?{auth_params}"

    print("Opening browser for BEEtexting login...")
    print(f"\nIf the browser doesn't open, visit this URL manually:\n  {auth_url}\n")
    webbrowser.open(auth_url)

    if prefilled_url:
        redirect_url = prefilled_url
    else:
        print("After logging in, the browser will redirect to https://localhost?code=...")
        print("The page will show an error (nothing is listening there) — that is EXPECTED.")
        print("Copy the full URL from the browser address bar and paste it here.\n")
        redirect_url = input("Paste the full redirect URL: ").strip()

    params = parse_qs(urlparse(redirect_url).query)
    if "code" not in params:
        sys.exit("Error: no 'code' found in the URL. Make sure you copied the full address bar URL.")
    code = params["code"][0]

    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    response = requests.post(
        TOKEN_URL,
        headers={
            "Authorization":  f"Basic {credentials}",
            "Content-Type":   "application/x-www-form-urlencoded",
        },
        data={
            "grant_type":   "authorization_code",
            "code":         code,
            "redirect_uri": CALLBACK,
        },
    )

    if not response.ok:
        print(f"Token exchange error {response.status_code}: {response.text}", file=sys.stderr)
        response.raise_for_status()

    return response.json()


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--redirect-url", help="Paste the full redirect URL from the browser here to skip the interactive prompt")
    args = parser.parse_args()

    tokens = get_user_tokens(prefilled_url=args.redirect_url)

    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        print("Warning: no refresh_token in response. Token data:")
        print(json.dumps(tokens, indent=2))
        sys.exit(1)

    TOKEN_FILE.write_text(refresh_token)
    print(f"\nSuccess! Refresh token saved to: {TOKEN_FILE.name}")
    print("You can now run beetexting_subscribe.py to register the webhook.")


def get_access_token_from_refresh() -> str:
    """Use stored refresh token to get a fresh access token (no browser needed)."""
    if not TOKEN_FILE.exists():
        sys.exit(f"No refresh token found. Run beetexting_user_auth.py first.")

    client_id     = os.getenv("BEETEXTING_USER_CLIENT_ID")
    client_secret = os.getenv("BEETEXTING_USER_CLIENT_SECRET")
    credentials   = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    refresh_token = TOKEN_FILE.read_text().strip()

    response = requests.post(
        TOKEN_URL,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type":  "application/x-www-form-urlencoded",
        },
        data={
            "grant_type":    "refresh_token",
            "refresh_token": refresh_token,
        },
    )

    if not response.ok:
        print(f"Refresh error {response.status_code}: {response.text}", file=sys.stderr)
        response.raise_for_status()

    data = response.json()
    if "refresh_token" in data:
        TOKEN_FILE.write_text(data["refresh_token"])

    return data["access_token"]


if __name__ == "__main__":
    main()
