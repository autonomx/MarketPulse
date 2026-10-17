"""Structured countervailing-power signatures for World Forge generation."""
from __future__ import annotations

from typing import Any

_COMPONENTS = (
    "authority_source",
    "constraint_mechanism",
    "leverage_type",
    "accountability_channel",
    "mobilization_speed",
    "territorial_reach",
    "vulnerability",
    "failure_condition",
)
_AUTHORITY = ("elected_mandate", "property_rights", "ritual_legitimacy", "technical_expertise", "popular_membership", "military_command")
_CONSTRAINT = ("budget_veto", "legal_injunction", "labour_withdrawal", "public_exposure", "route_denial", "resource_embargo")
_LEVERAGE = ("credit_access", "workforce_control", "information_access", "transport_control", "public_trust", "security_capacity")
_ACCOUNTABILITY = ("public_hearing", "member_recall", "judicial_review", "ritual_censure", "audit_board", "coalition_vote")
_SPEED = ("hours", "days", "weeks", "seasonal", "deliberative", "emergency_only")
_REACH = ("neighbourhood", "district", "citywide", "regional", "networked_enclaves", "mobile_corridor")
_VULNERABILITY = ("funding_shortfall", "leadership_split", "supply_dependency", "legitimacy_loss", "communication_disruption", "member_defection")
_FAILURE = ("coalition_breakdown", "public_noncompliance", "resource_exhaustion", "legal_override", "internal_schism", "external_intervention")


def countervailing_power_components() -> tuple[str, ...]:
    return _COMPONENTS


def deterministic_countervailing_power_signature(index: int) -> dict[str, Any]:
    return {
        "authority_source": _AUTHORITY[index % len(_AUTHORITY)],
        "constraint_mechanism": _CONSTRAINT[(index * 5 + 1) % len(_CONSTRAINT)],
        "leverage_type": _LEVERAGE[(index * 3 + 2) % len(_LEVERAGE)],
        "accountability_channel": _ACCOUNTABILITY[(index * 5 + 3) % len(_ACCOUNTABILITY)],
        "mobilization_speed": _SPEED[(index * 3 + 4) % len(_SPEED)],
        "territorial_reach": _REACH[(index * 5 + 5) % len(_REACH)],
        "vulnerability": _VULNERABILITY[(index * 3 + 1) % len(_VULNERABILITY)],
        "failure_condition": _FAILURE[(index * 5 + 2) % len(_FAILURE)],
    }


__all__ = ["countervailing_power_components", "deterministic_countervailing_power_signature"]
