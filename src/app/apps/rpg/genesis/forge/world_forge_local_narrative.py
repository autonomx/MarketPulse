"""Bounded local narrative-opportunity signatures for World Forge generation."""
from __future__ import annotations

from typing import Any

_COMPONENTS = (
    "discovery_channel",
    "evidence_form",
    "urgency_band",
    "expiry_window",
    "consequence_scope",
    "entry_mode",
    "information_scope",
    "failure_visibility",
)
_CHANNELS = (
    "direct_witness",
    "local_notice",
    "trusted_contact",
    "patrol_report",
    "market_rumour",
    "physical_trace",
)
_EVIDENCE = (
    "witness_account",
    "damaged_object",
    "transaction_record",
    "route_disruption",
    "public_order",
    "environmental_sign",
)
_URGENCY = (
    "hours",
    "one_day",
    "several_days",
    "one_week",
    "seasonal_window",
    "event_triggered",
)
_EXPIRY = (
    "end_of_day",
    "three_days",
    "one_week",
    "one_month",
    "after_next_escalation",
    "when_route_reopens",
)
_SCOPE = (
    "household",
    "neighbourhood",
    "district",
    "settlement",
    "route_corridor",
    "regional_cell",
)
_ENTRY = (
    "investigate_evidence",
    "answer_request",
    "intercept_transfer",
    "protect_witness",
    "negotiate_access",
    "follow_route_change",
)
_INFORMATION = (
    "single_place",
    "adjacent_places",
    "faction_reach",
    "witness_network",
    "market_network",
    "patrol_network",
)
_VISIBILITY = (
    "private_loss",
    "local_shortage",
    "public_dispute",
    "route_closure",
    "faction_reaction",
    "visible_hazard",
)


def local_narrative_components() -> tuple[str, ...]:
    return _COMPONENTS


def deterministic_local_narrative_signature(index: int) -> dict[str, Any]:
    return {
        "discovery_channel": _CHANNELS[index % len(_CHANNELS)],
        "evidence_form": _EVIDENCE[(index * 5 + 1) % len(_EVIDENCE)],
        "urgency_band": _URGENCY[(index * 3 + 2) % len(_URGENCY)],
        "expiry_window": _EXPIRY[(index * 5 + 3) % len(_EXPIRY)],
        "consequence_scope": _SCOPE[(index * 3 + 4) % len(_SCOPE)],
        "entry_mode": _ENTRY[(index * 5 + 5) % len(_ENTRY)],
        "information_scope": _INFORMATION[(index * 3 + 1) % len(_INFORMATION)],
        "failure_visibility": _VISIBILITY[(index * 5 + 2) % len(_VISIBILITY)],
    }


__all__ = [
    "deterministic_local_narrative_signature",
    "local_narrative_components",
]
