"""Structured actor incentive signatures shared by generation fixtures and audits."""
from __future__ import annotations

from typing import Any

_COMPONENTS = (
    "primary_motive",
    "scarce_need",
    "dependency_type",
    "risk_tolerance",
    "preferred_method",
    "red_line",
    "alliance_preference",
    "conflict_preference",
)
_PRIMARY_MOTIVES = (
    "security",
    "status",
    "belonging",
    "autonomy",
    "revenge",
    "truth",
    "wealth",
    "duty",
)
_SCARCE_NEEDS = (
    "access",
    "protection",
    "recognition",
    "information",
    "resources",
    "legitimacy",
    "freedom",
    "stability",
)
_DEPENDENCY_TYPES = (
    "institutional",
    "material",
    "personal",
    "informational",
    "territorial",
    "technological",
    "legal",
    "social",
)
_RISK_TOLERANCES = (
    "cautious",
    "calculated",
    "bold",
    "reckless",
)
_PREFERRED_METHODS = (
    "negotiation",
    "investigation",
    "patronage",
    "coercion",
    "sabotage",
    "trade",
    "exposure",
    "alliance_building",
)
_RED_LINES = (
    "betrayal",
    "public_humiliation",
    "loss_of_control",
    "harm_to_dependents",
    "ideological_compromise",
    "resource_seizure",
    "exile",
    "exposure",
)
_ALLIANCE_PREFERENCES = (
    "formal_alliance",
    "transactional_cooperation",
    "personal_loyalty",
    "independent_action",
    "covert_partnership",
)
_CONFLICT_PREFERENCES = (
    "avoidance",
    "legal_pressure",
    "economic_pressure",
    "public_challenge",
    "covert_action",
    "direct_force",
)


def actor_incentive_components() -> tuple[str, ...]:
    return _COMPONENTS


def deterministic_actor_incentive_signature(index: int) -> dict[str, Any]:
    """Return a stable, varied categorical actor incentive signature."""

    return {
        "primary_motive": _PRIMARY_MOTIVES[index % len(_PRIMARY_MOTIVES)],
        "scarce_need": _SCARCE_NEEDS[(index * 3 + 1) % len(_SCARCE_NEEDS)],
        "dependency_type": _DEPENDENCY_TYPES[
            (index * 5 + 2) % len(_DEPENDENCY_TYPES)
        ],
        "risk_tolerance": _RISK_TOLERANCES[
            (index * 3 + 1) % len(_RISK_TOLERANCES)
        ],
        "preferred_method": _PREFERRED_METHODS[
            (index * 5 + 3) % len(_PREFERRED_METHODS)
        ],
        "red_line": _RED_LINES[(index * 7 + 4) % len(_RED_LINES)],
        "alliance_preference": _ALLIANCE_PREFERENCES[
            (index * 2 + 1) % len(_ALLIANCE_PREFERENCES)
        ],
        "conflict_preference": _CONFLICT_PREFERENCES[
            (index * 5 + 2) % len(_CONFLICT_PREFERENCES)
        ],
    }


__all__ = [
    "actor_incentive_components",
    "deterministic_actor_incentive_signature",
]
