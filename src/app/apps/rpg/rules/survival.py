"""Deterministic RPG survival state model.

Bundle BA keeps survival in the real runtime package instead of autoplay
fragment overlays.  The functions here are pure, bounded, and JSON-safe so
session runtime code can persist them directly in ``simulation_state``.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional
from app.apps.rpg.foundation.safe_values import mapping_copy as _safe_dict

SURVIVAL_STATE_KEY = "survival"
SURVIVAL_EVENT_LIMIT = 32
SURVIVAL_MIN = 0
SURVIVAL_MAX = 100

DEFAULT_SURVIVAL_STATE: Dict[str, Any] = {
    "enabled": True,
    "hunger": 0,
    "thirst": 0,
    "fatigue": 0,
    "last_food_turn": None,
    "last_water_turn": None,
    "last_rest_turn": None,
    "events": [],
}

DEFAULT_SURVIVAL_TICK_RATES: Dict[str, int] = {
    "hunger": 1,
    "thirst": 2,
    "fatigue": 1,
}

SURVIVAL_ACTION_EFFECTS: Dict[str, Dict[str, int]] = {
    "eat_food": {"hunger": -30},
    "eat_rations": {"hunger": -30},
    "drink_water": {"thirst": -30},
    "drink_from_waterskin": {"thirst": -30},
    "rest": {"fatigue": -35},
    "sleep": {"fatigue": -50},
    "make_camp": {"fatigue": -35},
}

_NEED_KEYS = ("hunger", "thirst", "fatigue")
_LAST_TURN_BY_NEED = {
    "hunger": "last_food_turn",
    "thirst": "last_water_turn",
    "fatigue": "last_rest_turn",
}


def _safe_bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "enabled"}:
            return True
        if normalized in {"0", "false", "no", "off", "disabled"}:
            return False
    return bool(value)


def _clamp_need(value: Any) -> int:
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        numeric = 0
    return max(SURVIVAL_MIN, min(SURVIVAL_MAX, numeric))


def _bounded_events(events: Iterable[Any]) -> List[Dict[str, Any]]:
    bounded: List[Dict[str, Any]] = []
    for event in events:
        if not isinstance(event, Mapping):
            continue
        cleaned = dict(event)
        cleaned.setdefault("source", "runtime_survival_state")
        bounded.append(cleaned)
    return bounded[-SURVIVAL_EVENT_LIMIT:]


def normalize_survival_state(value: Any, *, enabled_default: bool = True) -> Dict[str, Any]:
    """Return a bounded, JSON-serializable survival state.

    Unknown keys are intentionally dropped so the persisted state cannot grow
    without bounds or smuggle LLM-generated effects into the runtime contract.
    """
    raw = _safe_dict(value)
    state = deepcopy(DEFAULT_SURVIVAL_STATE)
    state["enabled"] = _safe_bool(raw.get("enabled"), enabled_default)
    for key in _NEED_KEYS:
        state[key] = _clamp_need(raw.get(key, state[key]))
    for key in ("last_food_turn", "last_water_turn", "last_rest_turn"):
        turn_value = raw.get(key)
        if turn_value is None or turn_value == "":
            state[key] = None
        else:
            try:
                state[key] = int(turn_value)
            except (TypeError, ValueError):
                state[key] = None
    state["events"] = _bounded_events(raw.get("events", []))
    return state


def ensure_survival_state(simulation_state: MutableMapping[str, Any], *, enabled_default: bool = True) -> Dict[str, Any]:
    """Ensure ``simulation_state['survival']`` exists and is normalized."""
    state = normalize_survival_state(
        simulation_state.get(SURVIVAL_STATE_KEY),
        enabled_default=enabled_default,
    )
    simulation_state[SURVIVAL_STATE_KEY] = state
    return state


def survival_state_snapshot(simulation_state: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a copy of the current normalized survival state without mutation."""
    return normalize_survival_state(_safe_dict(simulation_state).get(SURVIVAL_STATE_KEY))


def _append_event(state: Dict[str, Any], event: Mapping[str, Any]) -> None:
    events = _bounded_events(state.get("events", []))
    cleaned = dict(event)
    cleaned.setdefault("source", "runtime_survival_state")
    events.append(cleaned)
    state["events"] = events[-SURVIVAL_EVENT_LIMIT:]


