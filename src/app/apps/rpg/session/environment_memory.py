"""Environmental memory and terrain derivation for RPG Environment 2.0."""
from __future__ import annotations

from typing import Any

RECENT_24H_MINUTES = 24 * 60
RECENT_72H_MINUTES = 72 * 60
RECENT_7D_MINUTES = 7 * 24 * 60

RECENT_CONDITION_KEYS = {
    "rain_minutes_24h": 0,
    "snow_minutes_24h": 0,
    "dry_minutes_72h": 0,
    "freezing_minutes_24h": 0,
    "storm_minutes_24h": 0,
    "mud_minutes_72h": 0,
    "snowpack_minutes_72h": 0,
    "dust_minutes_72h": 0,
    "drought_minutes_7d": 0,
    "thaw_minutes_24h": 0,
}

RAIN_CONDITIONS = {"rain", "storm"}
SNOW_CONDITIONS = {"snow", "blizzard"}
DRY_CONDITIONS = {"clear", "cloudy", "overcast", "windy"}
STORM_CONDITIONS = {"storm", "blizzard"}


def normalize_recent_conditions(recent_conditions: dict[str, Any] | None) -> dict[str, int]:
    recent = recent_conditions if isinstance(recent_conditions, dict) else {}
    return {key: max(0, _coerce_int(recent.get(key), default)) for key, default in RECENT_CONDITION_KEYS.items()}


def advance_environment_memory(recent_conditions: dict[str, Any] | None, *, condition: str, elapsed_minutes: int) -> dict[str, int]:
    elapsed = max(0, int(elapsed_minutes))
    memory = normalize_recent_conditions(recent_conditions)
    condition = str(condition or "clear")
    if condition in RAIN_CONDITIONS:
        memory["rain_minutes_24h"] += elapsed
        memory["dry_minutes_72h"] = max(0, memory["dry_minutes_72h"] - elapsed)
        if memory["rain_minutes_24h"] >= 120 or condition == "storm":
            memory["mud_minutes_72h"] += elapsed
        if memory["snowpack_minutes_72h"] > 0:
            memory["thaw_minutes_24h"] += elapsed
    elif condition in SNOW_CONDITIONS:
        memory["snow_minutes_24h"] += elapsed
        memory["freezing_minutes_24h"] += elapsed
        memory["snowpack_minutes_72h"] += elapsed
        memory["dry_minutes_72h"] = max(0, memory["dry_minutes_72h"] - elapsed)
    elif condition in DRY_CONDITIONS:
        memory["dry_minutes_72h"] += elapsed
        if memory["dry_minutes_72h"] >= 360:
            memory["dust_minutes_72h"] += elapsed
        if memory["dry_minutes_72h"] >= 720:
            memory["drought_minutes_7d"] += elapsed
        memory["mud_minutes_72h"] = max(0, memory["mud_minutes_72h"] - elapsed)
        memory["thaw_minutes_24h"] = max(0, memory["thaw_minutes_24h"] - elapsed)
    if condition in STORM_CONDITIONS:
        memory["storm_minutes_24h"] += elapsed
    return _bounded_memory(memory)


def derive_terrain_condition(*, condition: str, recent_conditions: dict[str, Any] | None, scene_context: dict[str, Any] | None = None) -> str:
    scene = scene_context if isinstance(scene_context, dict) else {}
    exposure = str(scene.get("exposure") or "outdoor")
    if exposure == "indoor":
        return "interior_floor"
    if exposure == "underground":
        return "underground_floor"
    if exposure in {"vehicle", "vehicle_like"}:
        return "vehicle_deck"
    memory = normalize_recent_conditions(recent_conditions)
    condition = str(condition or "clear")
    if condition in RAIN_CONDITIONS and memory["snowpack_minutes_72h"] >= 60:
        return "slush"
    if condition in SNOW_CONDITIONS and memory["freezing_minutes_24h"] >= 60:
        return "deep_snow"
    if condition in SNOW_CONDITIONS:
        return "snow_covered"
    if condition in RAIN_CONDITIONS or memory["mud_minutes_72h"] >= 60:
        return "muddy"
    if memory["dust_minutes_72h"] >= 60:
        return "dusty"
    if memory["drought_minutes_7d"] >= 60:
        return "drought_hardened"
    return "dry"


def _bounded_memory(memory: dict[str, int]) -> dict[str, int]:
    memory["rain_minutes_24h"] = min(RECENT_24H_MINUTES, memory["rain_minutes_24h"])
    memory["snow_minutes_24h"] = min(RECENT_24H_MINUTES, memory["snow_minutes_24h"])
    memory["freezing_minutes_24h"] = min(RECENT_24H_MINUTES, memory["freezing_minutes_24h"])
    memory["storm_minutes_24h"] = min(RECENT_24H_MINUTES, memory["storm_minutes_24h"])
    memory["thaw_minutes_24h"] = min(RECENT_24H_MINUTES, memory["thaw_minutes_24h"])
    memory["dry_minutes_72h"] = min(RECENT_72H_MINUTES, memory["dry_minutes_72h"])
    memory["mud_minutes_72h"] = min(RECENT_72H_MINUTES, memory["mud_minutes_72h"])
    memory["snowpack_minutes_72h"] = min(RECENT_72H_MINUTES, memory["snowpack_minutes_72h"])
    memory["dust_minutes_72h"] = min(RECENT_72H_MINUTES, memory["dust_minutes_72h"])
    memory["drought_minutes_7d"] = min(RECENT_7D_MINUTES, memory["drought_minutes_7d"])
    return memory


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
