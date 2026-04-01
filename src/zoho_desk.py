"""Zoho Desk client for creating tickets from SMS transcript payloads."""

from __future__ import annotations

import datetime as dt
import html
import os
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

TOKEN_LIFETIME_SECONDS = 3600
TOKEN_RENEW_GRACE_SECONDS = 10 * 60

_TOKEN_CACHE: dict[str, Any] = {
    "token": None,
    "expires_at": None,
}

_DEPARTMENT_CACHE: dict[str, str | None] = {
    "department_id": None,
}


def _env_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing env var: {name}")
    return value


def _token_url() -> str:
    explicit = os.getenv("ZOHO_ACCOUNTS_TOKEN_URL", "").strip()
    if explicit:
        return explicit

    accounts_base = os.getenv("ZOHO_ACCOUNTS_BASE", "").strip().rstrip("/")
    if accounts_base:
        return f"{accounts_base}/oauth/v2/token"

    return "https://accounts.zoho.com/oauth/v2/token"


def _desk_base() -> str:
    return os.getenv("ZOHO_DESK_BASE", "https://desk.zoho.com").strip().rstrip("/")


def _now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def get_access_token(force_refresh: bool = False) -> str:
    """Fetch or reuse a Zoho OAuth access token via refresh-token flow."""
    if not force_refresh:
        token = _TOKEN_CACHE.get("token")
        expires_at = _TOKEN_CACHE.get("expires_at")
        if token and isinstance(expires_at, dt.datetime):
            if expires_at - _now_utc() > dt.timedelta(seconds=TOKEN_RENEW_GRACE_SECONDS):
                return str(token)

    response = requests.post(
        _token_url(),
        data={
            "refresh_token": _env_required("ZOHO_REFRESH_TOKEN"),
            "client_id": _env_required("ZOHO_CLIENT_ID"),
            "client_secret": _env_required("ZOHO_CLIENT_SECRET"),
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    response.raise_for_status()

    payload = response.json()
    token = str(payload.get("access_token") or "").strip()
    if not token:
        raise RuntimeError(f"Token response did not include access_token: {payload}")

    _TOKEN_CACHE["token"] = token
    _TOKEN_CACHE["expires_at"] = _now_utc() + dt.timedelta(seconds=TOKEN_LIFETIME_SECONDS)
    return token


def _desk_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Zoho-oauthtoken {token}",
        "orgId": _env_required("ZOHO_DESK_ORG_ID"),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _desk_request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    timeout: int = 30,
) -> requests.Response:
    url = f"{_desk_base()}{path}"

    token = get_access_token(force_refresh=False)
    response = requests.request(
        method=method,
        url=url,
        headers=_desk_headers(token),
        params=params,
        json=json_body,
        timeout=timeout,
    )

    if response.status_code == 401:
        token = get_access_token(force_refresh=True)
        response = requests.request(
            method=method,
            url=url,
            headers=_desk_headers(token),
            params=params,
            json=json_body,
            timeout=timeout,
        )

    return response


def _coerce_department_id(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text


def get_department_id() -> str:
    """Resolve ticket department id from env or by querying Zoho departments."""
    env_department_id = _coerce_department_id(os.getenv("ZOHO_DESK_DEPARTMENT_ID"))
    if env_department_id:
        _DEPARTMENT_CACHE["department_id"] = env_department_id
        return env_department_id

    cached = _DEPARTMENT_CACHE.get("department_id")
    if cached:
        return cached

    # Attempt 1: departments endpoint (may require elevated scope in some orgs)
    response = _desk_request("GET", "/api/v1/departments", params={"limit": 100})
    if response.ok:
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, list) and data:
            chosen: str | None = None
            for department in data:
                if not isinstance(department, dict):
                    continue
                dep_id = _coerce_department_id(department.get("id"))
                if not dep_id:
                    continue

                is_enabled = department.get("isEnabled")
                if is_enabled is True:
                    chosen = dep_id
                    break

            if not chosen:
                chosen = _coerce_department_id(data[0].get("id") if isinstance(data[0], dict) else None)

            if chosen:
                _DEPARTMENT_CACHE["department_id"] = chosen
                return chosen

    # Attempt 2: infer from recent tickets (works with ticket-read scope)
    for path in ("/api/v1/tickets", "/api/v1/tickets/search"):
        params = {"limit": 1, "from": 0}
        resp = _desk_request("GET", path, params=params)
        if not resp.ok:
            continue

        payload = resp.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list) or not data:
            continue

        first = data[0] if isinstance(data[0], dict) else None
        dep_id = _coerce_department_id(first.get("departmentId") if first else None)
        if dep_id:
            _DEPARTMENT_CACHE["department_id"] = dep_id
            return dep_id

    raise RuntimeError(
        "Unable to resolve Zoho department id. Set ZOHO_DESK_DEPARTMENT_ID in .env explicitly."
    )


