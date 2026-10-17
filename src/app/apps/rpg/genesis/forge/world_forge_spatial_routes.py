"""Depth-scaled spatial route contracts for generation and certification."""
from __future__ import annotations

from typing import Any

_COMPONENTS = (
    "travel_time_band",
    "access_mode",
    "route_blocker",
    "failure_condition",
    "capacity_class",
    "information_delay",
)
_TRAVEL_TIME_BANDS = (
    "local_minutes",
    "district_hour",
    "cross_region_hours",
    "overnight",
    "multi_day",
)
_ACCESS_MODES = (
    "foot_route",
    "public_transit",
    "controlled_vehicle",
    "water_crossing",
    "rail_corridor",
    "portal_gate",
)
_ROUTE_BLOCKERS = (
    "checkpoint",
    "weather_window",
    "permit_required",
    "damaged_infrastructure",
    "hostile_patrol",
    "keyed_threshold",
)
_FAILURE_CONDITIONS = (
    "closure",
    "delay",
    "reroute",
    "breakdown",
    "interdiction",
    "misroute",
)
_CAPACITY_CLASSES = (
    "individual",
    "small_group",
    "convoy",
    "public_flow",
    "freight_limited",
)
_INFORMATION_DELAYS = (
    "immediate_local",
    "minutes",
    "hours",
    "next_day",
    "intermittent",
)
_DEPTH_ROUTE_TARGETS = {
    "quick": 3,
    "standard": 5,
    "epic": 8,
}


def spatial_route_components() -> tuple[str, ...]:
    return _COMPONENTS


def minimum_route_count(place_count: int, depth: str) -> int:
    """Return the connected-graph floor plus a depth-scaled route portfolio floor."""

    count = max(0, int(place_count))
    if count < 2:
        return 0
    maximum = count * (count - 1) // 2
    connected_floor = count - 1
    depth_floor = _DEPTH_ROUTE_TARGETS.get(str(depth).casefold(), 5)
    return min(maximum, max(connected_floor, depth_floor))


def deterministic_spatial_route_signature(index: int) -> dict[str, Any]:
    """Return one stable, bounded travel signature for deterministic generation."""

    return {
        "travel_time_band": _TRAVEL_TIME_BANDS[index % len(_TRAVEL_TIME_BANDS)],
        "access_mode": _ACCESS_MODES[(index * 5 + 1) % len(_ACCESS_MODES)],
        "route_blocker": _ROUTE_BLOCKERS[(index * 5 + 2) % len(_ROUTE_BLOCKERS)],
        "failure_condition": _FAILURE_CONDITIONS[
            (index * 5 + 3) % len(_FAILURE_CONDITIONS)
        ],
        "capacity_class": _CAPACITY_CLASSES[
            (index * 3 + 1) % len(_CAPACITY_CLASSES)
        ],
        "information_delay": _INFORMATION_DELAYS[
            (index * 3 + 2) % len(_INFORMATION_DELAYS)
        ],
    }


__all__ = [
    "deterministic_spatial_route_signature",
    "minimum_route_count",
    "spatial_route_components",
]
