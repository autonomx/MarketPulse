from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

SOURCE = "deterministic_phase4_time_day_hooks"
MINUTES_PER_DAY = 24 * 60
DEFAULT_START_MINUTE_OF_DAY = 8 * 60
DEFAULT_DAY_COUNT = 1
DEFAULT_SEASON = "early_autumn"
DEFAULT_WEATHER_ID = "weather:clear_mild"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _non_negative_int(value: Any, default: int = 0) -> int:
    return max(0, _safe_int(value, default))


def _travel_location_id(state: Dict[str, Any]) -> str:
    return str(_safe_dict(state.get("travel_state")).get("current_location_id") or "")


def _weather_fields(day_count: int, *, location_id: str = "") -> Dict[str, Any]:
    from app.apps.rpg.world.locations.weather import weather_fields_for_time_state

    return weather_fields_for_time_state(day_count, location_id=location_id)


def describe_time_of_day(minute_of_day: int) -> str:
    minute = _non_negative_int(minute_of_day, 0) % MINUTES_PER_DAY
    hour = minute // 60
    if 5 <= hour <= 7:
        return "Dawn"
    if 8 <= hour <= 11:
        return "Morning"
    if 12 <= hour <= 16:
        return "Afternoon"
    if 17 <= hour <= 20:
        return "Evening"
    return "Night"


