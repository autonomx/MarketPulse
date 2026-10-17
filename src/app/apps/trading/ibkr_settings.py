from __future__ import annotations

from app.config.env import environment

"""Durable, non-secret IBKR connection settings.

IB Gateway owns IBKR authentication.  Omnix stores only the local socket
connection and rollout switches needed to use the official client in its
market-data observation plane.
"""

import os
from dataclasses import asdict, dataclass
from typing import Any


_SETTINGS_SECTION = "trading_market_data"
_IBKR_KEY = "ibkr"
_TRUE_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class IbkrSettings:
    enabled: bool = False
    monitor_enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 4002
    client_id: int = 71
    live_authority_enabled: bool = False
    recovery_authority_enabled: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT_IBKR_SETTINGS = IbkrSettings()


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in _TRUE_VALUES


def _int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def settings_from_mapping(value: Any) -> IbkrSettings:
    raw = value if isinstance(value, dict) else {}
    host = str(raw.get("host", DEFAULT_IBKR_SETTINGS.host) or "").strip()
    return IbkrSettings(
        enabled=_bool(raw.get("enabled"), DEFAULT_IBKR_SETTINGS.enabled),
        monitor_enabled=_bool(raw.get("monitor_enabled"), DEFAULT_IBKR_SETTINGS.monitor_enabled),
        host=host or DEFAULT_IBKR_SETTINGS.host,
        port=_int(raw.get("port"), DEFAULT_IBKR_SETTINGS.port, minimum=1, maximum=65_535),
        client_id=_int(raw.get("client_id"), DEFAULT_IBKR_SETTINGS.client_id, minimum=0, maximum=32_767),
        live_authority_enabled=_bool(
            raw.get("live_authority_enabled"),
            DEFAULT_IBKR_SETTINGS.live_authority_enabled,
        ),
        recovery_authority_enabled=_bool(
            raw.get("recovery_authority_enabled"),
            DEFAULT_IBKR_SETTINGS.recovery_authority_enabled,
        ),
    )


def _environment_settings() -> IbkrSettings:
    return settings_from_mapping(
        {
            "enabled": environment().get("OMNIX_IBKR_ENABLED"),
            "monitor_enabled": environment().get("OMNIX_IBKR_MONITOR"),
            "host": environment().get("OMNIX_IBKR_HOST"),
            "port": environment().get("OMNIX_IBKR_PORT"),
            "client_id": environment().get("OMNIX_IBKR_CLIENT_ID"),
            "live_authority_enabled": environment().get("OMNIX_IBKR_LIVE_AUTHORITY"),
            "recovery_authority_enabled": environment().get("OMNIX_IBKR_RECOVERY_AUTHORITY"),
        }
    )


def load_ibkr_settings() -> tuple[IbkrSettings, str]:
    """Return effective settings and their source.

    A saved Omnix settings section wins over legacy environment variables.  The
    environment fallback keeps existing deployments compatible until the user
    saves the new Settings page.
    """

    from app.settings.access import load_settings

    document = load_settings(allow_defaults_without_service=True)
    section = document.get(_SETTINGS_SECTION)
    if isinstance(section, dict) and isinstance(section.get(_IBKR_KEY), dict):
        return settings_from_mapping(section[_IBKR_KEY]), "omnix_settings"

    environment_names = (
        "OMNIX_IBKR_ENABLED",
        "OMNIX_IBKR_MONITOR",
        "OMNIX_IBKR_HOST",
        "OMNIX_IBKR_PORT",
        "OMNIX_IBKR_CLIENT_ID",
        "OMNIX_IBKR_LIVE_AUTHORITY",
        "OMNIX_IBKR_RECOVERY_AUTHORITY",
    )
    if any(name in environment() for name in environment_names):
        return _environment_settings(), "environment"
    return DEFAULT_IBKR_SETTINGS, "defaults"


def save_ibkr_settings(patch: dict[str, Any]) -> IbkrSettings:
    """Persist a validated IBKR settings patch in the shared settings document."""

    from app.settings.access import load_settings, save_settings

    settings = load_settings()
    section = settings.get(_SETTINGS_SECTION)
    existing = dict(section) if isinstance(section, dict) else {}
    if isinstance(existing.get(_IBKR_KEY), dict):
        current = settings_from_mapping(existing[_IBKR_KEY])
    else:
        current = load_ibkr_settings()[0]
    merged = current.as_dict()
    merged.update(patch)
    validated = settings_from_mapping(merged)
    existing[_IBKR_KEY] = validated.as_dict()
    settings[_SETTINGS_SECTION] = existing
    save_settings(settings)
    return validated


__all__ = [
    "DEFAULT_IBKR_SETTINGS",
    "IbkrSettings",
    "load_ibkr_settings",
    "save_ibkr_settings",
    "settings_from_mapping",
]
