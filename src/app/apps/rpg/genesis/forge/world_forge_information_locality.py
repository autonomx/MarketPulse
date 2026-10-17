"""Faction-local information reach and delay signatures."""
from __future__ import annotations

from typing import Any

_COMPONENTS = (
    "channel_type",
    "latency_band",
    "verification_method",
    "distortion_risk",
    "interception_risk",
    "blackout_condition",
    "update_cadence",
    "confidence_decay",
)
_CHANNEL = ("courier_chain", "merchant_reports", "patrol_dispatch", "ritual_messenger", "encrypted_radio", "public_bulletin")
_LATENCY = ("same_day", "one_day", "several_days", "one_week", "irregular", "event_triggered")
_VERIFICATION = ("two_source_check", "trusted_officer", "physical_token", "local_witness", "archive_match", "technical_signature")
_DISTORTION = ("low", "moderate", "high", "politically_filtered", "commercially_filtered", "translation_sensitive")
_INTERCEPTION = ("low", "route_exposed", "checkpoint_exposed", "insider_exposed", "signal_exposed", "public_channel")
_BLACKOUT = ("route_closure", "weather_disruption", "leadership_split", "power_loss", "network_partition", "courier_capture")
_CADENCE = ("daily", "every_few_days", "weekly", "market_days", "patrol_return", "event_driven")
_DECAY = ("slow", "moderate", "fast", "distance_scaled", "conflict_scaled", "source_scaled")


def information_locality_components() -> tuple[str, ...]:
    return _COMPONENTS


def deterministic_information_locality_signature(index: int) -> dict[str, Any]:
    return {
        "channel_type": _CHANNEL[index % len(_CHANNEL)],
        "latency_band": _LATENCY[(index * 5 + 5) % len(_LATENCY)],
        "verification_method": _VERIFICATION[(index * 3 + 2) % len(_VERIFICATION)],
        "distortion_risk": _DISTORTION[(index * 5 + 3) % len(_DISTORTION)],
        "interception_risk": _INTERCEPTION[(index * 3 + 4) % len(_INTERCEPTION)],
        "blackout_condition": _BLACKOUT[(index * 5 + 5) % len(_BLACKOUT)],
        "update_cadence": _CADENCE[(index * 3 + 1) % len(_CADENCE)],
        "confidence_decay": _DECAY[(index * 5 + 2) % len(_DECAY)],
    }


__all__ = ["deterministic_information_locality_signature", "information_locality_components"]
