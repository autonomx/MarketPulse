"""Place-local market state signatures for World Forge generation."""
from __future__ import annotations

from typing import Any

_COMPONENTS = (
    "market_scope",
    "demand_profile",
    "supply_reliability",
    "price_level",
    "credit_access",
    "informal_share",
    "enforcement_level",
    "shock_sensitivity",
    "recovery_horizon",
)
_SCOPE = ("neighbourhood", "district", "settlement", "corridor_hub", "port_cluster", "remote_outpost")
_DEMAND = ("staples_led", "industrial_inputs", "transit_services", "luxury_goods", "medical_goods", "mixed_household")
_SUPPLY = ("robust", "seasonal", "intermittent", "single_route", "rationed", "smuggled")
_PRICE = ("elevated", "stable", "volatile", "discounted", "crisis", "barter_dominant")
_CREDIT = ("open_ledger", "guild_credit", "collateral_only", "relationship_only", "ration_credit", "cash_only")
_INFORMAL = ("minimal", "limited", "substantial", "dominant", "hidden", "licensed_parallel")
_ENFORCEMENT = ("light", "licensed", "inspected", "quota_based", "militarised", "contested")
_SHOCK = ("low", "moderate", "high", "route_sensitive", "politically_sensitive", "weather_sensitive")
_RECOVERY = ("days", "weeks", "months", "seasonal", "requires_new_route", "requires_policy_change")


def local_market_components() -> tuple[str, ...]:
    return _COMPONENTS


def deterministic_local_market_signature(index: int) -> dict[str, Any]:
    return {
        "market_scope": _SCOPE[index % len(_SCOPE)],
        "demand_profile": _DEMAND[(index * 5 + 1) % len(_DEMAND)],
        "supply_reliability": _SUPPLY[(index * 3 + 2) % len(_SUPPLY)],
        "price_level": _PRICE[(index * 2 + 2) % len(_PRICE)],
        "credit_access": _CREDIT[(index * 3 + 4) % len(_CREDIT)],
        "informal_share": _INFORMAL[(index * 5 + 5) % len(_INFORMAL)],
        "enforcement_level": _ENFORCEMENT[(index * 3 + 1) % len(_ENFORCEMENT)],
        "shock_sensitivity": _SHOCK[(index * 5 + 2) % len(_SHOCK)],
        "recovery_horizon": _RECOVERY[(index * 3 + 3) % len(_RECOVERY)],
    }


__all__ = ["deterministic_local_market_signature", "local_market_components"]
