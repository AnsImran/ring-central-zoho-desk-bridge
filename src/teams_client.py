"""Microsoft Teams proactive messaging client for inbound SMS notifications.

This module handles:
- creating a new thread per customer phone number
- appending to existing threads for known numbers
- posting Adaptive Cards with a Create Ticket action
"""

from __future__ import annotations

import datetime as dt
import logging
import os
from pathlib import Path
from typing import Any

from botbuilder.core import BotFrameworkAdapter, BotFrameworkAdapterSettings, CardFactory
from botbuilder.schema import (
    Activity,
    ActivityTypes,
    ChannelAccount,
    ConversationAccount,
    ConversationParameters,
    ConversationReference,
)

import message_store

log = logging.getLogger("teams_client")
log.setLevel(logging.INFO)
log.propagate = False
if not log.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(_handler)


class TeamsConfigError(ValueError):
    """Raised when required Teams configuration is missing."""


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise TeamsConfigError(f"Missing required environment variable: {name}")
    return value


def _format_timestamp(timestamp_ms: int | None) -> str:
    if timestamp_ms is None:
        return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return dt.datetime.fromtimestamp(timestamp_ms / 1000, tz=dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def build_inbound_sms_card(
    phone_number: str,
    text: str,
    timestamp_ms: int | None = None,
    message_id: str | None = None,
) -> dict[str, Any]:
    """Build an Adaptive Card for an inbound SMS message."""
    data: dict[str, Any] = {
        "action": "create_ticket",
        "phone": phone_number,
        "msteams": {"type": "task/fetch"},
    }
    if message_id:
        data["message_id"] = message_id

    return {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": [
            {"type": "TextBlock", "text": f"SMS from {phone_number}", "weight": "Bolder"},
            {"type": "TextBlock", "text": text or "", "wrap": True},
            {"type": "TextBlock", "text": _format_timestamp(timestamp_ms), "isSubtle": True, "size": "Small"},
        ],
        "actions": [
            {
                "type": "Action.Submit",
                "title": "Create Ticket",
                "data": data,
            }
        ],
    }


def create_adapter_from_env() -> BotFrameworkAdapter:
    """Build a BotFrameworkAdapter using Teams app credentials from environment."""
    app_id = _require_env("TEAMS_APP_ID")
    app_password = _require_env("TEAMS_APP_PASSWORD")
    tenant_id = os.getenv("TEAMS_TENANT_ID", "").strip() or None

    settings = BotFrameworkAdapterSettings(
        app_id=app_id,
        app_password=app_password,
        channel_auth_tenant=tenant_id,
    )
    return BotFrameworkAdapter(settings)


