from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

SOURCE = "deterministic_phase4_season_weather_expansion"
DEFAULT_WEATHER_ID = "weather:clear_mild"
DEFAULT_SEASON = "early_autumn"
DAYS_PER_SEASON = 30
SEASON_ORDER = ["early_autumn", "late_autumn", "winter", "spring", "summer"]

WEATHER_PROFILES: Dict[str, Dict[str, Any]] = {
    "weather:clear_mild": {
        "weather_id": "weather:clear_mild",
        "label": "Clear and mild",
        "visibility": "clear",
        "travel_fatigue_delta": 0,
        "survival_thirst_delta": 0,
        "description": "The air is clear and mild enough for ordinary travel.",
        "source": SOURCE,
    },
    "weather:cool_breeze": {
        "weather_id": "weather:cool_breeze",
        "label": "Cool breeze",
        "visibility": "clear",
        "travel_fatigue_delta": 0,
        "survival_thirst_delta": 0,
        "description": "A cool breeze moves through the road without changing travel costs.",
        "source": SOURCE,
    },
    "weather:light_rain": {
        "weather_id": "weather:light_rain",
        "label": "Light rain",
        "visibility": "reduced",
        "travel_fatigue_delta": 1,
        "survival_thirst_delta": -1,
        "description": "Light rain dampens the road and can add minor fatigue pressure.",
        "source": SOURCE,
    },
    "weather:cold_mist": {
        "weather_id": "weather:cold_mist",
        "label": "Cold mist",
        "visibility": "limited",
        "travel_fatigue_delta": 1,
        "survival_thirst_delta": 0,
        "description": "Cold mist limits visibility and can add minor fatigue pressure.",
        "source": SOURCE,
    },
    "weather:dry_heat": {
        "weather_id": "weather:dry_heat",
        "label": "Dry heat",
        "visibility": "clear",
        "travel_fatigue_delta": 1,
        "survival_thirst_delta": 1,
        "description": "Dry heat is clear but can increase thirst and fatigue pressure.",
        "source": SOURCE,
    },
}

SEASON_WEATHER_TABLE: Dict[str, List[str]] = {
    "early_autumn": ["weather:clear_mild", "weather:cool_breeze", "weather:light_rain"],
    "late_autumn": ["weather:cool_breeze", "weather:light_rain", "weather:cold_mist"],
    "winter": ["weather:cold_mist", "weather:cool_breeze", "weather:light_rain"],
    "spring": ["weather:light_rain", "weather:clear_mild", "weather:cool_breeze"],
    "summer": ["weather:clear_mild", "weather:dry_heat", "weather:cool_breeze"],
}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _positive_day(value: Any) -> int:
    return max(1, _safe_int(value, 1))


def _profile(weather_id: str) -> Dict[str, Any]:
    return deepcopy(WEATHER_PROFILES.get(weather_id) or WEATHER_PROFILES[DEFAULT_WEATHER_ID])


