"""Capability-specific network constraint signatures for generation and audits."""
from __future__ import annotations

from typing import Any

_COMPONENTS = (
    "coverage_scope",
    "access_model",
    "latency_class",
    "monitoring_mode",
    "blind_spot",
    "traceability_limit",
    "failure_mode",
    "jurisdiction_model",
)
_COVERAGE_SCOPES = (
    "district_mesh",
    "facility_cluster",
    "transit_corridor",
    "private_enclave",
    "civic_backbone",
    "regional_relay",
)
_ACCESS_MODELS = (
    "credentialed",
    "tiered_subscription",
    "physical_key",
    "sponsor_vouched",
    "public_limited",
    "black_market_bridge",
)
_LATENCY_CLASSES = (
    "local_realtime",
    "metropolitan_seconds",
    "regional_minutes",
    "intermittent_burst",
)
_MONITORING_MODES = (
    "metadata_logging",
    "biometric_correlation",
    "traffic_analysis",
    "human_review",
    "anomaly_detection",
    "checkpoint_scan",
)
_BLIND_SPOTS = (
    "maintenance_tunnels",
    "dead_zones",
    "legacy_hardware",
    "jurisdiction_boundary",
    "power_outage",
    "signal_congestion",
)
_TRACEABILITY_LIMITS = (
    "pseudonymous_window",
    "delayed_attribution",
    "fragmented_logs",
    "controller_only",
    "warrant_required",
    "ephemeral_sessions",
)
_FAILURE_MODES = (
    "partition",
    "spoofing",
    "overload",
    "controller_capture",
    "cascading_lockout",
    "sensor_drift",
)
_JURISDICTION_MODELS = (
    "corporate_lease",
    "municipal_warrant",
    "faction_control",
    "contested_boundary",
    "service_contract",
    "extraterritorial_claim",
)


def network_constraint_components() -> tuple[str, ...]:
    return _COMPONENTS


def deterministic_network_constraint_signature(index: int) -> dict[str, Any]:
    """Return a stable bounded network and surveillance constraint signature."""

    return {
        "coverage_scope": _COVERAGE_SCOPES[index % len(_COVERAGE_SCOPES)],
        "access_model": _ACCESS_MODELS[(index * 5 + 1) % len(_ACCESS_MODELS)],
        "latency_class": _LATENCY_CLASSES[(index * 3 + 1) % len(_LATENCY_CLASSES)],
        "monitoring_mode": _MONITORING_MODES[(index * 5 + 2) % len(_MONITORING_MODES)],
        "blind_spot": _BLIND_SPOTS[(index * 5 + 3) % len(_BLIND_SPOTS)],
        "traceability_limit": _TRACEABILITY_LIMITS[
            (index * 5 + 4) % len(_TRACEABILITY_LIMITS)
        ],
        "failure_mode": _FAILURE_MODES[(index * 5 + 5) % len(_FAILURE_MODES)],
        "jurisdiction_model": _JURISDICTION_MODELS[
            (index * 5 + 2) % len(_JURISDICTION_MODELS)
        ],
    }


__all__ = [
    "deterministic_network_constraint_signature",
    "network_constraint_components",
]
