"""Translate canonical/legacy Hermes Kasa calls into governed Omnix requests."""
from __future__ import annotations

from typing import Any

from app.capabilities import default_capability_registry
from app.capabilities.tool_call import ToolCall

from .models import AssistantToolRequest

_CANONICAL_KASA = {
    "kasa.discover_devices",
    "kasa.get_state",
    "kasa.turn_on",
    "kasa.turn_off",
}
KASA_READ_TOOLS = {"kasa.discover_devices", "kasa.get_state", "kasa_discover_devices", "kasa_get_state"}
KASA_WRITE_TOOLS = {"kasa.turn_on", "kasa.turn_off", "kasa_turn_on", "kasa_turn_off"}


def _canonical(name: str) -> str | None:
    canonical = default_capability_registry().canonical_id(name)
    return canonical if canonical in _CANONICAL_KASA else None


def is_kasa_tool_name(name: str) -> bool:
    return _canonical(name) is not None


def kasa_request_from_tool_call(
    call: ToolCall | dict[str, Any],
    *,
    session_id: str,
) -> AssistantToolRequest | None:
    if isinstance(call, ToolCall):
        name = call.name
        args = dict(call.args)
    else:
        name = str(call.get("name") or call.get("tool") or "")
        args = dict(call.get("args") or {})
    action_id = _canonical(name)
    if action_id is None:
        return None
    target = str(args.get("target") or args.get("alias") or args.get("device") or args.get("host") or "").strip()
    normalized_input = {"target": target} if target else {}
    return AssistantToolRequest(
        tool_id="kasa",
        action_id=action_id,
        session_id=session_id,
        input=normalized_input,
    )


def first_pending_kasa_write(
    mode_result: dict[str, Any],
    *,
    session_id: str,
) -> AssistantToolRequest | None:
    calls = mode_result.get("tool_calls")
    if not isinstance(calls, list):
        return None
    for call in calls:
        if not isinstance(call, dict):
            continue
        name = str(call.get("name") or call.get("tool") or "")
        if name not in KASA_WRITE_TOOLS:
            continue
        return kasa_request_from_tool_call(call, session_id=session_id)
    return None
