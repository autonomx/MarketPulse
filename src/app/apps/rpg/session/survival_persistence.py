"""Bundle BE — canonical survival save/load normalization.

The BA runtime state lives at ``simulation_state['survival']``.  This module is
used by session save/load/export/import boundaries so malformed, legacy, or
LLM-expanded survival payloads cannot become persistent truth.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from app.apps.rpg.rules.survival import SURVIVAL_STATE_KEY, normalize_survival_state
from app.apps.rpg.foundation.safe_values import mapping_copy as _safe_dict
from app.apps.rpg.foundation.safe_values import copy_state

SURVIVAL_PERSISTENCE_SOURCE = "runtime_survival_persistence"

_NEED_KEYS: tuple[str, str, str] = ("hunger", "thirst", "fatigue")
_LAST_TURN_KEYS: tuple[str, str, str] = (
    "last_food_turn",
    "last_water_turn",
    "last_rest_turn",
)


def _has_any_need(value: Mapping[str, Any]) -> bool:
    return any(key in value for key in _NEED_KEYS)


def _legacy_survival_candidate(simulation_state: Mapping[str, Any]) -> dict[str, Any]:
    simulation_state = _safe_dict(simulation_state)
    player_state = _safe_dict(simulation_state.get("player_state"))
    climate = _safe_dict(simulation_state.get("climate_survival"))
    player_climate = _safe_dict(player_state.get("climate_survival"))

    candidates = (
        _safe_dict(climate.get("survival")),
        _safe_dict(simulation_state.get("needs")),
        _safe_dict(player_state.get("needs")),
        _safe_dict(player_climate.get("survival")),
    )
    for candidate in candidates:
        if _has_any_need(candidate):
            return candidate
    return {}


def _canonical_survival_seed(simulation_state: Mapping[str, Any]) -> dict[str, Any]:
    simulation_state = _safe_dict(simulation_state)
    root_survival = _safe_dict(simulation_state.get(SURVIVAL_STATE_KEY))
    if root_survival:
        return root_survival
    return _legacy_survival_candidate(simulation_state)


def normalize_survival_for_persistence(simulation_state: Mapping[str, Any]) -> dict[str, Any]:
    """Return simulation_state with bounded canonical survival state.

    Unknown survival keys are pruned by ``normalize_survival_state``.  Legacy
    climate/needs fields are left untouched for backward compatibility, but they
    no longer act as authoritative state after canonical survival is present.
    """
    return _normalize_owned_simulation_state(deepcopy(_safe_dict(simulation_state)))


def _normalize_owned_simulation_state(normalized: dict[str, Any]) -> dict[str, Any]:
    state = normalize_survival_state(_canonical_survival_seed(normalized))
    normalized[SURVIVAL_STATE_KEY] = state
    return normalized


def normalize_session_survival_for_persistence(session: Mapping[str, Any], *, owned: bool = False) -> dict[str, Any]:
    """Normalize a whole session at save/load/export/import boundaries.

    ``owned``: the caller owns ``session`` (it was just decoded from storage), so
    it is normalized in place instead of copied.
    """
    normalized_session = _safe_dict(session) if owned else copy_state(_safe_dict(session))
    # The copy owns its simulation state: normalize it in place rather than copying it again.
    normalized_session["simulation_state"] = _normalize_owned_simulation_state(
        _safe_dict(normalized_session.get("simulation_state"))
    )
    return normalized_session


