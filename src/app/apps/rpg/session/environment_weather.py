"""Deterministic weather front generation for RPG Environment 2.0."""
from __future__ import annotations

import hashlib
from typing import Any

from app.apps.rpg.session.climate_profiles import resolve_climate_profile
from app.apps.rpg.session.environment_calendar import derive_calendar_state

INTENSITIES = ("trace", "light", "moderate", "heavy", "severe")
MIN_FRONT_DURATION_MINUTES = 6 * 60
MAX_FRONT_DURATION_MINUTES = 24 * 60


def generate_weather_event(
    *,
    environment_seed: int,
    region_id: str,
    climate_profile_id: str,
    absolute_minutes: int,
    calendar: dict[str, Any] | None = None,
    recent_conditions: dict[str, Any] | None = None,
    sequence: int = 0,
    condition_override: str | None = None,
) -> dict[str, Any]:
    """Generate a persistent weather event from climate, season, region, and seed."""

    climate = resolve_climate_profile(climate_profile_id)
    profile = climate["profile"]
    calendar_state = derive_calendar_state(absolute_minutes, calendar)
    season_id = str(calendar_state["season_id"])
    weights = _adjusted_weights(profile, season_id, recent_conditions or {})
    condition = _normalize_condition(condition_override) if condition_override else _weighted_condition(weights, environment_seed, region_id, climate["profile_id"], absolute_minutes, sequence)
    roll = _stable_int("weather_front", environment_seed, region_id, climate["profile_id"], season_id, absolute_minutes, sequence, condition)
    intensity = INTENSITIES[(roll // 13) % len(INTENSITIES)]
    duration_span = MAX_FRONT_DURATION_MINUTES - MIN_FRONT_DURATION_MINUTES
    duration = MIN_FRONT_DURATION_MINUTES + (roll % (duration_span + 1))
    duration = (duration // 10) * 10
    return {
        "id": f"weather_{roll % 1_000_000:06d}",
        "type": "weather",
        "condition": condition,
        "intensity": intensity,
        "remaining_minutes": max(MIN_FRONT_DURATION_MINUTES, duration),
        "started_at_minute": max(0, int(absolute_minutes)),
        "region_id": region_id,
        "climate_profile_id": climate["profile_id"],
        "season_id": season_id,
        "generation_sequence": sequence,
    }


def _adjusted_weights(profile: dict[str, Any], season_id: str, recent_conditions: dict[str, Any]) -> dict[str, float]:
    seasonal = profile.get("weather_weights", {}).get(season_id)
    weights = {str(key): float(value) for key, value in seasonal.items()} if isinstance(seasonal, dict) else {"clear": 1.0}
    rain_minutes = _coerce_int(recent_conditions.get("rain_minutes_24h"), 0)
    snow_minutes = _coerce_int(recent_conditions.get("snow_minutes_24h"), 0)
    dry_minutes = _coerce_int(recent_conditions.get("dry_minutes_72h"), 0)
    if rain_minutes >= 180:
        weights["rain"] = weights.get("rain", 0.0) * 0.55
        weights["clear"] = weights.get("clear", 0.0) + 0.12
        weights["cloudy"] = weights.get("cloudy", 0.0) + 0.08
    if snow_minutes >= 180:
        weights["snow"] = weights.get("snow", 0.0) * 0.6
        weights["clear"] = weights.get("clear", 0.0) + 0.10
    if dry_minutes >= 360:
        weights["clear"] = weights.get("clear", 0.0) * 0.75
        weights["rain"] = weights.get("rain", 0.0) + 0.15
        weights["storm"] = weights.get("storm", 0.0) + 0.05
    return {condition: max(0.0, weight) for condition, weight in weights.items() if weight > 0}


def _weighted_condition(weights: dict[str, float], seed: int, region_id: str, climate_profile_id: str, absolute_minutes: int, sequence: int) -> str:
    ordered = sorted(weights.items())
    total = sum(weight for _, weight in ordered)
    if total <= 0:
        return "clear"
    roll = _stable_int("weather_condition", seed, region_id, climate_profile_id, absolute_minutes, sequence) / 0x7FFF_FFFF
    threshold = roll * total
    cumulative = 0.0
    for condition, weight in ordered:
        cumulative += weight
        if threshold <= cumulative:
            return condition
    return ordered[-1][0]


def _normalize_condition(value: str | None) -> str:
    text = str(value or "clear").strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {"rainy": "rain", "raining": "rain", "cold_windy": "windy", "overcast": "cloudy"}
    return aliases.get(text, text or "clear")


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


def _stable_int(*parts: Any) -> int:
    text = "|".join(str(part) for part in parts)
    digest = hashlib.blake2b(text.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") & 0x7FFF_FFFF
