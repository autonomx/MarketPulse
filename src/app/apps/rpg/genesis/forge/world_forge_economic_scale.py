"""Representative economic scale bands for generation and certification."""
from __future__ import annotations

from typing import Any

_COMPONENTS = (
    "scale_scope",
    "served_population_band",
    "workforce_band",
    "service_reach_band",
    "throughput_band",
    "price_basis",
    "scarcity_level",
    "reserve_horizon",
    "demand_pressure",
)
_POPULATION_BANDS = (
    "dozens",
    "hundreds",
    "thousands",
    "tens_of_thousands",
    "hundreds_of_thousands",
)
_WORKFORCE_BANDS = (
    "individual",
    "small_crew",
    "dozens",
    "hundreds",
    "thousands",
)
_SERVICE_REACH_BANDS = (
    "household",
    "neighbourhood",
    "district",
    "multi_district",
    "regional",
)
_THROUGHPUT_BANDS = (
    "bespoke",
    "dozens_per_day",
    "hundreds_per_day",
    "thousands_per_day",
    "continuous_bulk",
)
_PRICE_BASES = (
    "labour_cost",
    "scarcity_markup",
    "regulated_tariff",
    "barter_equivalence",
    "risk_premium",
    "subscription",
    "ration_credit",
)
_SCARCITY_LEVELS = (
    "abundant",
    "stable",
    "constrained",
    "scarce",
    "critical",
)
_RESERVE_BY_SCARCITY = (
    "seasonal",
    "months",
    "weeks",
    "days",
    "hours",
)
_DEMAND_PRESSURES = (
    "low",
    "steady",
    "elevated",
    "surging",
    "overloaded",
)


def economic_scale_components() -> tuple[str, ...]:
    return _COMPONENTS


def deterministic_economic_scale_signature(
    index: int,
    *,
    scope: str,
) -> dict[str, Any]:
    """Return internally consistent scale bands for one place or service system."""

    scale_scope = (
        "place_population"
        if str(scope).casefold() in {"place", "location", "settlement"}
        else "service_system"
    )
    population_rank = index % len(_POPULATION_BANDS)
    workforce_rank = max(0, population_rank - 1)
    reach_rank = min(population_rank, (index * 3 + 1) % len(_SERVICE_REACH_BANDS))
    throughput_rank = min(
        len(_THROUGHPUT_BANDS) - 1,
        workforce_rank + 1,
    )
    scarcity_rank = (index * 3 + 1) % len(_SCARCITY_LEVELS)
    return {
        "scale_scope": scale_scope,
        "served_population_band": _POPULATION_BANDS[population_rank],
        "workforce_band": _WORKFORCE_BANDS[workforce_rank],
        "service_reach_band": _SERVICE_REACH_BANDS[reach_rank],
        "throughput_band": _THROUGHPUT_BANDS[throughput_rank],
        "price_basis": _PRICE_BASES[(index * 5 + 2) % len(_PRICE_BASES)],
        "scarcity_level": _SCARCITY_LEVELS[scarcity_rank],
        "reserve_horizon": _RESERVE_BY_SCARCITY[scarcity_rank],
        "demand_pressure": _DEMAND_PRESSURES[(index * 3 + 2) % len(_DEMAND_PRESSURES)],
    }


__all__ = [
    "deterministic_economic_scale_signature",
    "economic_scale_components",
]
