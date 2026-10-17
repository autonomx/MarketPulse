"""Canonical visible-response guards for RPG turns.

This module is deliberately pure and conservative. Raw provider metadata such as
``tool_calls: []`` may be useful diagnostics, but it must never become player-
facing RPG text.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any
from app.apps.rpg.foundation.safe_values import copy_state

_INVALID_VISIBLE_STRINGS = {
    "",
    "[]",
    "[ ]",
    "{}",
    "{ }",
    "null",
    "none",
    "undefined",
    "nan",
    "[object object]",
}
_PROVIDER_METADATA_KEYS = {
    "tool_calls",
    "reasoning_content",
    "usage",
    "stats",
    "system_fingerprint",
    "logprobs",
    "finish_reason",
}
_VISIBLE_STRING_KEYS = (
    "visible_text",
    "final_narration",
    "narration",
    "summary",
    "text",
    "content",
    "action",
)
_PLACEHOLDER_MARKERS = (
    "npc line will be filled",
    "filled upon runtime resolution",
    "intent is to ask",
    "runtime resolution",
    "placeholder",
    "todo",
)
_WORLD_INFO_TERMS = ("rumor", "rumour", "gossip", "news", "heard", "word around", "word is")


def _d(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _l(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _s(value: Any) -> str:
    return str(value) if value is not None else ""


def _norm_text(value: Any) -> str:
    return _s(value).strip().casefold()


def _is_invalid_string(value: Any) -> bool:
    text = _norm_text(value)
    if text in _INVALID_VISIBLE_STRINGS:
        return True
    if not text:
        return True
    if text.startswith("model generated tool calls"):
        return True
    if text in {"tool_calls: []", "\"tool_calls\": []"}:
        return True
    alpha_num = sum(1 for char in text if char.isalnum())
    return len(text) > 1 and alpha_num == 0


def extract_provider_message_content(response: Any) -> str:
    """Extract assistant message content from OpenAI/LM Studio shaped payloads.

    Explicitly ignores provider metadata fields such as ``tool_calls``. The bug
    this guards against was selecting ``tool_calls: []`` instead of
    ``choices[0].message.content``.
    """

    data = _d(response)
    if not data:
        return ""

    choices = _l(data.get("choices"))
    if choices:
        first = _d(choices[0])
        message = _d(first.get("message"))
        content = message.get("content")
        if isinstance(content, str) and not _is_invalid_string(content):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = _s(item.get("text") or item.get("content")).strip()
                    if text:
                        parts.append(text)
            joined = "\n".join(part.strip() for part in parts if part.strip()).strip()
            if joined and not _is_invalid_string(joined):
                return joined

    message = _d(data.get("message"))
    content = message.get("content")
    if isinstance(content, str) and not _is_invalid_string(content):
        return content.strip()

    for key in ("text", "content"):
        value = data.get(key)
        if isinstance(value, str) and not _is_invalid_string(value):
            return value.strip()
    return ""


def visible_response_text(value: Any) -> str:
    """Return canonical user-visible text from a bounded RPG result object."""

    if value is None or isinstance(value, (list, tuple, set)):
        return ""
    if isinstance(value, str):
        return "" if _is_invalid_string(value) else value.strip()

    data = _d(value)
    if not data:
        return ""

    provider_content = extract_provider_message_content(data)
    if provider_content:
        return provider_content

    visible_response = _d(data.get("visible_response"))
    if visible_response:
        text = visible_response_text(visible_response)
        if text:
            return text

    nested_result = _d(data.get("result"))
    if nested_result:
        text = visible_response_text(nested_result)
        if text:
            return text

    authoritative = _d(data.get("authoritative"))
    if authoritative:
        text = visible_response_text(authoritative)
        if text:
            return text

    npc = _d(data.get("npc"))
    speaker = _s(npc.get("speaker")).strip()
    line = _s(npc.get("line")).strip()
    if line and not _is_invalid_string(line):
        return f"{speaker}: {line}" if speaker else line

    for key in _VISIBLE_STRING_KEYS:
        value_for_key = data.get(key)
        if isinstance(value_for_key, str) and not _is_invalid_string(value_for_key):
            return value_for_key.strip()

    return ""


def is_invalid_visible_value(value: Any) -> bool:
    """True when a candidate must not be shown as RPG-visible text."""

    if value is None:
        return True
    if isinstance(value, str):
        return _is_invalid_string(value)
    if isinstance(value, (list, tuple, set)):
        return True
    data = _d(value)
    if not data:
        return True
    if set(data.keys()).issubset(_PROVIDER_METADATA_KEYS):
        return True
    return not bool(visible_response_text(data))


def invalid_visible_selection_reason(selection: Any) -> str:
    """Return a rejection reason for a consumable first-call selection."""

    data = _d(selection)
    if not data or not data.get("consumable"):
        return ""
    visible = _d(data.get("visible_response")) or data
    if is_invalid_visible_value(visible):
        return "invalid_visible_response_text"
    return ""


def validate_first_call_selection(selection: Any) -> dict[str, Any]:
    """Reject placeholder, parse-noise and world-info selections at the owner."""

    data = _d(selection)
    if not data.get("consumable"):
        return deepcopy(data)
    if _selection_has_placeholder_line(data):
        source = _s(data.get("source") or "first_call_dialogue_v1")
        return {
            "consumable": False,
            "reason": "no_safe_non_stateful_visible_response",
            "rejection_reasons": [f"{source}:placeholder_npc_line"],
            "rejected_visible_response": deepcopy(data.get("visible_response") or {}),
            "source": "first_call_dialogue_placeholder_guard_v1",
        }
    reason = invalid_visible_selection_reason(data) or _world_info_requires_runtime_reason(data)
    if not reason:
        return deepcopy(data)
    source = _s(data.get("source") or "first_call_dialogue_v1")
    return {
        "consumable": False,
        "reason": "no_safe_non_stateful_visible_response",
        "rejection_reasons": [f"{source}:{reason}"],
        "rejected_visible_response": deepcopy(data.get("visible_response") or {}),
        "source": "visible_response_contract_guard_v1",
    }


def _selection_has_placeholder_line(selection: dict[str, Any]) -> bool:
    visible = _d(selection.get("visible_response"))
    npc = _d(visible.get("npc"))
    selected_npc = _d(selection.get("npc"))
    line = npc.get("line") or selected_npc.get("line")
    text = _s(line).casefold().strip()
    return (
        text.startswith("[")
        and text.endswith("]")
        and any(marker in text for marker in _PLACEHOLDER_MARKERS)
    )


def _world_info_requires_runtime_reason(selection: dict[str, Any]) -> str:
    diagnostics = _d(selection.get("first_call_grounding_diagnostics"))
    advisory = _d(selection.get("advisory"))
    if not diagnostics:
        diagnostics = _d(advisory.get("first_call_grounding_diagnostics"))
    packet = _d(diagnostics.get("turn_grounding_packet"))
    player_input = _s(packet.get("player_input")).casefold()
    if not player_input or not any(term in player_input for term in _WORLD_INFO_TERMS):
        return ""
    priority = _d(packet.get("priority_context"))
    npc_context = _d(packet.get("npc_context"))
    addressed_ids = _l(priority.get("addressed_npc_ids"))
    addressed_profiles = _l(npc_context.get("addressed_npcs"))
    if addressed_ids or addressed_profiles or selection.get("npc"):
        return "world_info_inquiry_requires_runtime"
    return ""


def build_visible_turn_record(result: Any, *, player_input: str = "") -> dict[str, Any]:
    data = _d(result)
    visible_text = visible_response_text(data)
    rejected: list[dict[str, Any]] = []
    for label, candidate in (
        ("top_level_tool_calls", data.get("tool_calls")),
        ("top_level_text", data.get("text")),
        ("top_level_content", data.get("content")),
        ("visible_response", data.get("visible_response")),
    ):
        if candidate is not None and is_invalid_visible_value(candidate):
            rejected.append({"source": label, "reason": "invalid_visible_value", "value_type": type(candidate).__name__})
    return {
        "format_version": "rpg_visible_turn_record_v1",
        "player_input": _s(player_input),
        "visible_text": visible_text,
        "visible_text_valid": bool(visible_text),
        "rejected_visible_candidates": rejected,
        "source": "visible_response_contract_v1",
    }


def attach_visible_turn_record(result: Any, *, player_input: str = "") -> Any:
    if not isinstance(result, dict):
        return result
    data = copy_state(result)
    record = build_visible_turn_record(data, player_input=player_input or _s(data.get("player_input")))
    data["visible_turn_record"] = record
    if record["visible_text"]:
        data["visible_text"] = record["visible_text"]
    return data
