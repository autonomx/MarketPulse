"""Pure calendar and season helpers for RPG Environment 2.0."""
from __future__ import annotations

import hashlib
from typing import Any

DEFAULT_DAYS_PER_YEAR = 360
MINUTES_PER_DAY = 1440

SEASON_ORDER = ("early_spring", "spring", "summer", "early_autumn", "late_autumn", "winter")
SEASON_LABELS = {
    "early_spring": "Early Spring",
    "spring": "Spring",
    "summer": "Summer",
    "early_autumn": "Early Autumn",
    "late_autumn": "Late Autumn",
    "winter": "Winter",
}

REGION_SEASON_BIAS = {
    "market_road": "spring",
    "town": "spring",
    "trade_road": "early_autumn",
    "road_lowlands": "early_autumn",
    "mountain_pass": "winter",
    "northern_mountains": "winter",
    "abandoned_works": "late_autumn",
    "quarry_hills": "late_autumn",
}

CalendarState = dict[str, Any]


def derive_calendar_state(absolute_minutes: int, calendar: dict[str, Any] | None = None) -> CalendarState:
    """Derive day/year/minute/season state from absolute minutes.

    `season_id` is always derived from `absolute_minutes` and calendar config;
    callers should not persist it as mutable source-of-truth.
    """

    days_per_year = _days_per_year(calendar)
    minutes = max(0, int(absolute_minutes))
    day_index = minutes // MINUTES_PER_DAY
    minute_of_day = minutes % MINUTES_PER_DAY
    day = day_index + 1
    day_of_year = 1 + (day_index % days_per_year)
    year = 1 + (day_index // days_per_year)
    season_id = season_id_for_day(day_of_year, days_per_year=days_per_year)
    return {
        "absolute_minutes": minutes,
        "day": day,
        "year": year,
        "day_of_year": day_of_year,
        "days_per_year": days_per_year,
        "minute_of_day": minute_of_day,
        "season_id": season_id,
        "season_label": SEASON_LABELS[season_id],
        "day_label": f"Day {day}",
        "time_label": _time_label(day, minute_of_day),
    }


def season_id_for_day(day_of_year: int, *, days_per_year: int = DEFAULT_DAYS_PER_YEAR) -> str:
    """Return the derived season id for a day in the configured year."""

    normalized_day = 1 + ((max(1, int(day_of_year)) - 1) % max(1, int(days_per_year)))
    segment_size = max(1, int(days_per_year) // len(SEASON_ORDER))
    segment_index = min(len(SEASON_ORDER) - 1, (normalized_day - 1) // segment_size)
    return SEASON_ORDER[segment_index]


def _days_per_year(calendar: dict[str, Any] | None) -> int:
    if isinstance(calendar, dict):
        value = _first_int(calendar.get("days_per_year"))
        if value is not None and value > 0:
            return value
    return DEFAULT_DAYS_PER_YEAR


def _time_label(day: int, minute_of_day: int) -> str:
    hour = minute_of_day // 60
    minute = minute_of_day % 60
    return f"Day {day} • {hour:02d}:{minute:02d}"


def _first_int(*values: Any) -> int | None:
    for value in values:
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            return int(value.strip())
    return None


def _normalize_identifier(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _stable_int(*parts: Any) -> int:
    text = "|".join(str(part) for part in parts)
    digest = hashlib.blake2b(text.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") & 0x7FFF_FFFF
