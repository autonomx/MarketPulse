"""Deterministic climate profile registry for RPG Environment 2.0."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

ClimateProfile = dict[str, Any]
ClimateProfileResolution = dict[str, Any]

DEFAULT_CLIMATE_PROFILE_ID = "temperate_hills"

SEASON_IDS = ("early_spring", "spring", "summer", "early_autumn", "late_autumn", "winter")

LOCATION_CLIMATE_PROFILE_IDS: dict[str, str] = {
    "rusty_flagon_tavern": "temperate_hills",
    "market_district": "temperate_hills",
    "northern_road": "road_lowlands",
    "glimmerdeep_pass": "northern_mountains",
    "old_quarry": "quarry_hills",
}

CLIMATE_PROFILES: dict[str, ClimateProfile] = {
    "temperate_hills": {
        "id": "temperate_hills",
        "display_name": "Temperate Hills",
        "temperature_ranges_c": {
            "early_spring": [2, 11],
            "spring": [6, 17],
            "summer": [13, 26],
            "early_autumn": [8, 19],
            "late_autumn": [2, 12],
            "winter": [-4, 6],
        },
        "sunrise_minutes": {"early_spring": 390, "spring": 360, "summer": 300, "early_autumn": 390, "late_autumn": 450, "winter": 480},
        "sunset_minutes": {"early_spring": 1080, "spring": 1170, "summer": 1260, "early_autumn": 1110, "late_autumn": 990, "winter": 900},
        "weather_weights": {
            "early_spring": {"rain": 0.35, "cloudy": 0.25, "clear": 0.20, "fog": 0.15, "windy": 0.05},
            "spring": {"rain": 0.30, "clear": 0.25, "cloudy": 0.25, "fog": 0.10, "windy": 0.10},
            "summer": {"clear": 0.35, "cloudy": 0.20, "rain": 0.20, "storm": 0.15, "windy": 0.10},
            "early_autumn": {"rain": 0.30, "cloudy": 0.25, "clear": 0.20, "fog": 0.15, "windy": 0.10},
            "late_autumn": {"rain": 0.35, "cloudy": 0.25, "fog": 0.20, "clear": 0.10, "windy": 0.10},
            "winter": {"cloudy": 0.30, "rain": 0.20, "snow": 0.20, "fog": 0.15, "clear": 0.15},
        },
        "base_wind": "light",
        "hazard_weights": {"flash_flood_risk": 0.02, "frost_risk": 0.03},
        "resource_baselines": {"water_availability": 70, "vegetation": 65, "soil_moisture": 55, "forage_availability": 60},
    },
    "northern_mountains": {
        "id": "northern_mountains",
        "display_name": "Northern Mountains",
        "temperature_ranges_c": {
            "early_spring": [-12, 2],
            "spring": [-6, 8],
            "summer": [0, 16],
            "early_autumn": [-3, 10],
            "late_autumn": [-10, 4],
            "winter": [-28, -8],
        },
        "sunrise_minutes": {"early_spring": 420, "spring": 390, "summer": 330, "early_autumn": 420, "late_autumn": 480, "winter": 540},
        "sunset_minutes": {"early_spring": 1050, "spring": 1140, "summer": 1230, "early_autumn": 1050, "late_autumn": 930, "winter": 840},
        "weather_weights": {
            "early_spring": {"snow": 0.25, "rain": 0.25, "fog": 0.20, "windy": 0.20, "clear": 0.10},
            "spring": {"rain": 0.30, "fog": 0.20, "windy": 0.20, "snow": 0.15, "clear": 0.15},
            "summer": {"clear": 0.25, "rain": 0.20, "fog": 0.20, "windy": 0.20, "storm": 0.15},
            "early_autumn": {"windy": 0.25, "rain": 0.25, "fog": 0.20, "clear": 0.15, "snow": 0.15},
            "late_autumn": {"snow": 0.35, "rain": 0.20, "fog": 0.20, "windy": 0.15, "clear": 0.10},
            "winter": {"snow": 0.45, "blizzard": 0.20, "windy": 0.15, "fog": 0.10, "clear": 0.10},
        },
        "base_wind": "moderate",
        "hazard_weights": {"avalanche_risk": 0.05, "frost_risk": 0.08},
        "resource_baselines": {"water_availability": 55, "vegetation": 35, "soil_moisture": 40, "forage_availability": 30},
    },
    "quarry_hills": {
        "id": "quarry_hills",
        "display_name": "Quarry Hills",
        "temperature_ranges_c": {
            "early_spring": [1, 10],
            "spring": [5, 16],
            "summer": [12, 27],
            "early_autumn": [7, 18],
            "late_autumn": [1, 11],
            "winter": [-5, 5],
        },
        "sunrise_minutes": {"early_spring": 390, "spring": 360, "summer": 315, "early_autumn": 390, "late_autumn": 450, "winter": 495},
        "sunset_minutes": {"early_spring": 1080, "spring": 1170, "summer": 1245, "early_autumn": 1110, "late_autumn": 990, "winter": 885},
        "weather_weights": {
            "early_spring": {"rain": 0.30, "cloudy": 0.25, "fog": 0.20, "clear": 0.15, "windy": 0.10},
            "spring": {"rain": 0.25, "cloudy": 0.25, "clear": 0.20, "windy": 0.15, "fog": 0.15},
            "summer": {"clear": 0.35, "cloudy": 0.20, "storm": 0.20, "dust": 0.15, "windy": 0.10},
            "early_autumn": {"windy": 0.25, "cloudy": 0.25, "rain": 0.20, "clear": 0.20, "fog": 0.10},
            "late_autumn": {"rain": 0.30, "cloudy": 0.25, "fog": 0.20, "windy": 0.15, "clear": 0.10},
            "winter": {"cloudy": 0.30, "snow": 0.25, "windy": 0.20, "clear": 0.15, "fog": 0.10},
        },
        "base_wind": "moderate",
        "hazard_weights": {"rockslide_risk": 0.04, "dust_risk": 0.03},
        "resource_baselines": {"water_availability": 45, "vegetation": 40, "soil_moisture": 35, "forage_availability": 35},
    },
    "road_lowlands": {
        "id": "road_lowlands",
        "display_name": "Road Lowlands",
        "temperature_ranges_c": {
            "early_spring": [3, 12],
            "spring": [7, 18],
            "summer": [14, 28],
            "early_autumn": [9, 20],
            "late_autumn": [3, 13],
            "winter": [-2, 7],
        },
        "sunrise_minutes": {"early_spring": 390, "spring": 360, "summer": 300, "early_autumn": 390, "late_autumn": 450, "winter": 480},
        "sunset_minutes": {"early_spring": 1080, "spring": 1170, "summer": 1260, "early_autumn": 1110, "late_autumn": 990, "winter": 900},
        "weather_weights": {
            "early_spring": {"rain": 0.30, "cloudy": 0.30, "fog": 0.20, "clear": 0.15, "windy": 0.05},
            "spring": {"rain": 0.30, "cloudy": 0.25, "clear": 0.25, "fog": 0.10, "windy": 0.10},
            "summer": {"clear": 0.35, "cloudy": 0.20, "rain": 0.20, "storm": 0.15, "windy": 0.10},
            "early_autumn": {"rain": 0.25, "cloudy": 0.25, "clear": 0.20, "fog": 0.20, "windy": 0.10},
            "late_autumn": {"rain": 0.35, "fog": 0.25, "cloudy": 0.20, "clear": 0.10, "windy": 0.10},
            "winter": {"cloudy": 0.30, "rain": 0.25, "fog": 0.20, "snow": 0.15, "clear": 0.10},
        },
        "base_wind": "light",
        "hazard_weights": {"flooded_road_risk": 0.03, "fog_risk": 0.04},
        "resource_baselines": {"water_availability": 65, "vegetation": 60, "soil_moisture": 55, "forage_availability": 55},
    },
}


def resolve_climate_profile(profile_id: str | None) -> ClimateProfileResolution:
    """Return a profile copy and deterministic fallback metadata."""

    requested = _normalize_profile_id(profile_id)
    fallback_used = requested not in CLIMATE_PROFILES
    resolved_id = requested if not fallback_used else DEFAULT_CLIMATE_PROFILE_ID
    metadata = {"requested_profile_id": requested, "fallback_used": fallback_used}
    if fallback_used:
        metadata["warning"] = f"unknown_climate_profile:{requested}"
    return {"profile_id": resolved_id, "profile": deepcopy(CLIMATE_PROFILES[resolved_id]), "metadata": metadata}


def _normalize_profile_id(value: str | None) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return text or DEFAULT_CLIMATE_PROFILE_ID
