"""
Show which extension the current access token is authenticated as,
and list the phone numbers assigned to that extension.

Usage:
    python src/whoami.py
"""

import json
import sys
from pathlib import Path

import requests

RINGCENTRAL_BASE_URL = "https://platform.ringcentral.com"
ACCESS_TOKEN_FILE = Path(__file__).parent.parent / "access_token.txt"


def load_access_token() -> str:
    if not ACCESS_TOKEN_FILE.exists():
        sys.exit(f"Error: access_token.txt not found at {ACCESS_TOKEN_FILE}")
    token = ACCESS_TOKEN_FILE.read_text().strip()
    if not token:
        sys.exit("Error: access_token.txt is empty")
    return token


def get(path: str, token: str) -> dict:
    response = requests.get(
        RINGCENTRAL_BASE_URL + path,
        headers={"Authorization": f"Bearer {token}"},
    )
    if not response.ok:
        print(f"Error {response.status_code}: {response.text}", file=sys.stderr)
        response.raise_for_status()
    return response.json()


def main():
    token = load_access_token()

    ext = get("/restapi/v1.0/account/~/extension/~", token)
    print(f"Authenticated as:")
    print(f"  Name      : {ext.get('name')}")
    print(f"  Extension : {ext.get('extensionNumber')}")
    print(f"  Type      : {ext.get('type')}")
    print(f"  Status    : {ext.get('status')}")
    print(f"  ID        : {ext.get('id')}")

    numbers = get("/restapi/v1.0/account/~/extension/~/phone-number", token)
    records = numbers.get("records", [])

    print(f"\nPhone numbers assigned to this extension ({len(records)} found):")
    for r in records:
        features = r.get("features", [])
        sms_tag = "  [SMS]" if "SmsSender" in features else ""
        print(f"  {r.get('phoneNumber'):<20} {r.get('usageType')}{sms_tag}")

    if not records:
        print("  (none)")

    print(f"\nFull extension details saved to: whoami.json")
    Path("whoami.json").write_text(json.dumps(ext, indent=2))


if __name__ == "__main__":
    main()
