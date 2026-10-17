"""Pure Environment 2.0 snapshot derivation helpers."""
from __future__ import annotations

import hashlib
from typing import Any

from app.apps.rpg.session.climate_profiles import resolve_climate_profile
from app.apps.rpg.session.environment_calendar import derive_calendar_state
from app.apps.rpg.session.environment_memory import derive_terrain_condition
from app.apps.rpg.session.environment_signals import derive_environment_signals

EnvironmentSnapshot = dict[str, Any]

TEMP_MODIFIERS = {"clear": 1, "cloudy": 0, "overcast": -1, "rain": -2, "storm": -3, "fog": -1, "snow": -4, "windy": -1}
INTENSITY_MODIFIERS = {"trace": 0, "light": 0, "moderate": -1, "heavy": -2, "severe": -3}
WIND_ORDER = ("calm", "light", "moderate", "strong")


def derive_environment_snapshot(environment: dict[str, Any], scene_context: dict[str, Any] | None = None) -> EnvironmentSnapshot:
    env = environment if isinstance(environment, dict) else {}
    scene = scene_context if isinstance(scene_context, dict) else {}
    region_id = str(env.get("region_id") or scene.get("region_id") or "starting_region")
    climate = resolve_climate_profile(str(env.get("climate_profile_id") or "temperate_hills"))
    profile = climate["profile"]
    calendar = derive_calendar_state(_coerce_int(env.get("absolute_minutes"), 0), env.get("calendar"))
    event = _active_weather_event(env)
    condition = str(event.get("condition") or "clear")
    intensity = str(event.get("intensity") or "light")
    exposure = str(scene.get("exposure") or "outdoor")
    light_level = _derive_light_level(calendar, profile, scene)
    temperature_c = _derive_temperature_c(profile, calendar, condition, intensity, _coerce_int(env.get("environment_seed"), 0))
    wind = _derive_wind(profile, condition, intensity)
    visibility = _derive_visibility(exposure, condition, intensity, light_level)
    terrain = derive_terrain_condition(condition=condition, recent_conditions=env.get("recent_conditions"), scene_context=scene)
    resources = derive_environment_signals(profile, calendar, event, env.get("recent_conditions"))
    return {
        "environment_version": env.get("environment_version"),
        "region_id": region_id,
        "climate_profile_id": climate["profile_id"],
        "climate_profile_metadata": climate["metadata"],
        "calendar": calendar,
        "weather": {
            "condition": condition,
            "intensity": intensity,
            "event_id": event.get("id"),
            "remaining_minutes": event.get("remaining_minutes"),
            "label": _weather_label(condition, intensity),
        },
        "temperature_c": temperature_c,
        "temperature_label": f"{temperature_c}°C",
        "wind": wind,
        "visibility": visibility,
        "light_level": light_level,
        "terrain_condition": terrain,
        "context": {
            "exposure": exposure,
            "shelter": scene.get("shelter") or "exposed",
            "location_id": scene.get("location_id"),
            "location_label": scene.get("location_label"),
            "label": _context_label(exposure, scene),
        },
        "resources": resources,
        "display": {
            "day_time": calendar["time_label"],
            "season": calendar["season_label"],
            "weather": _weather_label(condition, intensity),
            "temperature": f"{temperature_c}°C",
            "wind": wind.title(),
            "visibility": visibility.title().replace("_", " "),
            "light": light_level.title().replace("_", " "),
            "terrain": terrain.title().replace("_", " "),
            "context": _context_label(exposure, scene),
        },
    }


def _active_weather_event(environment: dict[str, Any]) -> dict[str, Any]:
    events = environment.get("active_events")
    if isinstance(events, list):
        for event in events:
            if isinstance(event, dict) and event.get("type") == "weather":
                return dict(event)
    return {"type": "weather", "condition": "clear", "intensity": "light"}


def _derive_temperature_c(profile: dict[str, Any], calendar: dict[str, Any], condition: str, intensity: str, seed: int) -> int:
    ranges = raw_temperature_ranges_c if isinstance(raw_temperature_ranges_c := profile.get("temperature_ranges_c"), dict) else {}
    low, high = _temperature_range(ranges.get(calendar["season_id"]))
    span = max(1, high - low)
    offset = _stable_int("temperature", seed, calendar["day"], calendar["minute_of_day"], condition) % (span + 1)
    hour = calendar["minute_of_day"] // 60
    diurnal = -2 if hour < 6 else 2 if 12 <= hour <= 16 else 0
    modifier = TEMP_MODIFIERS.get(condition, 0) + INTENSITY_MODIFIERS.get(intensity, 0)
    return max(low, min(high, low + offset + diurnal + modifier))


def _temperature_range(value: Any) -> tuple[int, int]:
    if isinstance(value, list | tuple) and len(value) == 2:
        low, high = value
        if isinstance(low, int | float) and isinstance(high, int | float) and low <= high:
            return int(low), int(high)
    return 0, 20


def _derive_wind(profile: dict[str, Any], condition: str, intensity: str) -> str:
    base = str(profile.get("base_wind") or "light")
    index = WIND_ORDER.index(base) if base in WIND_ORDER else 1
    bump = 1 if condition in {"storm", "windy"} else 0
    bump += 1 if intensity in {"heavy", "severe"} else 0
    return WIND_ORDER[min(len(WIND_ORDER) - 1, index + bump)]


def _derive_visibility(exposure: str, condition: str, intensity: str, light_level: str) -> str:
    if exposure == "indoor":
        return "interior"
    if condition in {"fog", "storm", "snow", "rain"}:
        return "poor" if intensity in {"heavy", "severe"} else "reduced"
    if light_level in {"dark", "dim"}:
        return "dim"
    return "clear"


def _derive_light_level(calendar: dict[str, Any], profile: dict[str, Any], scene: dict[str, Any]) -> str:
    if scene.get("light_override"):
        return str(scene["light_override"])
    if scene.get("exposure") == "indoor":
        return "indoor_dim"
    season = calendar["season_id"]
    sunrise = _coerce_int(profile.get("sunrise_minutes", {}).get(season), 360)
    sunset = _coerce_int(profile.get("sunset_minutes", {}).get(season), 1080)
    minute = calendar["minute_of_day"]
    if minute < sunrise - 45 or minute > sunset + 45:
        return "dark"
    if sunrise - 45 <= minute < sunrise + 45 or sunset - 45 < minute <= sunset + 45:
        return "dim"
    return "daylight"


def _weather_label(condition: str, intensity: str) -> str:
    return f"{intensity.replace('_', ' ').title()} {condition.replace('_', ' ').title()}"


def _context_label(exposure: str, scene: dict[str, Any]) -> str:
    shelter = str(scene.get("shelter") or "exposed").replace("_", " ")
    return f"{exposure.replace('_', ' ').title()} • {shelter.title()}"


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
