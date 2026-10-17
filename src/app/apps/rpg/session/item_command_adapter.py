"""Deterministic adapter from item commands to route-free session actions.

This module intentionally avoids natural-language inference. It recognizes a small,
explicit command surface used by tests, autoplay, and future route/UI callers, then
normalizes it into the central item session dispatcher payloads.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.apps.rpg.session.item_session_actions import apply_item_session_action, available_item_session_actions
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

ITEM_COMMAND_ADAPTER_SOURCE = "engine_item_command_adapter_v1"


_PICKUP_PREFIXES = ("pick up ", "pickup ", "take ", "collect ", "gather ")
_EFFECT_PREFIXES = ("use ", "activate ", "read ", "study ")
_SELL_PREFIXES = ("sell ",)
_BUY_PREFIXES = ("buy ", "purchase ")
_ATTACK_PREFIXES = ("attack ", "strike ")
_DISCOVERY_COMMANDS = {"discover recipes", "check recipes", "recipe discovery", "learn recipes"}
_REPORT_COMMANDS = {"item report", "report items", "record item report", "item coverage"}


def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _norm(value: Any) -> str:
    return _text(value).casefold()


def _append_trace(state: dict[str, Any], bucket: str, trace: dict[str, Any], *, limit: int = 50) -> None:
    mechanics = state.setdefault("mechanics", {})
    if not isinstance(mechanics, dict):
        state["mechanics"] = mechanics = {}
    traces = mechanics.setdefault(bucket, [])
    if isinstance(traces, list):
        traces.insert(0, deepcopy(trace))
        del traces[limit:]


def _strip_prefix(text: str, prefixes: tuple[str, ...]) -> tuple[str | None, str | None]:
    lowered = text.casefold()
    for prefix in prefixes:
        if lowered.startswith(prefix):
            return prefix.strip(), text[len(prefix) :].strip()
    return None, None


def _find_pickup_id(state: dict[str, Any] | None, label: str) -> str:
    if not state:
        return label
    available = available_item_session_actions(state)
    target = _norm(label)
    for raw_pickup in _safe_list(available.get("pickups")):
        pickup = _safe_dict(raw_pickup)
        if target in {_norm(pickup.get("node_id")), _norm(pickup.get("name")), _norm(pickup.get("label"))}:
            return _text(pickup.get("node_id"), label)
    return label


def _first_effect_id(effect: dict[str, Any]) -> str | None:
    if _text(effect.get("effect_id")):
        return _text(effect.get("effect_id"))
    for raw_signal in _safe_list(effect.get("effects")):
        signal = _safe_dict(raw_signal)
        signal_id = _text(signal.get("effect_id") or signal.get("signal_id") or signal.get("id"))
        if signal_id:
            return signal_id
    return None


def _find_effect(state: dict[str, Any] | None, label: str) -> tuple[str, str | None]:
    if not state:
        return label, None
    available = available_item_session_actions(state)
    target = _norm(label)
    for raw_effect in _safe_list(available.get("effects")):
        effect = _safe_dict(raw_effect)
        names = {_norm(effect.get("item_id")), _norm(effect.get("name")), _norm(effect.get("item_name"))}
        if target in names:
            return _text(effect.get("name") or effect.get("item_name") or effect.get("item_id"), label), _first_effect_id(effect)
    return label, None


def _canonical_action_name(value: Any) -> str:
    kind = _norm(value)
    if kind in {"pickup", "pick_up", "collect", "take", "gather"}:
        return "pickup"
    if kind in {"effect", "use_effect", "activate", "use", "read", "study"}:
        return "effect"
    if kind in {"recipe_discovery", "discover_recipes", "recipes", "discover"}:
        return "recipe_discovery"
    if kind in {"report", "item_report", "coverage"}:
        return "report"
    if kind in {"combat", "attack", "item_combat", "strike"}:
        return "combat"
    if kind in {"buy", "sell", "market"}:
        return kind
    return kind


def normalize_item_command(command: Any, state: dict[str, Any] | None = None) -> dict[str, Any]:
    """Normalize an explicit item command into an item-session action payload."""

    if isinstance(command, dict):
        payload = deepcopy(command)
        payload["action"] = _canonical_action_name(payload.get("action") or payload.get("kind"))
        payload.setdefault("source", "item_command_adapter")
        return {"ok": True, "action": payload, "trace": {"event": "item_command_normalized", "input_type": "dict", "action": payload.get("action"), "mechanics_source": ITEM_COMMAND_ADAPTER_SOURCE}, "mechanics_source": ITEM_COMMAND_ADAPTER_SOURCE}

    text = _text(command)
    lowered = text.casefold()
    if not text:
        return {"ok": False, "error": "empty_item_command", "mechanics_source": ITEM_COMMAND_ADAPTER_SOURCE}

    if lowered in _DISCOVERY_COMMANDS:
        action: dict[str, Any] = {"action": "recipe_discovery", "source": "item_command_adapter"}
    elif lowered in _REPORT_COMMANDS:
        action = {"action": "report", "record": True, "source": "item_command_adapter"}
    else:
        matched, label = _strip_prefix(text, _PICKUP_PREFIXES)
        if matched and label:
            action = {"action": "pickup", "node_id": _find_pickup_id(state, label), "source": "item_command_adapter"}
        else:
            matched, label = _strip_prefix(text, _EFFECT_PREFIXES)
            if matched and label:
                item_name, effect_id = _find_effect(state, label)
                action = {"action": "effect", "item_name": item_name, "source": "item_command_adapter"}
                if effect_id:
                    action["effect_id"] = effect_id
            else:
                matched, label = _strip_prefix(text, _BUY_PREFIXES)
                if matched and label:
                    action = {"action": "buy", "item_id": label, "quantity": 1, "source": "item_command_adapter"}
                else:
                    matched, label = _strip_prefix(text, _SELL_PREFIXES)
                    if matched and label:
                        action = {"action": "sell", "item_id": label, "quantity": 1, "source": "item_command_adapter"}
                    else:
                        matched, label = _strip_prefix(text, _ATTACK_PREFIXES)
                        if matched and label:
                            action = {"action": "combat", "attacker_id": "player", "defender_id": label, "source": "item_command_adapter"}
                        else:
                            return {"ok": False, "error": "unsupported_item_command", "command": text, "mechanics_source": ITEM_COMMAND_ADAPTER_SOURCE}

    trace = {"event": "item_command_normalized", "input_type": "text", "command": text, "action": action.get("action"), "mechanics_source": ITEM_COMMAND_ADAPTER_SOURCE}
    return {"ok": True, "action": action, "trace": trace, "mechanics_source": ITEM_COMMAND_ADAPTER_SOURCE}


def apply_item_command(state: dict[str, Any], command: Any) -> dict[str, Any]:
    """Normalize and apply an explicit item command to mutable session state."""

    state = _safe_dict(state)
    normalized = normalize_item_command(command, state)
    if normalized.get("ok") is not True:
        return normalized

    action = _safe_dict(normalized.get("action"))
    result = apply_item_session_action(state, action)
    command_trace = deepcopy(_safe_dict(normalized.get("trace")))
    command_trace.update(
        {
            "event": "item_command_applied",
            "ok": result.get("ok") is True,
            "result_action": result.get("session_action"),
            "mechanics_source": ITEM_COMMAND_ADAPTER_SOURCE,
        }
    )
    _append_trace(state, "item_command_traces", command_trace)
    _append_trace(state, "item_traces", command_trace)

    response = deepcopy(_safe_dict(result))
    response["command"] = command
    response["normalized_action"] = action
    response["command_trace"] = command_trace
    response["mechanics_source"] = ITEM_COMMAND_ADAPTER_SOURCE
    return response
