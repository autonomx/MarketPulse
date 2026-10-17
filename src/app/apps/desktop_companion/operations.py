"""Operational kill switch and compatibility policy for Desktop Companion."""
from __future__ import annotations

from app.config.env import env_str, environment

import os
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict


class DesktopCompanionOperationalStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    available: bool
    kill_switch: bool
    reason: str
    supported_browsers: tuple[str, ...] = ("Chromium 120+", "Edge 120+", "Chrome 120+")
    secure_context_required: bool = True
    supported_capture_sources: tuple[str, ...] = ("browser-tab", "window", "monitor")
    supported_provider_contracts: tuple[str, ...] = ("OpenAI-compatible image chat completions",)
    raw_frame_persistence: bool = False
    max_consecutive_provider_failures: int = 6
    circuit_backoff_seconds: int = 60


def desktop_companion_operational_status(
    environ: Mapping[str, str] | None = None,
) -> DesktopCompanionOperationalStatus:
    values = environ if environ is not None else environment()
    killed = _truthy(values.get("OMNIX_DESKTOP_COMPANION_KILL_SWITCH"))
    return DesktopCompanionOperationalStatus(
        available=not killed,
        kill_switch=killed,
        reason="deployment_kill_switch" if killed else "operational",
    )


def _truthy(value: object) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


__all__ = ["DesktopCompanionOperationalStatus", "desktop_companion_operational_status"]
