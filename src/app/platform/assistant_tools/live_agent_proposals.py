"""Translate proposal-only Live Agent output into governed assistant tool previews."""
from __future__ import annotations

from app.config.env import env_str, environment

import hashlib
import json
import os
from datetime import datetime
from typing import Any

from .gate import review_assistant_tool_request
from .intent import detect_assistant_tool_intent
from .models import AssistantToolRequest

_SUPPORTED_ACTIONS = {"calendar.read_availability", "calendar.create_event"}


def live_agent_tool_proposals(
    *,
    user_request: str,
    session_id: str,
    source_message_id: str,
    mode_result: dict[str, Any],
) -> list[dict[str, Any]]:
    candidates = _supported_calls(mode_result.get("tool_calls"))
    if not candidates:
        intent = detect_assistant_tool_intent(user_request)
        if intent.detected and intent.action_id in _SUPPORTED_ACTIONS:
            candidates = [{"name": intent.action_id, "args": dict(intent.input), "reason": intent.preview_summary}]
    proposals = []
    for index, call in enumerate(candidates):
        action_id = str(call.get("name") or "")
        tool_id = action_id.split(".", 1)[0]
        input_payload = _normalize_calendar_input(user_request, action_id, call.get("args"))
        proposal_id = _proposal_id(session_id, source_message_id, action_id, index, input_payload)
        request = AssistantToolRequest(
            tool_id=tool_id,
            action_id=action_id,
            session_id=session_id,
            proposal_id=proposal_id,
            input=input_payload,
        )
        decision = review_assistant_tool_request(request)
        missing_fields = _missing_calendar_fields(action_id, input_payload)
        reason = "clarification_required" if missing_fields else decision.reason
        proposals.append(
            {
                "proposal_id": proposal_id,
                "tool_id": tool_id,
                "action_id": action_id,
                "title": "Create Google Calendar event" if action_id == "calendar.create_event" else "Read Google Calendar availability",
                "summary": str(call.get("reason") or decision.result_summary or "Review this calendar proposal."),
                "input": input_payload,
                "risk_level": decision.risk_level,
                "approval_required": True,
                "ready_for_approval": decision.allowed and not missing_fields,
                "connection_required": decision.reason in {"missing_connection", "tool_disabled", "action_disabled"},
                "missing_fields": missing_fields,
                "reason": reason,
                "executes": False,
            }
        )
    return proposals


def live_agent_planner_context() -> dict[str, str]:
    timezone_name = environment().get("OMNIX_TIMEZONE", "UTC").strip() or "UTC"
    return {
        "current_datetime": datetime.now().astimezone().isoformat(timespec="seconds"),
        "user_timezone": timezone_name,
        "calendar_rule": "Never infer a missing date, AM/PM choice, duration, or attendee. Return no action when clarification is required.",
    }


def _supported_calls(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict) and str(item.get("name") or "") in _SUPPORTED_ACTIONS]


def _normalize_calendar_input(user_request: str, action_id: str, value: object) -> dict[str, Any]:
    payload = dict(value) if isinstance(value, dict) else {}
    payload.setdefault("query", user_request)
    if action_id == "calendar.create_event":
        lower = user_request.lower()
        payload.setdefault("title", "Reminder" if "remind" in lower else "Meeting" if "meeting" in lower else "Calendar event")
        payload.setdefault("timezone", environment().get("OMNIX_TIMEZONE", "UTC").strip() or "UTC")
        if "remind" in lower or "reminder" in lower:
            payload.setdefault("reminder_minutes", 0)
    return payload


def _missing_calendar_fields(action_id: str, payload: dict[str, Any]) -> list[str]:
    required = ["start_time", "end_time"]
    if action_id == "calendar.create_event":
        required.insert(0, "title")
    missing = [field for field in required if not str(payload.get(field) or payload.get(field.replace("_time", "")) or "").strip()]
    for field in ("start_time", "end_time"):
        value = str(payload.get(field) or payload.get(field.replace("_time", "")) or "").strip()
        if value and not _is_iso_datetime(value):
            missing.append(field)
    return sorted(set(missing))


def _is_iso_datetime(value: str) -> bool:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _proposal_id(session_id: str, message_id: str, action_id: str, index: int, payload: dict[str, Any]) -> str:
    source = json.dumps([session_id, message_id, action_id, index, payload], sort_keys=True, default=str)
    return f"proposal-{hashlib.sha256(source.encode('utf-8')).hexdigest()[:20]}"


class AssistantLiveAgentTools:
    """Assistant tools' implementation of chat's ``LIVE_AGENT_TOOLS`` port (PA-1.3)."""

    @property
    def read_tool_names(self) -> frozenset[str]:
        from .kasa_plan import KASA_READ_TOOLS

        return frozenset(KASA_READ_TOOLS)

    def is_device_tool(self, name: str) -> bool:
        from .kasa_plan import is_kasa_tool_name

        return is_kasa_tool_name(name)

    def read_request(self, call: Any, *, session_id: str) -> AssistantToolRequest | None:
        from .kasa_plan import kasa_request_from_tool_call

        return kasa_request_from_tool_call(call, session_id=session_id)

    def planner_context(self) -> dict[str, str]:
        return live_agent_planner_context()

    def tool_proposals(self, *, user_request: str, session_id: str, source_message_id: str,
                       mode_result: dict[str, Any]) -> list[dict[str, Any]]:
        return live_agent_tool_proposals(user_request=user_request, session_id=session_id,
                                         source_message_id=source_message_id, mode_result=mode_result)

    def first_pending_write(self, mode_result: dict[str, Any], *, session_id: str) -> AssistantToolRequest | None:
        from .kasa_plan import first_pending_kasa_write

        return first_pending_kasa_write(mode_result, session_id=session_id)

    def parse_request(self, raw: dict[str, Any]) -> AssistantToolRequest:
        return AssistantToolRequest.model_validate(raw)
