"""Deterministic time advancement for RPG Environment 2.0."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.apps.rpg.session.environment_calendar import derive_calendar_state
from app.apps.rpg.session.environment_memory import advance_environment_memory
from app.apps.rpg.session.environment_weather import generate_weather_event

DEFAULT_TURN_MINUTES = 10
DEFAULT_HISTORY_LIMIT = 12


def advance_environment_time(environment: dict[str, Any], *, elapsed_minutes: int = DEFAULT_TURN_MINUTES) -> dict[str, Any]:
    """Advance authoritative environment state by deterministic elapsed minutes.

    The helper returns a new environment dictionary. It increments absolute time,
    decrements event timers, moves expired events into bounded history, updates
    recent-condition counters, and creates deterministic replacement weather only
    when the prior weather event expires and no other weather event remains.
    """

    env = deepcopy(environment) if isinstance(environment, dict) else {}
    elapsed = max(0, int(elapsed_minutes))
    previous_minutes = _coerce_int(env.get("absolute_minutes"), 0)
    next_minutes = previous_minutes + elapsed
    env["absolute_minutes"] = next_minutes
    env["calendar"] = _calendar_for_environment(next_minutes, env.get("calendar"))

    active_events = [dict(event) for event in env.get("active_events", []) if isinstance(event, dict)]
    kept_events: list[dict[str, Any]] = []
    expired_events: list[dict[str, Any]] = []
    active_weather_condition = _current_weather_condition(active_events)

    for event in active_events:
        event_elapsed = elapsed if _event_counts_down(event) else 0
        remaining = _coerce_int(event.get("remaining_minutes"), 0) - event_elapsed
        event["remaining_minutes"] = remaining
        if remaining > 0:
            kept_events.append(event)
        else:
            expired = dict(event)
            expired["ended_at_minute"] = next_minutes
            expired["expired_after_elapsed_minutes"] = elapsed
            expired_events.append(expired)

    weather_expired = any(event.get("type") == "weather" for event in expired_events)
    env["event_history"] = _bounded_history(env.get("event_history"), expired_events, _history_limit(env))
    env["recent_conditions"] = advance_environment_memory(env.get("recent_conditions"), condition=active_weather_condition, elapsed_minutes=elapsed)
    if weather_expired and not any(event.get("type") == "weather" for event in kept_events):
        kept_events.append(_next_weather_event(env, started_at_minute=next_minutes))

    env["active_events"] = kept_events
    return env


def _calendar_for_environment(absolute_minutes: int, calendar: Any) -> dict[str, int]:
    derived = derive_calendar_state(absolute_minutes, calendar if isinstance(calendar, dict) else None)
    return {"year": derived["year"], "day_of_year": derived["day_of_year"], "days_per_year": derived["days_per_year"]}


def _event_counts_down(event: dict[str, Any]) -> bool:
    return event.get("remaining_minutes") is not None


def _current_weather_condition(events: list[dict[str, Any]]) -> str:
    for event in events:
        if event.get("type") == "weather":
            return str(event.get("condition") or "clear")
    return "clear"


def _next_weather_event(environment: dict[str, Any], *, started_at_minute: int) -> dict[str, Any]:
    return generate_weather_event(
        environment_seed=_coerce_int(environment.get("environment_seed"), 0),
        region_id=str(environment.get("region_id") or "starting_region"),
        climate_profile_id=str(environment.get("climate_profile_id") or "temperate_hills"),
        absolute_minutes=started_at_minute,
        calendar=raw_calendar if isinstance(raw_calendar := environment.get("calendar"), dict) else None,
        recent_conditions=raw_recent_conditions if isinstance(raw_recent_conditions := environment.get("recent_conditions"), dict) else None,
        sequence=len(environment.get("event_history", [])) if isinstance(environment.get("event_history"), list) else 0,
    )


def _bounded_history(history: Any, expired_events: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    existing = [dict(event) for event in history if isinstance(event, dict)] if isinstance(history, list) else []
    combined = existing + expired_events
    return combined[-limit:]


def _history_limit(environment: dict[str, Any]) -> int:
    limit = _coerce_int(environment.get("event_history_limit"), DEFAULT_HISTORY_LIMIT)
    return max(1, limit)


def _coerce_int(value: Any, fallback: int) -> int:
    if isinstance(value, bool) or value is None:
        return fallback
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return fallback
