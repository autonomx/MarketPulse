"""Long horizon environment signals for RPG Environment 2.0."""
from __future__ import annotations

from typing import Any

SIGNAL_DEFAULTS = {
    "water_availability": 50,
    "vegetation": 50,
    "soil_moisture": 50,
    "forage_availability": 50,
    "snowpack": 0,
    "drought_pressure": 0,
    "flood_pressure": 0,
    "frost_pressure": 0,
}

SEASON_SIGNAL_MODIFIERS = {
    "early_spring": {
        "soil_moisture": 6,
        "vegetation": 4,
        "forage_availability": 3,
        "snowpack": 6,
    },
    "spring": {
        "water_availability": 5,
        "soil_moisture": 8,
        "vegetation": 8,
        "forage_availability": 6,
    },
    "summer": {
        "water_availability": -8,
        "soil_moisture": -7,
        "drought_pressure": 8,
        "vegetation": 4,
    },
    "early_autumn": {
        "vegetation": -2,
        "forage_availability": -3,
        "soil_moisture": 2,
    },
    "late_autumn": {
        "vegetation": -8,
        "forage_availability": -8,
        "soil_moisture": 5,
        "frost_pressure": 5,
    },
    "winter": {
        "vegetation": -18,
        "forage_availability": -18,
        "snowpack": 16,
        "frost_pressure": 18,
    },
}

EVENT_SIGNAL_MODIFIERS = {
    "rain": {
        "water_availability": 6,
        "soil_moisture": 12,
        "flood_pressure": 4,
        "drought_pressure": -8,
    },
    "storm": {
        "water_availability": 8,
        "soil_moisture": 16,
        "flood_pressure": 14,
        "drought_pressure": -12,
    },
    "snow": {
        "snowpack": 18,
        "frost_pressure": 10,
        "soil_moisture": 4,
        "forage_availability": -8,
    },
    "blizzard": {
        "snowpack": 28,
        "frost_pressure": 18,
        "forage_availability": -14,
    },
    "clear": {"drought_pressure": 2},
    "windy": {"soil_moisture": -3, "drought_pressure": 3},
    "dust": {
        "soil_moisture": -8,
        "vegetation": -6,
        "forage_availability": -6,
        "drought_pressure": 14,
    },
}

INTENSITY_MULTIPLIERS = {
    "trace": 0.5,
    "light": 0.75,
    "moderate": 1.0,
    "heavy": 1.25,
    "severe": 1.5,
}


def derive_environment_signals(
    profile: dict[str, Any],
    calendar: dict[str, Any] | None = None,
    active_weather: dict[str, Any] | None = None,
    recent_conditions: dict[str, Any] | None = None,
) -> dict[str, int]:
    signals = _baseline_signals(profile)
    season_id = str((calendar or {}).get("season_id") or "early_spring")
    _apply_modifiers(signals, SEASON_SIGNAL_MODIFIERS.get(season_id, {}))

    event = active_weather if isinstance(active_weather, dict) else {}
    intensity = str(event.get("intensity") or "moderate")
    scale = INTENSITY_MULTIPLIERS.get(intensity, 1.0)
    _apply_modifiers(
        signals,
        EVENT_SIGNAL_MODIFIERS.get(str(event.get("condition") or "clear"), {}),
        scale=scale,
    )
    _apply_memory(signals, recent_conditions if isinstance(recent_conditions, dict) else {})
    return {key: _clamp(value) for key, value in signals.items()}


def _baseline_signals(profile: dict[str, Any]) -> dict[str, int]:
    baselines = profile.get("resource_baselines")
    if not isinstance(baselines, dict):
        baselines = {}
    signals = {
        key: _coerce_int(baselines.get(key), fallback)
        for key, fallback in SIGNAL_DEFAULTS.items()
    }
    weights = profile.get("hazard_weights")
    if not isinstance(weights, dict):
        weights = {}
    signals["flood_pressure"] += _scaled_weight(weights, "flash_flood_risk")
    signals["flood_pressure"] += _scaled_weight(weights, "flooded_road_risk")
    signals["frost_pressure"] += _scaled_weight(weights, "frost_risk")
    signals["drought_pressure"] += _scaled_weight(weights, "dust_risk")
    return signals


def _apply_memory(signals: dict[str, int], recent: dict[str, Any]) -> None:
    rain = _coerce_int(recent.get("rain_minutes_24h"), 0)
    dry = _coerce_int(recent.get("dry_minutes_72h"), 0)
    snow = _coerce_int(recent.get("snowpack_minutes_72h"), 0)
    mud = _coerce_int(recent.get("mud_minutes_72h"), 0)
    dust = _coerce_int(recent.get("dust_minutes_72h"), 0)
    drought = _coerce_int(recent.get("drought_minutes_7d"), 0)
    freezing = _coerce_int(recent.get("freezing_minutes_24h"), 0)
    storm = _coerce_int(recent.get("storm_minutes_24h"), 0)
    _apply_modifiers(
        signals,
        {
            "water_availability": min(16, rain // 60) - min(18, dry // 180),
            "soil_moisture": min(20, (rain + mud) // 60) - min(22, (dry + dust) // 180),
            "vegetation": min(8, rain // 180) - min(18, (drought + dust) // 240),
            "forage_availability": min(8, rain // 240) - min(16, (drought + snow) // 240),
            "snowpack": min(35, snow // 60),
            "drought_pressure": min(35, (dry + drought + dust) // 180),
            "flood_pressure": min(30, (rain + mud + storm) // 120),
            "frost_pressure": min(32, (freezing + snow) // 120),
        },
    )


def _apply_modifiers(
    signals: dict[str, int],
    modifiers: dict[str, int],
    *,
    scale: float = 1.0,
) -> None:
    for key, value in modifiers.items():
        if key in signals:
            signals[key] += int(round(value * scale))


def _scaled_weight(weights: dict[str, Any], key: str) -> int:
    value = weights.get(key)
    if isinstance(value, int | float) and not isinstance(value, bool):
        return int(round(float(value) * 100))
    return 0


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


def _clamp(value: int) -> int:
    return max(0, min(100, int(value)))
