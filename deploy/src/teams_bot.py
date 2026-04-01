"""Microsoft Teams bot handlers for SMS reply and task module actions."""

from __future__ import annotations

import inspect
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import quote_plus

from botbuilder.core import TurnContext
from botbuilder.core.teams import TeamsActivityHandler
from botbuilder.schema.teams import (
    TaskModuleContinueResponse,
    TaskModuleMessageResponse,
    TaskModuleRequest,
    TaskModuleResponse,
    TaskModuleTaskInfo,
)

import message_store
from beetexting_send_sms import send_sms
from teams_client import TeamsClient

log = logging.getLogger("teams_bot")
log.setLevel(logging.INFO)
log.propagate = False
if not log.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(_handler)

TicketCreator = Callable[[dict[str, Any]], Any | Awaitable[Any]]


class TeamsBot(TeamsActivityHandler):
    """Teams bot activity handler for SMS bridge interactions."""

    def __init__(
        self,
        teams_client: TeamsClient,
        db_path: str | Path | None = None,
        ticket_creator: TicketCreator | None = None,
        default_from_number: str | None = None,
        task_module_base_url: str | None = None,
    ):
        super().__init__()
        self.teams_client = teams_client
        self.db_path = db_path
        self.ticket_creator = ticket_creator
        self.default_from_number = (default_from_number or os.getenv("BEETEXTING_DEFAULT_FROM_NUMBER", "")).strip() or None
        self.task_module_base_url = (
            task_module_base_url
            or os.getenv("TASK_MODULE_BASE_URL")
            or os.getenv("PUBLIC_BASE_URL")
            or ""
        ).rstrip("/")

        message_store.initialize(self.db_path)

    async def on_turn(self, turn_context: TurnContext):
        # Capture and persist service_url so proactive posting can reuse it.
        self.teams_client.save_service_url_from_activity(turn_context.activity)
        await super().on_turn(turn_context)

    async def on_message_activity(self, turn_context: TurnContext):
        activity = turn_context.activity

        raw_text = TurnContext.remove_recipient_mention(activity) or activity.text or ""
        text = raw_text.strip()
        if not text:
            return

        if activity.from_property and activity.from_property.id == self.teams_client.app_id:
            return

        conversation_id = activity.conversation.id if activity.conversation else None
        if not conversation_id:
            await turn_context.send_activity("Unable to route this reply: missing Teams conversation id.")
            return

        phone_number = message_store.get_phone_by_conversation(conversation_id, db_path=self.db_path)
        if not phone_number:
            await turn_context.send_activity("No customer phone mapping was found for this thread.")
            return

        try:
            from_number = self._resolve_outbound_from_number(phone_number)
            response = send_sms(from_number=from_number, to_number=phone_number, text=text)

            outbound_message_id = self._extract_message_id(response)
            if not outbound_message_id:
                outbound_message_id = f"teams-{uuid.uuid4().hex}"

            message_store.upsert_message(
                message_id=outbound_message_id,
                timestamp=message_store.now_ms(),
                direction="outbound",
                from_number=from_number,
                to_number=phone_number,
                text=text,
                db_path=self.db_path,
            )

            await turn_context.send_activity(f"SMS sent to {phone_number}.")
        except Exception as exc:
            log.exception("Failed to send SMS from Teams reply: %s", exc)
            await turn_context.send_activity("Failed to send SMS. Check server logs for details.")

    async def on_teams_task_module_fetch(
        self,
        turn_context: TurnContext,
        task_module_request: TaskModuleRequest,
    ) -> TaskModuleResponse:
        data = self._normalize_request_data(task_module_request)
        phone_number = str(data.get("phone") or "").strip()

        if not phone_number:
            conversation_id = turn_context.activity.conversation.id if turn_context.activity.conversation else None
            if conversation_id:
                phone_number = message_store.get_phone_by_conversation(conversation_id, db_path=self.db_path) or ""

        if not phone_number:
            return self._task_message_response("Could not determine customer phone number for this thread.")

        task_url = self._build_task_module_url(phone_number)

        return TaskModuleResponse(
            task=TaskModuleContinueResponse(
                value=TaskModuleTaskInfo(
                    title="Create Zoho Desk Ticket",
                    url=task_url,
                    fallback_url=task_url,
                    width="large",
                    height="large",
                )
            )
        )

    async def on_teams_task_module_submit(
        self,
        turn_context: TurnContext,
        task_module_request: TaskModuleRequest,
    ) -> TaskModuleResponse:
        data = self._normalize_request_data(task_module_request)

        phone_number = str(data.get("phone") or "").strip()
        from_id = str(data.get("from_id") or "").strip()
        to_id = str(data.get("to_id") or "").strip()
        subject = str(data.get("subject") or "SMS Conversation").strip()

        if not phone_number or not from_id or not to_id:
            return self._task_message_response("Missing required fields: phone, from_id, to_id.")

        transcript = message_store.list_messages_between_ids(
            phone_number=phone_number,
            from_id=from_id,
            to_id=to_id,
            db_path=self.db_path,
        )
        if not transcript:
            return self._task_message_response("No messages found for the selected range.")

        ticket_payload = {
            "phone": phone_number,
            "subject": subject,
            "from_id": from_id,
            "to_id": to_id,
            "messages": transcript,
        }

        confirmation_text: str
        if self.ticket_creator is None:
            confirmation_text = (
                "Ticket request captured from Teams. Zoho Desk integration is not wired yet in this step."
            )
        else:
            try:
                ticket_result = self.ticket_creator(ticket_payload)
                if inspect.isawaitable(ticket_result):
                    ticket_result = await ticket_result
                confirmation_text = self._format_ticket_result(ticket_result)
            except Exception as exc:
                log.exception("Ticket creation failed: %s", exc)
                return self._task_message_response(f"Ticket creation failed: {exc}")

        try:
            await self.teams_client.post_text_to_phone_thread(phone_number, confirmation_text)
        except Exception as exc:
            log.warning("Could not post ticket confirmation into thread for %s: %s", phone_number, exc)

        return self._task_message_response(confirmation_text)

    def _resolve_outbound_from_number(self, phone_number: str) -> str:
        if self.default_from_number:
            return self.default_from_number

        history = message_store.list_messages_for_phone(
            phone_number=phone_number,
            limit=500,
            db_path=self.db_path,
        )

        for item in reversed(history):
            if item["direction"] == "inbound" and item["from_number"] == phone_number:
                return item["to_number"]
            if item["direction"] == "outbound" and item["to_number"] == phone_number:
                return item["from_number"]

        raise ValueError(
            "No outbound sender number available. Set BEETEXTING_DEFAULT_FROM_NUMBER or ensure inbound history exists."
        )

    @staticmethod
    def _extract_message_id(response: dict[str, Any] | None) -> str | None:
        if not response:
            return None

        for key in ("id", "messageId", "message_id"):
            value = response.get(key)
            if value:
                return str(value)

        content = response.get("content")
        if isinstance(content, dict):
            for key in ("id", "messageId", "message_id"):
                value = content.get(key)
                if value:
                    return str(value)

        return None

    def _build_task_module_url(self, phone_number: str) -> str:
        path = f"/task-module/create-ticket?phone={quote_plus(phone_number)}"
        if self.task_module_base_url:
            return f"{self.task_module_base_url}{path}"
        return path

    @staticmethod
    def _normalize_request_data(task_module_request: TaskModuleRequest) -> dict[str, Any]:
        data = task_module_request.data or {}
        if isinstance(data, dict):
            return data

        if hasattr(data, "to_dict") and callable(data.to_dict):
            maybe = data.to_dict()
            if isinstance(maybe, dict):
                return maybe

        if hasattr(data, "__dict__") and isinstance(data.__dict__, dict):
            return data.__dict__

        return {}

    @staticmethod
    def _task_message_response(message: str) -> TaskModuleResponse:
        return TaskModuleResponse(task=TaskModuleMessageResponse(value=message))

    @staticmethod
    def _format_ticket_result(ticket_result: Any) -> str:
        if ticket_result is None:
            return "Ticket creation completed."

        if isinstance(ticket_result, str):
            text = ticket_result.strip()
            return f"Ticket created: {text}" if text else "Ticket creation completed."

        if isinstance(ticket_result, dict):
            ticket_number = str(ticket_result.get("ticketNumber") or "").strip()
            ticket_id = str(ticket_result.get("id") or "").strip()
            web_url = str(ticket_result.get("webUrl") or "").strip()

            identifier = ticket_number or ticket_id
            if identifier and web_url:
                return f"Ticket #{identifier} created: {web_url}"
            if identifier:
                return f"Ticket #{identifier} created."
            if web_url:
                return f"Ticket created: {web_url}"

            return "Ticket creation completed."

        return f"Ticket creation completed: {ticket_result}"