class TeamsClient:
    """Proactive Teams posting client keyed by customer phone number."""

    def __init__(
        self,
        adapter: BotFrameworkAdapter,
        app_id: str | None = None,
        tenant_id: str | None = None,
        channel_id: str | None = None,
        db_path: str | Path | None = None,
    ):
        self.adapter = adapter
        self.app_id = app_id or _require_env("TEAMS_APP_ID")
        self.tenant_id = tenant_id if tenant_id is not None else os.getenv("TEAMS_TENANT_ID", "").strip()
        self.channel_id = channel_id or _require_env("TEAMS_CHANNEL_ID")
        self.db_path = db_path

        message_store.initialize(self.db_path)

    def save_service_url(self, service_url: str) -> None:
        """Persist the Teams service URL captured during bot activity processing."""
        if not service_url:
            return
        message_store.set_bot_state("service_url", service_url, db_path=self.db_path)

    def save_service_url_from_activity(self, activity: Activity | None) -> None:
        """Extract and persist service_url from an incoming Teams activity."""
        if activity is None:
            return
        self.save_service_url(activity.service_url or "")

    def _get_service_url(self) -> str:
        service_url = message_store.get_bot_state("service_url", db_path=self.db_path)
        if not service_url:
            raise TeamsConfigError(
                "Teams service_url is not set. Capture it from incoming /api/messages activity and store in bot_state key 'service_url'."
            )
        return service_url

    def _thread_reference(self, conversation_id: str, service_url: str) -> ConversationReference:
        return ConversationReference(
            channel_id="msteams",
            service_url=service_url,
            bot=ChannelAccount(id=self.app_id),
            user=ChannelAccount(id=self.app_id),
            conversation=ConversationAccount(
                id=conversation_id,
                is_group=True,
                conversation_type="channel",
                tenant_id=self.tenant_id or None,
            ),
        )

    def _seed_reference_for_create(self, service_url: str) -> ConversationReference:
        return ConversationReference(
            channel_id="msteams",
            service_url=service_url,
            bot=ChannelAccount(id=self.app_id),
            user=ChannelAccount(id=self.app_id),
            conversation=ConversationAccount(
                is_group=True,
                conversation_type="channel",
                tenant_id=self.tenant_id or None,
            ),
        )

    def _conversation_parameters(self) -> ConversationParameters:
        channel_data: dict[str, Any] = {
            "channel": {"id": self.channel_id},
        }
        if self.tenant_id:
            channel_data["tenant"] = {"id": self.tenant_id}

        return ConversationParameters(
            is_group=True,
            bot=ChannelAccount(id=self.app_id),
            channel_data=channel_data,
            tenant_id=self.tenant_id or None,
        )

    async def _send_to_existing_thread(
        self,
        conversation_id: str,
        service_url: str,
        attachment,
    ) -> None:
        reference = self._thread_reference(conversation_id=conversation_id, service_url=service_url)

        async def _callback(turn_context):
            await turn_context.send_activity(Activity(type=ActivityTypes.message, attachments=[attachment]))

        await self.adapter.continue_conversation(reference, _callback, bot_id=self.app_id)

    async def _create_thread_and_send(
        self,
        phone_number: str,
        service_url: str,
        attachment,
    ) -> dict[str, str]:
        channel_data: dict[str, Any] = {"channel": {"id": self.channel_id}}
        if self.tenant_id:
            channel_data["tenant"] = {"id": self.tenant_id}

        initial_activity = Activity(type=ActivityTypes.message, attachments=[attachment])

        params = ConversationParameters(
            is_group=True,
            bot=ChannelAccount(id=self.app_id),
            channel_data=channel_data,
            activity=initial_activity,
            tenant_id=self.tenant_id or None,
        )

        log.info(
            "Creating Teams thread for %s | service_url=%s | channel_id=%s | tenant=%s",
            phone_number, service_url, self.channel_id, self.tenant_id,
        )

        connector_client = await self.adapter.create_connector_client(service_url)
        response = await connector_client.conversations.create_conversation(params)
        conversation_id = response.id

        log.info("Teams thread created for %s | conversation_id=%s", phone_number, conversation_id)

        message_store.upsert_thread(
            phone_number=phone_number,
            conversation_id=conversation_id,
            service_url=service_url,
            db_path=self.db_path,
        )

        return {
            "conversation_id": conversation_id,
            "service_url": service_url,
        }

    async def post_to_channel(
        self,
        phone_number: str,
        text: str,
        message_id: str | None = None,
        timestamp_ms: int | None = None,
    ) -> dict[str, str]:
        """Post inbound SMS to Teams, creating or continuing a thread for the phone number."""
        service_url = self._get_service_url()
        card = build_inbound_sms_card(
            phone_number=phone_number,
            text=text,
            timestamp_ms=timestamp_ms,
            message_id=message_id,
        )
        attachment = CardFactory.adaptive_card(card)

        thread = message_store.get_thread_by_phone(phone_number, db_path=self.db_path)
        if thread:
            log.info("Existing thread found for %s | conversation_id=%s", phone_number, thread["conversation_id"])
            try:
                await self._send_to_existing_thread(
                    conversation_id=thread["conversation_id"],
                    service_url=thread["service_url"],
                    attachment=attachment,
                )
                return {
                    "mode": "continue",
                    "conversation_id": thread["conversation_id"],
                    "service_url": thread["service_url"],
                }
            except Exception as exc:
                log.warning(
                    "Failed to continue existing Teams thread for %s (conversation_id=%s): %s. Recreating thread.",
                    phone_number,
                    thread.get("conversation_id"),
                    exc,
                )
        else:
            log.info("No existing thread for %s — creating new thread.", phone_number)

        created = await self._create_thread_and_send(
            phone_number=phone_number,
            service_url=service_url,
            attachment=attachment,
        )
        return {
            "mode": "create",
            "conversation_id": created["conversation_id"],
            "service_url": created["service_url"],
        }

    async def post_text_to_phone_thread(self, phone_number: str, text: str) -> dict[str, str]:
        """Post a plain text message into an existing phone-number thread."""
        thread = message_store.get_thread_by_phone(phone_number, db_path=self.db_path)
        if not thread:
            raise KeyError(f"No Teams thread mapping found for phone number: {phone_number}")

        reference = self._thread_reference(
            conversation_id=thread["conversation_id"],
            service_url=thread["service_url"],
        )

        async def _callback(turn_context):
            await turn_context.send_activity(text)

        await self.adapter.continue_conversation(reference, _callback, bot_id=self.app_id)

        return {
            "conversation_id": thread["conversation_id"],
            "service_url": thread["service_url"],
        }
