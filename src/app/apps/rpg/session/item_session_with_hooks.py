"""Hook-aware wrappers for route-facing RPG item session actions.

The central item dispatcher and command adapter mutate item state directly. These
wrappers keep route/compat callers compact while ensuring successful item mutations
also run the deterministic item turn-hook coordinator.
"""
from __future__ import annotations

from app.runtime.clock import utc_now

from copy import deepcopy
from typing import Any

from app.apps.rpg.session import item_command_adapter, item_session_actions
from app.apps.rpg.session.item_turn_hooks import run_item_turn_hooks
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

MECHANICS_SOURCE = "engine_item_session_with_hooks_v1"
ITEM_TRACE_LIMIT = 50
TRACE_LIMIT = 20
KNOWN_RESULT_SOURCES = {
    "engine_item_session_actions_v1",
    "engine_item_command_adapter_v1",
}
HOOKED_SESSION_ACTIONS = frozenset(
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
    }
)


def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _norm(value: Any) -> str:
    return _text(value).casefold()


def _utc_now() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def _turn(state: dict[str, Any], current_turn: int | None = None) -> int:
    if current_turn is not None:
        return int(current_turn)
    return int(state.get("current_turn") or state.get("turn_count") or 0)


def _mechanics(state: dict[str, Any]) -> dict[str, Any]:
    mechanics = _safe_dict(state.get("mechanics"))
    state["mechanics"] = mechanics
    return mechanics


def _prepend_trace(mechanics: dict[str, Any], key: str, trace: dict[str, Any], *, limit: int) -> None:
    mechanics[key] = [deepcopy(trace), *_safe_list(mechanics.get(key))][: max(1, int(limit or 1))]


def _result_source_is_real(result: dict[str, Any]) -> bool:
    return _text(result.get("mechanics_source")) in KNOWN_RESULT_SOURCES


def _action_kind(action: dict[str, Any] | None, result: dict[str, Any]) -> str:
    request = _safe_dict(action)
    return _norm(
        result.get("session_action")
        or request.get("action")
        or request.get("kind")
        or _safe_dict(result.get("normalized_action")).get("action")
    )


def _compat_failure(result: dict[str, Any]) -> dict[str, Any]:
    failure = deepcopy(_safe_dict(result))
    if failure.get("error") == "unsupported_item_command":
        failure.pop("mechanics_source", None)
    return failure


def run_item_session_action_hooks(
    state: dict[str, Any],
    *,
    action: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
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
    """Run turn hooks after successful dispatcher-backed item session actions."""

    mutable_state = state if isinstance(state, dict) else {}
    response = _safe_dict(result)
    kind = _action_kind(action, response)
    turn = _turn(mutable_state, current_turn)
    if kind not in HOOKED_SESSION_ACTIONS:
        return {
            "ok": True,
            "skipped": True,
            "turn": turn,
            "action": kind,
            "reason": "non_hooked_item_session_action",
            "recorded": False,
            "mechanics_source": MECHANICS_SOURCE,
        }
    if response and not _result_source_is_real(response):
        return {
            "ok": True,
            "skipped": True,
            "turn": turn,
            "action": kind,
            "reason": "non_engine_result_source",
            "recorded": False,
            "mechanics_source": MECHANICS_SOURCE,
        }

    hook_result = run_item_turn_hooks(
        mutable_state,
        current_turn=turn,
        station=station,
        genre=genre,
        diagnostics_interval=diagnostics_interval,
        maintenance_interval=maintenance_interval,
        report_interval=report_interval,
        objective_limit=objective_limit,
        record_trace=record_hook_trace,
    )
    trace = {
        "event": "item_session_action_hooks_ran",
        "action": kind,
        "turn": turn,
        "executed_actions": list(hook_result.get("executed_actions", [])),
        "hook_recorded": bool(hook_result.get("recorded")),
        "mechanics_source": MECHANICS_SOURCE,
        "timestamp": _utc_now(),
    }
    if record_trace:
        mechanics = _mechanics(mutable_state)
        _prepend_trace(mechanics, "item_session_action_hook_traces", trace, limit=TRACE_LIMIT)
        _prepend_trace(mechanics, "item_traces", trace, limit=ITEM_TRACE_LIMIT)

    return {
        "ok": True,
        "skipped": False,
        "turn": turn,
        "action": kind,
        "hook_result": hook_result,
        "executed_actions": list(hook_result.get("executed_actions", [])),
        "mechanics_trace": trace,
        "recorded": bool(record_trace),
        "detail": f"Item session hooks ran after {kind} on turn {turn}.",
        "mechanics_source": MECHANICS_SOURCE,
    }


def apply_item_session_action_with_hooks(
    state: dict[str, Any],
    action: dict[str, Any],
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
    """Apply a structured item session action and run item hooks on success."""

    request = _safe_dict(action)
    result = item_session_actions.apply_item_session_action(state, request)
    if result.get("ok") is not True:
        return result
    hook_result = run_item_session_action_hooks(
        state,
        action=request,
        result=result,
        current_turn=current_turn,
        station=station,
        genre=genre,
        diagnostics_interval=diagnostics_interval,
        maintenance_interval=maintenance_interval,
        report_interval=report_interval,
        objective_limit=objective_limit,
        record_trace=record_trace,
        record_hook_trace=record_hook_trace,
    )
    response = deepcopy(_safe_dict(result))
    response["item_hook_result"] = hook_result
    response["hook_mechanics_source"] = MECHANICS_SOURCE
    return response


def apply_item_command_with_hooks(
    state: dict[str, Any],
    command: Any,
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
    """Apply an explicit item command and run item hooks on success."""

    result = item_command_adapter.apply_item_command(state, command)
    if result.get("ok") is not True:
        return _compat_failure(result)
    action = _safe_dict(result.get("normalized_action"))
    hook_result = run_item_session_action_hooks(
        state,
        action=action,
        result=result,
        current_turn=current_turn,
        station=station,
        genre=genre,
        diagnostics_interval=diagnostics_interval,
        maintenance_interval=maintenance_interval,
        report_interval=report_interval,
        objective_limit=objective_limit,
        record_trace=record_trace,
        record_hook_trace=record_hook_trace,
    )
    response = deepcopy(_safe_dict(result))
    response["item_hook_result"] = hook_result
    response["hook_mechanics_source"] = MECHANICS_SOURCE
    return response