def derive_season_for_day_count(day_count: int) -> str:
    day = _positive_day(day_count)
    index = ((day - 1) // DAYS_PER_SEASON) % len(SEASON_ORDER)
    return SEASON_ORDER[index]


def select_weather_for_day(day_count: int, *, location_id: str = "") -> Dict[str, Any]:
    day = _positive_day(day_count)
    season = derive_season_for_day_count(day)
    table = SEASON_WEATHER_TABLE.get(season) or SEASON_WEATHER_TABLE[DEFAULT_SEASON]
    location_bias = sum(ord(ch) for ch in str(location_id or "")) % len(table)
    index = ((day - 1) + location_bias) % len(table)
    if day == 1 and not location_id:
        index = 0
    selected = _profile(table[index])
    selected.update(
        {
            "day_count": day,
            "season": season,
            "location_id": str(location_id or ""),
            "weather_source": SOURCE,
            "source": SOURCE,
        }
    )
    return selected


def weather_fields_for_time_state(day_count: int, *, location_id: str = "") -> Dict[str, Any]:
    selected = select_weather_for_day(day_count, location_id=location_id)
    return {
        "season": selected["season"],
        "weather_id": selected["weather_id"],
        "weather_label": selected["label"],
        "weather_visibility": selected["visibility"],
        "weather_travel_fatigue_delta": selected["travel_fatigue_delta"],
        "weather_survival_thirst_delta": selected["survival_thirst_delta"],
        "weather_description": selected["description"],
        "weather_source": SOURCE,
    }


def ensure_weather_state(simulation_state: Dict[str, Any], *, location_id: str = "") -> Dict[str, Any]:
    state = _safe_dict(simulation_state)
    time_state = _safe_dict(state.get("time_state"))
    day_count = _positive_day(time_state.get("day_count"))
    weather_state = select_weather_for_day(day_count, location_id=location_id)
    weather_state["weather_log"] = list(_safe_list(_safe_dict(state.get("weather_state")).get("weather_log")))
    state["weather_state"] = weather_state
    time_state.update(weather_fields_for_time_state(day_count, location_id=location_id))
    state["time_state"] = time_state
    return weather_state


def refresh_weather_state(
    simulation_state: Dict[str, Any],
    *,
    day_count: int,
    location_id: str = "",
    reason: str = "time_advanced",
    turn_index: int = 0,
) -> Dict[str, Any]:
    state = _safe_dict(simulation_state)
    previous = deepcopy(_safe_dict(state.get("weather_state")))
    after = select_weather_for_day(day_count, location_id=location_id)
    changed = any(previous.get(key) != after.get(key) for key in ("day_count", "season", "weather_id", "location_id"))
    log = list(_safe_list(previous.get("weather_log")))
    entry = {
        "turn_index": int(turn_index or 0),
        "reason": str(reason or "time_advanced"),
        "before_day_count": previous.get("day_count"),
        "after_day_count": after["day_count"],
        "before_weather_id": previous.get("weather_id"),
        "after_weather_id": after["weather_id"],
        "before_season": previous.get("season"),
        "after_season": after["season"],
        "changed": changed,
        "source": SOURCE,
    }
    if changed:
        log.append(deepcopy(entry))
    after["weather_log"] = log
    state["weather_state"] = after
    time_state = _safe_dict(state.get("time_state"))
    time_state.update(weather_fields_for_time_state(after["day_count"], location_id=location_id))
    state["time_state"] = time_state
    return {"ok": True, "reason": "weather_refreshed", "before": previous, "after": deepcopy(after), "weather_log_entry": entry, "source": SOURCE}


def build_weather_narration_contract(weather_result: Dict[str, Any]) -> Dict[str, Any]:
    result = _safe_dict(weather_result)
    after = _safe_dict(result.get("after") or result.get("weather_state") or result)
    allowed = []
    if after:
        allowed.extend(
            [
                f"Season: {after.get('season')}",
                f"Weather: {after.get('label') or after.get('weather_label')}",
                f"Visibility: {after.get('visibility') or after.get('weather_visibility')}",
                f"Travel fatigue modifier: {_safe_int(after.get('travel_fatigue_delta', after.get('weather_travel_fatigue_delta')), 0)}",
                f"Survival thirst modifier: {_safe_int(after.get('survival_thirst_delta', after.get('weather_survival_thirst_delta')), 0)}",
            ]
        )
    return {
        "source": SOURCE,
        "allowed_weather_claims": allowed,
        "forbidden_weather_claims": [
            "Do not invent weather, seasons, forecasts, storms, visibility, or survival effects.",
            "Only claim weather or season details present in the deterministic weather payload.",
            "Do not claim weather changed unless a source-backed refresh result says changed=true.",
            "Do not call providers or LLMs for deterministic weather selection.",
            "Do not mutate travel, discovery, inventory, combat, quest, route access, or survival from weather rendering.",
        ],
    }


def assert_phase4_season_weather_expansion_ready() -> Dict[str, Any]:
    state: Dict[str, Any] = {"time_state": {"day_count": 1}}
    initial = ensure_weather_state(state)
    boundary = refresh_weather_state(state, day_count=31, reason="season_boundary", turn_index=4)
    contract = build_weather_narration_contract(boundary)
    blockers = []
    if initial.get("weather_id") == "weather:unset":
        blockers.append({"kind": "placeholder_weather_not_replaced", "source": SOURCE})
    if derive_season_for_day_count(1) != "early_autumn" or derive_season_for_day_count(31) != "late_autumn":
        blockers.append({"kind": "season_progression_not_deterministic", "source": SOURCE})
    if boundary.get("after", {}).get("season") != "late_autumn":
        blockers.append({"kind": "weather_refresh_missing_season_boundary", "source": SOURCE})
    if not contract.get("forbidden_weather_claims") or not contract.get("allowed_weather_claims"):
        blockers.append({"kind": "missing_weather_narration_contract", "source": SOURCE})
    return {
        "ok": not blockers,
        "reason": "phase4_season_weather_expansion_ready" if not blockers else "phase4_season_weather_expansion_not_ready",
        "initial": initial,
        "boundary": boundary,
        "blockers": blockers,
        "source": SOURCE,
    }
