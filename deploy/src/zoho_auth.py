"""
One-time Zoho OAuth authorization_code flow.

Run this script once to log in via browser and save the refresh token to .env.
The refresh token is then used by zoho_desk.py to get fresh access tokens
without browser interaction.

Before running:
  1. In Zoho API Console (api-console.zoho.com) -> your client -> Redirect URIs,
     make sure 'https://localhost' is listed as an allowed redirect URI.
  2. Run: python src/zoho_auth.py
  3. Log in with your Zoho credentials in the browser that opens.
  4. After login, the browser will redirect to https://localhost?code=...
     The page will fail to load (nothing is listening) — that is expected.
     Copy the full URL from the address bar and paste it into the terminal.

Usage:
    python src/zoho_auth.py
    python src/zoho_auth.py --redirect-url "https://localhost?code=...&..."
"""

import os
import sys
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from dotenv import load_dotenv, set_key

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_PATH)

AUTH_URL   = "https://accounts.zoho.com/oauth/v2/auth"
TOKEN_URL  = "https://accounts.zoho.com/oauth/v2/token"
REDIRECT   = "https://localhost"
SCOPES     = "Desk.tickets.CREATE,Desk.tickets.READ,Desk.basic.READ"


def get_tokens(prefilled_url: str | None = None) -> dict:
    client_id     = os.getenv("ZOHO_CLIENT_ID", "").strip()
    client_secret = os.getenv("ZOHO_CLIENT_SECRET", "").strip()

    if not client_id or not client_secret:
        sys.exit("Error: ZOHO_CLIENT_ID or ZOHO_CLIENT_SECRET missing from .env")

    auth_params = urlencode({
        "client_id":     client_id,
        "response_type": "code",
        "scope":         SCOPES,
        "redirect_uri":  REDIRECT,
        "access_type":   "offline",
        "prompt":        "consent",
    })
    auth_url = f"{AUTH_URL}?{auth_params}"

    print("Opening browser for Zoho login...")
    print(f"\nIf the browser doesn't open, visit this URL manually:\n  {auth_url}\n")
    webbrowser.open(auth_url)

    if prefilled_url:
        redirect_url = prefilled_url
    else:
        print("After logging in, the browser will redirect to https://localhost?code=...")
        print("The page will show an error (nothing is listening) — that is EXPECTED.")
        print("Copy the full URL from the browser address bar and paste it here.\n")
        redirect_url = input("Paste the full redirect URL: ").strip()

    params = parse_qs(urlparse(redirect_url).query)
    if "code" not in params:
        sys.exit("Error: no 'code' found in the URL. Make sure you copied the full address bar URL.")
    code = params["code"][0]

    response = requests.post(
        TOKEN_URL,
        data={
            "grant_type":    "authorization_code",
            "client_id":     client_id,
            "client_secret": client_secret,
            "redirect_uri":  REDIRECT,
            "code":          code,
        },
        timeout=30,
    )

    if not response.ok:
        print(f"Token exchange error {response.status_code}: {response.text}", file=sys.stderr)
        response.raise_for_status()

    return response.json()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Zoho OAuth token setup")
    parser.add_argument("--redirect-url", help="Paste the full redirect URL from the browser to skip the interactive prompt")
    args = parser.parse_args()

    tokens = get_tokens(prefilled_url=args.redirect_url)

    refresh_token = tokens.get("refresh_token", "").strip()
    access_token  = tokens.get("access_token", "").strip()

    if not refresh_token:
        print("Warning: no refresh_token in response. Full response:")
        import json
        print(json.dumps(tokens, indent=2))
        sys.exit(1)

    set_key(str(ENV_PATH), "ZOHO_REFRESH_TOKEN", refresh_token)
    print(f"\nSuccess! ZOHO_REFRESH_TOKEN updated in .env")

    if access_token:
        print(f"Access token (valid ~1 hour): {access_token[:20]}...")

    print("\nYou can now restart the server — Zoho ticket creation will work.")


if __name__ == "__main__":
    main()