def _format_transcript(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for message in messages:
        timestamp_ms = message.get("timestamp")
        try:
            ts = dt.datetime.fromtimestamp(int(timestamp_ms) / 1000, tz=dt.timezone.utc)
            ts_text = ts.strftime("%Y-%m-%d %H:%M:%S UTC")
        except Exception:
            ts_text = "unknown-time"

        direction = str(message.get("direction") or "").upper()
        from_number = str(message.get("from_number") or "")
        to_number = str(message.get("to_number") or "")
        text = str(message.get("text") or "")

        lines.append(f"[{ts_text}] {direction} {from_number} -> {to_number}: {text}")

    return "\n".join(lines)


def _build_ticket_description(phone: str, subject: str, messages: list[dict[str, Any]]) -> str:
    transcript = _format_transcript(messages)

    return (
        "<div>"
        "<p><strong>Source:</strong> Microsoft Teams SMS Bridge</p>"
        f"<p><strong>Customer Phone:</strong> {html.escape(phone)}</p>"
        f"<p><strong>Subject:</strong> {html.escape(subject)}</p>"
        "<p><strong>Transcript:</strong></p>"
        f"<pre>{html.escape(transcript)}</pre>"
        "</div>"
    )


def create_ticket_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Create a Zoho Desk ticket from Teams task-module payload."""
    phone = str(payload.get("phone") or "").strip()
    subject = str(payload.get("subject") or "SMS Conversation").strip() or "SMS Conversation"
    messages_raw = payload.get("messages")

    if not phone:
        raise ValueError("phone is required for Zoho ticket creation")
    if not isinstance(messages_raw, list) or not messages_raw:
        raise ValueError("messages list is required for Zoho ticket creation")

    messages = [m for m in messages_raw if isinstance(m, dict)]
    if not messages:
        raise ValueError("messages list did not contain valid items")

    department_id = get_department_id()
    description = _build_ticket_description(phone=phone, subject=subject, messages=messages)

    ticket_payload: dict[str, Any] = {
        "subject": subject,
        "departmentId": department_id,
        "description": description,
        "channel": "SMS",
        "phone": phone,
        "contact": {
            "lastName": phone,
            "phone": phone,
        },
    }

    status = str(os.getenv("ZOHO_DESK_DEFAULT_STATUS", "")).strip()
    if status:
        ticket_payload["status"] = status

    priority = str(os.getenv("ZOHO_DESK_DEFAULT_PRIORITY", "")).strip()
    if priority:
        ticket_payload["priority"] = priority

    response = _desk_request("POST", "/api/v1/tickets", json_body=ticket_payload)
    if not response.ok:
        raise RuntimeError(f"Zoho create-ticket failed ({response.status_code}): {response.text}")

    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError(f"Zoho create-ticket response is not JSON object: {data}")

    return {
        "id": data.get("id"),
        "ticketNumber": data.get("ticketNumber"),
        "webUrl": data.get("webUrl"),
        "subject": data.get("subject") or subject,
        "raw": data,
    }