def tick_survival_state(
    simulation_state: MutableMapping[str, Any],
    *,
    tick: Optional[int] = None,
    turns: int = 1,
    rates: Optional[Mapping[str, int]] = None,
    reason: str = "player_turn",
) -> Dict[str, Any]:
    """Advance survival needs deterministically for elapsed turns.

    Needs only rise here. Concrete actions such as eating/drinking/resting are
    applied through ``apply_survival_effect`` so the turn contract can attribute
    their authoritative effects separately from passive pressure.
    """
    state = ensure_survival_state(simulation_state)
    if not state.get("enabled", True):
        return state

    try:
        turn_count = max(0, int(turns))
    except (TypeError, ValueError):
        turn_count = 1
    if turn_count <= 0:
        return state

    merged_rates = dict(DEFAULT_SURVIVAL_TICK_RATES)
    for key, value in _safe_dict(rates).items():
        if key not in _NEED_KEYS:
            continue
        try:
            merged_rates[key] = max(0, int(value))
        except (TypeError, ValueError):
            merged_rates[key] = DEFAULT_SURVIVAL_TICK_RATES[key]

    before = {key: state[key] for key in _NEED_KEYS}
    for key in _NEED_KEYS:
        state[key] = _clamp_need(state[key] + merged_rates[key] * turn_count)

    after = {key: state[key] for key in _NEED_KEYS}
    if after != before:
        _append_event(
            state,
            {
                "kind": "survival_tick",
                "reason": reason,
                "tick": tick,
                "turns": turn_count,
                "before": before,
                "after": after,
                "rates": {key: merged_rates[key] for key in _NEED_KEYS},
            },
        )
    simulation_state[SURVIVAL_STATE_KEY] = state
    return state


def apply_survival_effect(
    simulation_state: MutableMapping[str, Any],
    *,
    kind: str,
    effects: Optional[Mapping[str, int]] = None,
    tick: Optional[int] = None,
    source: str = "runtime_action_resolver",
) -> Dict[str, Any]:
    """Apply authoritative survival effects from a concrete runtime action."""
    state = ensure_survival_state(simulation_state)
    if not state.get("enabled", True):
        return {
            "ok": False,
            "reason": "survival_disabled",
            "survival": deepcopy(state),
            "source": source,
        }

    action_kind = str(kind or "").strip().lower().replace(" ", "_")
    raw_effects = dict(effects or SURVIVAL_ACTION_EFFECTS.get(action_kind, {}))
    if not raw_effects:
        return {
            "ok": False,
            "reason": "unknown_survival_effect",
            "action": action_kind,
            "survival": deepcopy(state),
            "source": source,
        }

    before = {key: state[key] for key in _NEED_KEYS}
    applied: Dict[str, int] = {}
    for key, raw_delta in raw_effects.items():
        if key not in _NEED_KEYS:
            continue
        try:
            delta = int(raw_delta)
        except (TypeError, ValueError):
            continue
        old_value = state[key]
        state[key] = _clamp_need(old_value + delta)
        applied[f"{key}_delta"] = state[key] - old_value
        last_turn_key = _LAST_TURN_BY_NEED.get(key)
        if delta < 0 and last_turn_key:
            state[last_turn_key] = tick

    if not applied:
        return {
            "ok": False,
            "reason": "no_valid_survival_effects",
            "action": action_kind,
            "survival": deepcopy(state),
            "source": source,
        }

    after = {key: state[key] for key in _NEED_KEYS}
    event = {
        "kind": action_kind,
        "tick": tick,
        "before": before,
        "after": after,
        "effects": dict(applied),
        "source": source,
    }
    _append_event(state, event)
    simulation_state[SURVIVAL_STATE_KEY] = state
    return {
        "ok": True,
        "action_category": "survival",
        "action": action_kind,
        "effects": dict(applied),
        "survival_event": event,
        "survival": deepcopy(state),
        "source": source,
    }


def survival_pressure(state: Mapping[str, Any]) -> Dict[str, str]:
    """Classify survival needs for future action-context surfacing."""
    normalized = normalize_survival_state(state)
    pressure: Dict[str, str] = {}
    for key in _NEED_KEYS:
        value = int(normalized[key])
        if value >= 75:
            pressure[key] = "critical"
        elif value >= 50:
            pressure[key] = "high"
        elif value >= 25:
            pressure[key] = "moderate"
        else:
            pressure[key] = "low"
    return pressure