def format_clock_time(minute_of_day: int) -> str:
    minute = _non_negative_int(minute_of_day, 0) % MINUTES_PER_DAY
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _derive_time_state(elapsed_minutes: int, *, time_log: List[Any], location_id: str = "") -> Dict[str, Any]:
    elapsed = _non_negative_int(elapsed_minutes, 0)
    absolute = DEFAULT_START_MINUTE_OF_DAY + elapsed
    minute_of_day = absolute % MINUTES_PER_DAY
    day_count = DEFAULT_DAY_COUNT + (absolute // MINUTES_PER_DAY)
    normalized = {
        "elapsed_minutes": elapsed,
        "day_count": day_count,
        "minute_of_day": minute_of_day,
        "hour": minute_of_day // 60,
        "clock_time": format_clock_time(minute_of_day),
        "time_of_day_label": describe_time_of_day(minute_of_day),
        "time_log": list(time_log),
        "source": SOURCE,
    }
    normalized.update(_weather_fields(day_count, location_id=location_id))
    return normalized


def ensure_time_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    state = _safe_dict(simulation_state)
    time_state = _safe_dict(state.get("time_state"))
    elapsed = _non_negative_int(time_state.get("elapsed_minutes"), 0)
    time_log = list(_safe_list(time_state.get("time_log")))
    normalized = _derive_time_state(elapsed, time_log=time_log, location_id=_travel_location_id(state))
    state["time_state"] = normalized
    from app.apps.rpg.world.locations.weather import ensure_weather_state

    ensure_weather_state(state, location_id=_travel_location_id(state))
    return _safe_dict(state.get("time_state"))


def advance_time(
    simulation_state: Dict[str, Any],
    minutes: int,
    *,
    reason: str,
    turn_index: int = 0,
) -> Dict[str, Any]:
    requested = _safe_int(minutes, -1)
    if requested <= 0:
        return {
            "ok": False,
            "reason": "invalid_time_advance_minutes",
            "requested_minutes": minutes,
            "source": SOURCE,
        }
    before = deepcopy(ensure_time_state(simulation_state))
    after_elapsed = _non_negative_int(before.get("elapsed_minutes"), 0) + requested
    after_day_count = DEFAULT_DAY_COUNT + ((DEFAULT_START_MINUTE_OF_DAY + after_elapsed) // MINUTES_PER_DAY)
    entry = {
        "turn_index": int(turn_index or 0),
        "minutes": requested,
        "reason": str(reason or "time_advanced"),
        "before_day_count": before["day_count"],
        "after_day_count": after_day_count,
        "source": SOURCE,
    }
    location_id = _travel_location_id(simulation_state)
    after = _derive_time_state(
        after_elapsed,
        time_log=list(_safe_list(before.get("time_log"))) + [deepcopy(entry)],
        location_id=location_id,
    )
    simulation_state["time_state"] = after
    from app.apps.rpg.world.locations.weather import refresh_weather_state

    weather_refresh = refresh_weather_state(
        simulation_state,
        day_count=after_day_count,
        location_id=location_id,
        reason=entry["reason"],
        turn_index=turn_index,
    )
    after = _safe_dict(simulation_state.get("time_state"))
    return {
        "ok": True,
        "reason": "time_advanced",
        "minutes": requested,
        "advance_reason": entry["reason"],
        "before": before,
        "after": deepcopy(after),
        "time_log_entry": entry,
        "weather_refresh": weather_refresh,
        "source": SOURCE,
    }


def apply_travel_time(simulation_state: Dict[str, Any], travel_result: Dict[str, Any], *, turn_index: int = 0) -> Dict[str, Any]:
    result = _safe_dict(travel_result)
    if result.get("ok") is not True:
        return {"ok": False, "reason": "travel_time_not_applied", "travel_result": result, "source": SOURCE}
    entry = _safe_dict(result.get("travel_log_entry"))
    if not entry:
        entry = _safe_dict(_safe_dict(result.get("travel_result")).get("travel_log_entry"))
    minutes = _safe_int(entry.get("minutes"), -1)
    if minutes <= 0:
        return {"ok": False, "reason": "missing_travel_minutes", "travel_result": result, "source": SOURCE}
    advanced = advance_time(
        simulation_state,
        minutes,
        reason="travel",
        turn_index=turn_index,
    )
    if advanced.get("ok") is True:
        advanced["reason"] = "travel_time_applied"
        advanced["travel_minutes"] = minutes
    return advanced


def build_time_narration_contract(time_result: Dict[str, Any]) -> Dict[str, Any]:
    result = _safe_dict(time_result)
    allowed_claims = []
    after = _safe_dict(result.get("after")) if result.get("ok") else {}
    if after:
        allowed_claims.extend(
            [
                f"Day count: {_safe_int(after.get('day_count'), DEFAULT_DAY_COUNT)}",
                f"Clock time: {after.get('clock_time')}",
                f"Time of day: {after.get('time_of_day_label')}",
                f"Elapsed minutes: {_safe_int(after.get('elapsed_minutes'), 0)}",
                f"Season: {after.get('season')}",
                f"Weather: {after.get('weather_label')}",
            ]
        )
    return {
        "source": SOURCE,
        "allowed_time_claims": allowed_claims,
        "forbidden_time_claims": [
            "Do not invent dates, calendar names, or time jumps.",
            "Only claim weather and season details present in the deterministic after time_state.",
            "Do not claim weather changed unless the weather_refresh payload says changed=true.",
            "Do not claim time changed unless advance_time or apply_travel_time returned ok=true.",
            "Do not mutate inventory, survival, combat, quest, discovery, or route access from time hooks.",
        ],
    }


def assert_phase4_time_day_hooks_ready() -> Dict[str, Any]:
    state: Dict[str, Any] = {}
    initial = ensure_time_state(state)
    morning = advance_time(state, 55, reason="travel", turn_index=1)
    crossing = advance_time(state, MINUTES_PER_DAY, reason="overnight_wait", turn_index=2)
    invalid = advance_time(state, 0, reason="invalid", turn_index=3)
    contract = build_time_narration_contract(crossing)
    blockers = []
    if initial.get("day_count") != 1 or initial.get("clock_time") != "08:00" or initial.get("time_of_day_label") != "Morning":
        blockers.append({"kind": "unexpected_initial_time_state", "source": SOURCE})
    if initial.get("weather_id") == "weather:unset" or not initial.get("weather_label"):
        blockers.append({"kind": "missing_deterministic_weather_state", "source": SOURCE})
    if morning.get("after", {}).get("clock_time") != "08:55":
        blockers.append({"kind": "unexpected_travel_time_advance", "source": SOURCE})
    if crossing.get("after", {}).get("day_count") != 2:
        blockers.append({"kind": "expected_day_count_increment", "source": SOURCE})
    if invalid.get("reason") != "invalid_time_advance_minutes":
        blockers.append({"kind": "expected_invalid_minutes_rejection", "source": SOURCE})
    if not contract.get("forbidden_time_claims"):
        blockers.append({"kind": "missing_time_guardrails", "source": SOURCE})
    return {
        "ok": not blockers,
        "reason": "phase4_time_day_hooks_ready" if not blockers else "phase4_time_day_hooks_not_ready",
        "initial": initial,
        "morning": morning,
        "crossing": crossing,
        "invalid": invalid,
        "blockers": blockers,
        "source": SOURCE,
    }
