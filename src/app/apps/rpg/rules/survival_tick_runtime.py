"""Bundle BF — deterministic survival runtime tick integration.

This module advances canonical BA survival pressure once per authoritative live
turn.  It is intentionally independent from LLM/autoplay code and records its
own bounded evidence so reports, UI, and tests can see why hunger/thirst/fatigue
changed.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, Mapping, MutableMapping, Optional

from app.apps.rpg.rules.survival import DEFAULT_SURVIVAL_TICK_RATES, tick_survival_state
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

SURVIVAL_TICK_SOURCE = "runtime_survival_tick"
SURVIVAL_TICK_HISTORY_LIMIT = 64

_WAIT_WORDS = ("wait", "listen", "observe", "look around", "keep watch", "stand guard")
_TRAVEL_WORDS = ("travel", "walk", "go to", "head to", "leave for", "ride", "march", "journey")
_REST_WORDS = ("rest", "sleep", "nap", "make camp", "camp for the night")
_DIRECT_SURVIVAL_WORDS = (
    "drink",
    "eat",
    "ration",
    "rations",
    "water",
    "waterskin",
    "buy water",
    "buy rations",
    "rest",
    "sleep",
    "make camp",
)

SURVIVAL_TICK_RATE_PROFILES: Dict[str, Dict[str, int]] = {
    "standard_turn": dict(DEFAULT_SURVIVAL_TICK_RATES),
    "wait": {"hunger": 1, "thirst": 2, "fatigue": 1},
    "travel": {"hunger": 2, "thirst": 3, "fatigue": 2},
    "rest": {"hunger": 1, "thirst": 1, "fatigue": 0},
    "sleep": {"hunger": 2, "thirst": 2, "fatigue": 0},
    "survival_action": {"hunger": 0, "thirst": 0, "fatigue": 0},
}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _norm(value: Any) -> str:
    return " ".join(_safe_str(value).strip().lower().replace("_", " ").split())


def _contains_any(text: str, phrases: Iterable[str]) -> bool:
    return any(phrase in text for phrase in phrases if phrase)


def _candidate_text_values(*containers: Mapping[str, Any]) -> Iterable[str]:
    for container in containers:
        container = _safe_dict(container)
        if not container:
            continue
        for key in ("player_input", "input", "raw_input", "command", "action", "summary"):
            value = _safe_str(container.get(key)).strip()
            if value:
                yield value
        for key in (
            "semantic_action_v2",
            "semantic_action",
            "interaction_result",
            "general_interaction_result",
            "resolved_result",
            "resolved_action",
        ):
            nested = _safe_dict(container.get(key))
            for nested_key in ("player_input", "input", "raw_input", "command", "action", "kind", "target_ref", "reason"):
                value = _safe_str(nested.get(nested_key)).strip()
                if value:
                    yield value


def _walk_dicts(value: Any, *, depth: int = 0, max_depth: int = 5) -> Iterable[Dict[str, Any]]:
    if depth > max_depth:
        return
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk_dicts(nested, depth=depth + 1, max_depth=max_depth)
    elif isinstance(value, list):
        for item in value[:20]:
            yield from _walk_dicts(item, depth=depth + 1, max_depth=max_depth)


def _find_survival_result(*containers: Mapping[str, Any]) -> Dict[str, Any]:
    for container in containers:
        for item in _walk_dicts(container):
            survival_result = _safe_dict(item.get("survival_result"))
            if survival_result:
                return survival_result
            if _safe_str(item.get("action_category")) == "survival" and item.get("action"):
                return item
    return {}


def _extract_turn_id(authoritative_result: Mapping[str, Any], result_payload: Mapping[str, Any], turn_contract: Mapping[str, Any]) -> str:
    authoritative_result = _safe_dict(authoritative_result)
    authoritative = _safe_dict(authoritative_result.get("authoritative"))
    result_sub = _safe_dict(authoritative_result.get("result"))
    for container in (result_payload, turn_contract, result_sub, authoritative, authoritative_result):
        container = _safe_dict(container)
        value = _safe_str(container.get("turn_id") or container.get("id")).strip()
        if value:
            return value
    tick = _safe_int(
        _safe_dict(result_payload).get("tick")
        or _safe_dict(turn_contract).get("tick")
        or result_sub.get("tick")
        or authoritative.get("tick"),
        0,
    )
    return f"tick:{tick}"


def _extract_tick(authoritative_result: Mapping[str, Any], session: Mapping[str, Any], result_payload: Mapping[str, Any], turn_contract: Mapping[str, Any]) -> int:
    authoritative_result = _safe_dict(authoritative_result)
    authoritative = _safe_dict(authoritative_result.get("authoritative"))
    result_sub = _safe_dict(authoritative_result.get("result"))
    runtime_state = _safe_dict(_safe_dict(session).get("runtime_state"))
    simulation_state = _safe_dict(_safe_dict(session).get("simulation_state"))
    return _safe_int(
        _safe_dict(result_payload).get("tick")
        or _safe_dict(turn_contract).get("tick")
        or result_sub.get("tick")
        or authoritative.get("tick")
        or runtime_state.get("tick")
        or simulation_state.get("tick"),
        0,
    )


def classify_survival_tick_context(
    *,
    player_input: str = "",
    survival_result: Optional[Mapping[str, Any]] = None,
    resolved_result: Optional[Mapping[str, Any]] = None,
    turn_contract: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    survival_result = _safe_dict(survival_result)
    resolved_result = _safe_dict(resolved_result)
    turn_contract = _safe_dict(turn_contract)
    action = _norm(survival_result.get("action"))
    text = _norm(" ".join([player_input, *_candidate_text_values(resolved_result, turn_contract)]))

    if survival_result.get("action_category") == "survival" or action:
        profile = "survival_action"
        skip = True
        reason = "direct_survival_action"
    elif _contains_any(text, _REST_WORDS):
        profile = "rest" if "sleep" not in text else "sleep"
        skip = False
        reason = f"{profile}_turn"
    elif _contains_any(text, _TRAVEL_WORDS):
        profile = "travel"
        skip = False
        reason = "travel_turn"
    elif _contains_any(text, _WAIT_WORDS):
        profile = "wait"
        skip = False
        reason = "wait_turn"
    elif _contains_any(text, _DIRECT_SURVIVAL_WORDS):
        profile = "survival_action"
        skip = True
        reason = "survival_text_without_runtime_result"
    else:
        profile = "standard_turn"
        skip = False
        reason = "standard_turn"

    return {
        "profile": profile,
        "rates": deepcopy(SURVIVAL_TICK_RATE_PROFILES[profile]),
        "skip_tick": bool(skip),
        "reason": reason,
        "source": SURVIVAL_TICK_SOURCE,
    }


def _history_has_turn(runtime_state: Mapping[str, Any], turn_id: str) -> bool:
    for row in _safe_list(_safe_dict(runtime_state).get("survival_tick_history")):
        row = _safe_dict(row)
        if _safe_str(row.get("turn_id")) == turn_id:
            return True
    return False


def _append_history(runtime_state: MutableMapping[str, Any], row: Mapping[str, Any]) -> Dict[str, Any]:
    runtime_state = dict(_safe_dict(runtime_state))
    history = _safe_list(runtime_state.get("survival_tick_history"))
    history.append(deepcopy(_safe_dict(row)))
    runtime_state["survival_tick_history"] = history[-SURVIVAL_TICK_HISTORY_LIMIT:]
    return runtime_state


def apply_survival_runtime_tick(
    *,
    authoritative_result: Mapping[str, Any],
    session: MutableMapping[str, Any],
    turn_contract: MutableMapping[str, Any],
    result_payload: MutableMapping[str, Any],
    resolved_result: Mapping[str, Any],
) -> Dict[str, Any]:
    """Apply one passive survival tick to the returned live-turn session."""
    session = dict(_safe_dict(session))
    simulation_state = dict(_safe_dict(session.get("simulation_state")))
    runtime_state = dict(_safe_dict(session.get("runtime_state")))
    turn_contract = dict(_safe_dict(turn_contract))
    result_payload = dict(_safe_dict(result_payload))
    resolved_result = _safe_dict(resolved_result)

    turn_id = _extract_turn_id(authoritative_result, result_payload, turn_contract)
    tick = _extract_tick(authoritative_result, session, result_payload, turn_contract)
    survival_result = _find_survival_result(authoritative_result, result_payload, turn_contract, resolved_result)
    player_input = " ".join(_candidate_text_values(authoritative_result, result_payload, turn_contract, resolved_result))
    context = classify_survival_tick_context(
        player_input=player_input,
        survival_result=survival_result,
        resolved_result=resolved_result,
        turn_contract=turn_contract,
    )

    before = deepcopy(_safe_dict(simulation_state.get("survival")))
    already_applied = _history_has_turn(runtime_state, turn_id)
    tick_result = {
        "applied": False,
        "skipped": False,
        "turn_id": turn_id,
        "tick": tick,
        "before": before,
        "after": before,
        "context": deepcopy(context),
        "source": SURVIVAL_TICK_SOURCE,
    }

    if already_applied:
        tick_result["skipped"] = True
        tick_result["reason"] = "already_applied_for_turn"
    elif context.get("skip_tick"):
        tick_result["skipped"] = True
        tick_result["reason"] = _safe_str(context.get("reason")) or "tick_skipped"
    else:
        after_state = tick_survival_state(
            simulation_state,
            tick=tick,
            turns=1,
            rates=_safe_dict(context.get("rates")),
            reason=_safe_str(context.get("reason")) or "standard_turn",
        )
        tick_result["applied"] = True
        tick_result["reason"] = _safe_str(context.get("reason")) or "standard_turn"
        tick_result["after"] = deepcopy(after_state)
        simulation_state["survival"] = after_state

    runtime_state = _append_history(runtime_state, tick_result)
    session["simulation_state"] = simulation_state
    session["runtime_state"] = runtime_state

    turn_contract["survival_tick_result"] = deepcopy(tick_result)
    result_payload["survival_tick_result"] = deepcopy(tick_result)
    resolved_copy = dict(_safe_dict(result_payload.get("resolved_result") or resolved_result))
    resolved_copy["survival_tick_result"] = deepcopy(tick_result)
    result_payload["resolved_result"] = resolved_copy

    return {
        "session": session,
        "turn_contract": turn_contract,
        "result_payload": result_payload,
        "survival_tick_result": tick_result,
        "source": SURVIVAL_TICK_SOURCE,
    }
