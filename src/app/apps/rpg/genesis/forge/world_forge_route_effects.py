"""Endpoint-specific travel effects for generated place connections."""
from __future__ import annotations

from typing import Any

_COMPONENTS = (
    "travel_cost_band",
    "time_variance",
    "hazard_level",
    "supply_effect",
    "price_effect",
    "information_delay",
    "closure_recovery",
)
_COST = ("low", "moderate", "high", "very_high", "permit_cost", "escort_cost")
_VARIANCE = ("predictable", "weather_variable", "checkpoint_variable", "congestion_variable", "seasonal", "highly_uncertain")
_HAZARD = ("low", "patrol_risk", "environmental_risk", "bandit_risk", "infrastructure_risk", "conflict_risk")
_SUPPLY = ("improves_supply", "stabilises_supply", "neutral_supply", "reduces_supply", "bottleneck_supply", "cuts_supply")
_PRICE = ("lowers_prices", "stabilises_prices", "neutral_prices", "raises_prices", "volatile_prices", "crisis_prices")
_DELAY = ("same_day", "one_day", "several_days", "one_week", "irregular", "courier_only")
_RECOVERY = ("hours", "days", "weeks", "seasonal", "requires_repair", "requires_negotiation")


def route_effect_components() -> tuple[str, ...]:
    return _COMPONENTS


def deterministic_route_effect_signature(index: int) -> dict[str, Any]:
    return {
        "travel_cost_band": _COST[index % len(_COST)],
        "time_variance": _VARIANCE[(index * 5 + 1) % len(_VARIANCE)],
        "hazard_level": _HAZARD[(index * 3 + 2) % len(_HAZARD)],
        "supply_effect": _SUPPLY[(index * 5 + 3) % len(_SUPPLY)],
        "price_effect": _PRICE[(index * 3 + 4) % len(_PRICE)],
        "information_delay": _DELAY[(index * 5 + 5) % len(_DELAY)],
        "closure_recovery": _RECOVERY[(index * 3 + 1) % len(_RECOVERY)],
    }


__all__ = ["deterministic_route_effect_signature", "route_effect_components"]
