"""Item-aware action resolution helpers for RPG turn/session loops.

The route-facing compatibility layer can already call explicit item commands and
structured item-session actions. This module gives the future gameplay turn
resolver a compact, schema-free integration point: pass a text command or a
structured payload, and item actions are applied through the hook-aware item
wrappers while non-item inputs safely skip.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.apps.rpg.session.item_session_with_hooks import (
    apply_item_command_with_hooks,
    apply_item_session_action_with_hooks,
)
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict

ITEM_ACTION_RESOLUTION_SOURCE = "engine_item_action_resolution_v1"

COMMAND_KEYS = ("item_command", "command")
STRUCTURED_REQUEST_KEYS = ("item_action", "item_session_action", "request")
ACTION_KIND_KEYS = ("item_action_kind", "item_kind", "session_action", "kind", "action")
TOP_LEVEL_IGNORED_KEYS = {
    "action",
    "session_id",
    "item_action",
    "item_session_action",
    "request",
    "item_action_kind",
    "item_kind",
    "session_action",
    "kind",
}
SUPPORTED_ITEM_ACTIONS = frozenset(
    {
        "buy",
        "sell",
        "market",
        "pickup",
        "collect",
        "take",
        "effect",
        "use_effect",
        "activate",
        "combat",
        "attack",
        "item_combat",
        "recipe_discovery",
        "discover_recipes",
        "recipes",
        "report",
        "item_report",
    }
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _norm(value: Any) -> str:
    return _text(value).casefold()


def _first_text(payload: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = _text(payload.get(key))
        if value:
            return value
    return ""


def _nested_structured_request(payload: dict[str, Any]) -> dict[str, Any]:
    for key in STRUCTURED_REQUEST_KEYS:
        request = _safe_dict(payload.get(key))
        if request:
            return deepcopy(request)
    return {}


def _flat_structured_request(payload: dict[str, Any], action_kind: str) -> dict[str, Any]:
    request = {key: deepcopy(value) for key, value in payload.items() if key not in TOP_LEVEL_IGNORED_KEYS}
    request["action"] = action_kind
    return request


def build_item_action_resolution_plan(payload: Any) -> dict[str, Any]:
    """Resolve user/autoplay input into a deterministic item action plan.

    The plan does not mutate state. Unsupported inputs return ``handled=False``
    so outer action resolvers can continue with non-item handling.
    """

    if isinstance(payload, str):
        command = payload.strip()
        return {
            "ok": True,
            "handled": bool(command),
            "input_kind": "command" if command else "empty",
            "command": command,
            "reason": "text_item_command" if command else "empty_text",
            "mechanics_source": ITEM_ACTION_RESOLUTION_SOURCE,
        }

    request_payload = _safe_dict(payload)
    if not request_payload:
        return {
            "ok": True,
            "handled": False,
            "input_kind": "empty",
            "reason": "empty_payload",
            "mechanics_source": ITEM_ACTION_RESOLUTION_SOURCE,
        }

    for key in COMMAND_KEYS:
        if key in request_payload and _text(request_payload.get(key)):
            command = _text(request_payload.get(key))
            return {
                "ok": True,
                "handled": True,
                "input_kind": "command",
                "command": command,
                "reason": f"{key}_field",
                "mechanics_source": ITEM_ACTION_RESOLUTION_SOURCE,
            }

    nested = _nested_structured_request(request_payload)
    if nested:
        action_kind = _norm(nested.get("action") or nested.get("kind"))
        return {
            "ok": True,
            "handled": action_kind in SUPPORTED_ITEM_ACTIONS,
            "input_kind": "structured",
            "action": nested,
            "action_kind": action_kind,
            "reason": "nested_item_action" if action_kind in SUPPORTED_ITEM_ACTIONS else "unsupported_nested_action",
            "mechanics_source": ITEM_ACTION_RESOLUTION_SOURCE,
        }

    action_kind = _norm(_first_text(request_payload, ACTION_KIND_KEYS))
    if action_kind in SUPPORTED_ITEM_ACTIONS:
        request = _flat_structured_request(request_payload, action_kind)
        return {
            "ok": True,
            "handled": True,
            "input_kind": "structured",
            "action": request,
            "action_kind": action_kind,
            "reason": "flat_item_action",
            "mechanics_source": ITEM_ACTION_RESOLUTION_SOURCE,
        }

    return {
        "ok": True,
        "handled": False,
        "input_kind": "unknown",
        "action_kind": action_kind,
        "reason": "non_item_action",
        "mechanics_source": ITEM_ACTION_RESOLUTION_SOURCE,
    }


def apply_item_action_input(
    state: dict[str, Any],
    payload: Any,
    *,
    current_turn: int | None = None,
    station: str | None = None,
    genre: str = "classic_fantasy",
    diagnostics_interval: int = 10,
    maintenance_interval: int = 25,
    report_interval: int = 20,
    objective_limit: int = 5,
    record_trace: bool = True,
    record_hook_trace: bool = True,
) -> dict[str, Any]:
    """Apply item-aware input through hook-aware item wrappers.

    Non-item inputs return a skipped response instead of failing so gameplay turn
    resolvers can fall through to other action handlers.
    """

    mutable_state = state if isinstance(state, dict) else {}
    plan = build_item_action_resolution_plan(payload)
    if not plan.get("handled"):
        return {
            "ok": True,
            "handled": False,
            "skipped": True,
            "reason": plan.get("reason"),
            "plan": plan,
            "mechanics_source": ITEM_ACTION_RESOLUTION_SOURCE,
        }

    common_options: dict[str, Any] = {
        "current_turn": current_turn,
        "station": station,
        "genre": genre,
        "diagnostics_interval": diagnostics_interval,
        "maintenance_interval": maintenance_interval,
        "report_interval": report_interval,
        "objective_limit": objective_limit,
        "record_trace": record_trace,
        "record_hook_trace": record_hook_trace,
    }
    if plan.get("input_kind") == "command":
        result = apply_item_command_with_hooks(mutable_state, plan.get("command"), **common_options)
    else:
        result = apply_item_session_action_with_hooks(mutable_state, _safe_dict(plan.get("action")), **common_options)

    response = deepcopy(_safe_dict(result))
    response["handled"] = True
    response["skipped"] = False
    response["resolution_plan"] = plan
    response["resolver_mechanics_source"] = ITEM_ACTION_RESOLUTION_SOURCE
    return response
