from __future__ import annotations

from app.config.env import env_str, environment

import os
from dataclasses import dataclass, field
from typing import Any

from app.providers.hermes_client import HermesSidecarClient


def _flag(name: str, default: bool = False) -> bool:
    raw = environment().get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _timeout() -> float:
    raw = environment().get("HERMES_TIMEOUT_SECONDS", "45")
    try:
        return max(1.0, float(raw))
    except ValueError:
        return 45.0


@dataclass
class HermesRuntimeConfig:
    enabled: bool = False
    base_url: str = "http://127.0.0.1:8642"
    api_key_configured: bool = False
    timeout_seconds: float = 45.0


@dataclass
class HermesStatus:
    enabled: bool
    reachable: bool
    base_url: str
    state: str
    message: str
    health: dict[str, Any] = field(default_factory=dict)
    capabilities: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def hermes_runtime_config() -> HermesRuntimeConfig:
    return HermesRuntimeConfig(
        enabled=_flag("HERMES_ENABLED", default=False),
        base_url=environment().get("HERMES_BASE_URL", "http://127.0.0.1:8642"),
        api_key_configured=bool(environment().get("HERMES_API_KEY")),
        timeout_seconds=_timeout(),
    )


def hermes_status() -> HermesStatus:
    config = hermes_runtime_config()
    if not config.enabled:
        return HermesStatus(
            enabled=False,
            reachable=False,
            base_url=config.base_url,
            state="disabled",
            message="Installed, disabled in Omnix.",
        )
    client = HermesSidecarClient(
        base_url=config.base_url,
        api_key=environment().get("HERMES_API_KEY") or None,
        timeout=min(config.timeout_seconds, 8.0),
    )
    try:
        health = client.health()
        capabilities = client.capabilities()
        return HermesStatus(
            enabled=True,
            reachable=True,
            base_url=config.base_url,
            state="reachable",
            message="Connected to Hermes sidecar.",
            health=health,
            capabilities=capabilities,
        )
    except Exception as exc:
        return HermesStatus(
            enabled=True,
            reachable=False,
            base_url=config.base_url,
            state="offline",
            message="Enabled in Omnix, but the Hermes sidecar is unreachable.",
            error=str(exc),
        )


def hermes_status_payload() -> dict[str, Any]:
    status = hermes_status()
    return {
        "enabled": status.enabled,
        "reachable": status.reachable,
        "state": status.state,
        "message": status.message,
        "base_url": status.base_url,
        "health": status.health,
        "capabilities": status.capabilities,
        "error": status.error,
    }
