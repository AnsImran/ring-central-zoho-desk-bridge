"""BEEtexting webhook receiver + Teams bot bridge endpoints.

BEEtexting sends:
  - GET  with header 'validation-token' -> must echo it back
  - POST with JSON payload containing messageId -> fetch full message and process it

Teams sends:
  - POST /api/messages for standard messages and invoke actions
"""

from __future__ import annotations

import argparse
import datetime
import html
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any

import requests
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

import message_store
from beetexting_auth import get_access_token
from teams_bot import TeamsBot
from teams_client import TeamsClient, TeamsConfigError, create_adapter_from_env
from zoho_desk import create_ticket_from_payload

load_dotenv(Path(__file__).parent.parent / ".env")

log = logging.getLogger("beetexting_webhook")
log.setLevel(logging.INFO)
log.propagate = False
if not log.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(_handler)

app = FastAPI()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = PROJECT_ROOT / "static"
BASE_URL = "https://connect.beetexting.com/prod"

# Step 5 requires static mounting. The directory can be populated in step 6.
app.mount("/static", StaticFiles(directory=str(STATIC_DIR), check_dir=False), name="static")

_teams_adapter = None
_teams_client: TeamsClient | None = None
_teams_bot: TeamsBot | None = None
_teams_init_error: str | None = None


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_to_number(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list) and value:
        first = value[0]
        if isinstance(first, dict):
            candidate = first.get("phoneNumber") or first.get("number")
            return str(candidate).strip() if candidate else None
        return str(first).strip()
    if isinstance(value, dict):
        candidate = value.get("phoneNumber") or value.get("number")
        return str(candidate).strip() if candidate else None
    return str(value).strip()


def _window_start_ms(window: str) -> int:
    normalized = (window or "").strip().lower()
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    now_ms = int(now_utc.timestamp() * 1000)

    if normalized in {"30min", "30m", "last30min", "last_30min"}:
        return now_ms - 30 * 60 * 1000

    if normalized in {"1h", "1hr", "1hour", "60min", "last1hour", "last_1hour"}:
        return now_ms - 60 * 60 * 1000

    local_now = now_utc.astimezone()
    local_start_of_day = local_now.replace(hour=0, minute=0, second=0, microsecond=0)

    if normalized in {"today", "day"}:
        return int(local_start_of_day.timestamp() * 1000)

    if normalized in {"thisweek", "week", "this_week"}:
        local_start_of_week = local_start_of_day - datetime.timedelta(days=local_start_of_day.weekday())
        return int(local_start_of_week.timestamp() * 1000)

    raise ValueError("Invalid window value. Use one of: 30min, 1hour, today, thisweek")


def get_teams_runtime() -> tuple[Any, TeamsClient, TeamsBot] | None:
    """Create and cache Teams adapter/client/bot runtime when env is ready."""
    global _teams_adapter, _teams_client, _teams_bot, _teams_init_error

    if _teams_adapter and _teams_client and _teams_bot:
        return _teams_adapter, _teams_client, _teams_bot

    try:
        adapter = create_adapter_from_env()
        client = TeamsClient(adapter=adapter)
        bot = TeamsBot(teams_client=client, ticket_creator=create_ticket_from_payload)

        _teams_adapter = adapter
        _teams_client = client
        _teams_bot = bot
        _teams_init_error = None

        log.info("Teams runtime initialized.")
        return _teams_adapter, _teams_client, _teams_bot

    except TeamsConfigError as exc:
        error_text = str(exc)
        if _teams_init_error != error_text:
            log.warning("Teams runtime not ready: %s", error_text)
            _teams_init_error = error_text
        return None

    except Exception as exc:  # pragma: no cover
        error_text = str(exc)
        if _teams_init_error != error_text:
            log.exception("Teams runtime initialization failed: %s", error_text)
            _teams_init_error = error_text
        return None


def fetch_message(message_id: str) -> dict:
    """Fetch full message details from BEEtexting using the messageId."""
    api_key = os.getenv("BEETEXTING_API_KEY")
    token = get_access_token()
    response = requests.get(
        f"{BASE_URL}/message/getmessagebyid/{message_id}",
        headers={
            "Authorization": f"Bearer {token}",
            "x-api-key": api_key,
        },
    )
    response.raise_for_status()
    return response.json()


