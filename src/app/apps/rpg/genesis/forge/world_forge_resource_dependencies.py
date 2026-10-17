"""Structured resource dependency signatures for generation and certification."""
from __future__ import annotations

from typing import Any

_COMPONENTS = (
    "resource_class",
    "supply_mode",
    "dependency_strength",
    "substitute_class",
    "bottleneck_type",
    "depletion_horizon",
    "failure_consequence",
    "recovery_mode",
)
_RESOURCE_CLASSES = (
    "energy",
    "water",
    "food",
    "specialist_material",
    "data_access",
    "skilled_labour",
    "transport_capacity",
    "legal_authority",
)
_SUPPLY_MODES = (
    "local_extraction",
    "contracted_import",
    "distributed_production",
    "licensed_access",
    "salvage_recovery",
    "seasonal_harvest",
    "specialist_service",
)
_DEPENDENCY_STRENGTHS = (
    "supporting",
    "important",
    "critical",
    "single_source",
)
_SUBSTITUTE_CLASSES = (
    "no_substitute",
    "lower_grade",
    "slower_route",
    "black_market",
    "recycled_input",
    "manual_process",
)
_BOTTLENECK_TYPES = (
    "single_source",
    "transport_chokepoint",
    "licensed_access",
    "seasonal_supply",
    "specialist_labour",
    "political_embargo",
    "storage_limit",
)
_DEPLETION_HORIZONS = (
    "hours",
    "days",
    "weeks",
    "seasonal",
    "event_driven",
)
_FAILURE_CONSEQUENCES = (
    "service_shutdown",
    "price_spike",
    "access_loss",
    "production_stall",
    "public_unrest",
    "quality_degradation",
    "territorial_shift",
)
_RECOVERY_MODES = (
    "rationing",
    "reroute_supply",
    "repair_capacity",
    "negotiate_access",
    "substitute_input",
    "salvage_stock",
    "emergency_release",
)


def resource_dependency_components() -> tuple[str, ...]:
    return _COMPONENTS


def deterministic_resource_dependency_signature(index: int) -> dict[str, Any]:
    """Return a stable dependency signature with chokepoints and alternatives."""

    return {
        "resource_class": _RESOURCE_CLASSES[index % len(_RESOURCE_CLASSES)],
        "supply_mode": _SUPPLY_MODES[(index * 5 + 1) % len(_SUPPLY_MODES)],
        "dependency_strength": _DEPENDENCY_STRENGTHS[
            (index * 3 + 1) % len(_DEPENDENCY_STRENGTHS)
        ],
        "substitute_class": _SUBSTITUTE_CLASSES[
            (index * 5) % len(_SUBSTITUTE_CLASSES)
        ],
        "bottleneck_type": _BOTTLENECK_TYPES[
            (index * 5 + 2) % len(_BOTTLENECK_TYPES)
        ],
        "depletion_horizon": _DEPLETION_HORIZONS[
            (index * 3 + 2) % len(_DEPLETION_HORIZONS)
        ],
        "failure_consequence": _FAILURE_CONSEQUENCES[
            (index * 5 + 3) % len(_FAILURE_CONSEQUENCES)
        ],
        "recovery_mode": _RECOVERY_MODES[
            (index * 5 + 4) % len(_RECOVERY_MODES)
        ],
    }


__all__ = [
    "deterministic_resource_dependency_signature",
    "resource_dependency_components",
]