async def handle_inbound_message(message: dict, received_at_ms: int | None = None):
    """Process inbound SMS: persist then proactively post to Teams thread when configured."""
    from_number = str(message.get("from") or "").strip()
    to_number = _extract_to_number(message.get("to"))
    text = str(message.get("text") or "")
    message_id = str(message.get("id") or f"beetexting-{uuid.uuid4().hex}")

    timestamp_ms = _safe_int(received_at_ms)
    if timestamp_ms is None:
        timestamp_ms = _safe_int(message.get("lastUpdated"))
    if timestamp_ms is None:
        timestamp_ms = message_store.now_ms()

    timestamp_text = datetime.datetime.fromtimestamp(timestamp_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")

    log.info("Inbound SMS received:")
    log.info("  From    : %s", from_number)
    log.info("  To      : %s", to_number)
    log.info("  Message : %s", text)
    log.info("  ID      : %s", message_id)
    log.info("  Time    : %s", timestamp_text)

    # Skip like/reaction notifications — BEEtexting forwards these as inbound messages
    # but they are not real customer messages and should not create Teams threads.
    if text.startswith("Liked '") or text.startswith('Liked "'):
        log.info("Skipping like/reaction notification from %s: %s", from_number, text)
        return

    if from_number and to_number:
        message_store.upsert_message(
            message_id=message_id,
            timestamp=timestamp_ms,
            direction="inbound",
            from_number=from_number,
            to_number=to_number,
            text=text,
        )

    runtime = get_teams_runtime()
    if runtime is None:
        log.info("Teams runtime unavailable. Inbound SMS was stored but not forwarded to Teams.")
        return

    _, teams_client, _ = runtime

    if not from_number:
        log.warning("Cannot forward to Teams: missing sender phone number in inbound payload.")
        return

    result = await teams_client.post_to_channel(
        phone_number=from_number,
        text=text,
        message_id=message_id,
        timestamp_ms=timestamp_ms,
    )
    log.info(
        "Forwarded inbound SMS to Teams (%s thread): conversation_id=%s",
        result.get("mode", "unknown"),
        result.get("conversation_id", ""),
    )


@app.get("/webhook")
async def webhook_get(request: Request):
    """BEEtexting webhook validation handshake."""
    validation_token = request.headers.get("validation-token")
    if validation_token:
        log.info("Webhook validation handshake received.")
        return Response(content="", headers={"validation-token": validation_token})
    return JSONResponse({"status": "ok"})


@app.post("/webhook")
async def webhook_post(request: Request):
    """Inbound message notification from BEEtexting."""
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    log.info("Webhook payload: %s", json.dumps(payload))

    message_info = payload.get("messageInfo", {})
    message_id = message_info.get("messageId")

    if not message_id:
        log.warning("No messageId in payload.")
        return JSONResponse({"status": "ignored"})

    try:
        received_at_ms = _safe_int(message_info.get("lastUpdated"))
        message = fetch_message(message_id)
        await handle_inbound_message(message, received_at_ms=received_at_ms)
    except Exception as exc:
        log.exception("Failed to fetch/process message %s: %s", message_id, exc)
        return JSONResponse({"status": "error", "detail": str(exc)}, status_code=500)

    return JSONResponse({"status": "ok"})


@app.post("/api/messages")
async def teams_messages(request: Request):
    """Bot Framework endpoint for Microsoft Teams activities."""
    runtime = get_teams_runtime()
    if runtime is None:
        return JSONResponse(
            {
                "status": "error",
                "detail": "Teams runtime is not configured. Set TEAMS_APP_ID, TEAMS_APP_PASSWORD, and TEAMS_CHANNEL_ID.",
            },
            status_code=503,
        )

    adapter, _, bot = runtime

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "detail": "Invalid JSON body."}, status_code=400)

    auth_header = request.headers.get("Authorization", "")

    try:
        invoke_response = await adapter.process_activity({"body": body}, auth_header, bot.on_turn)
    except Exception as exc:
        log.exception("Teams activity processing failed: %s", exc)
        return JSONResponse({"status": "error", "detail": str(exc)}, status_code=500)

    if invoke_response:
        content = invoke_response.body if invoke_response.body is not None else {}
        return JSONResponse(content=content, status_code=invoke_response.status)

    return Response(status_code=201)


@app.get("/task-module/create-ticket")
async def task_module_create_ticket(phone: str | None = Query(default=None)):
    """Serve Task Module HTML page for ticket creation."""
    html_file = STATIC_DIR / "create_ticket.html"
    if html_file.exists():
        return FileResponse(html_file)

    escaped_phone = html.escape(phone or "")
    return HTMLResponse(
        content=(
            "<html><body>"
            "<h2>Create Ticket UI not added yet.</h2>"
            "<p>Step 6 will add static/create_ticket.html.</p>"
            f"<p>phone={escaped_phone}</p>"
            "</body></html>"
        ),
        status_code=200,
    )


@app.get("/api/conversation/messages")
async def conversation_messages(
    phone: str = Query(..., description="Customer phone number in E.164 format"),
    window: str = Query("30min", description="30min | 1hour | today | thisweek"),
    limit: int = Query(500, ge=1, le=2000),
):
    """Return phone-number message history for task module dropdown population."""
    phone_number = phone.strip()
    if not phone_number:
        raise HTTPException(status_code=400, detail="phone query parameter is required")

    try:
        start_timestamp = _window_start_ms(window)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    messages = message_store.list_messages_for_phone(
        phone_number=phone_number,
        start_timestamp=start_timestamp,
        limit=limit,
    )

    return JSONResponse(
        {
            "phone": phone_number,
            "window": window,
            "start_timestamp": start_timestamp,
            "count": len(messages),
            "messages": messages,
        }
    )


def main():
    parser = argparse.ArgumentParser(description="BEEtexting webhook receiver")
    parser.add_argument("--port", type=int, default=5000, help="Port to listen on (default: 5000)")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind (default: 0.0.0.0)")
    args = parser.parse_args()

    log.info("Starting webhook receiver on %s:%s", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
