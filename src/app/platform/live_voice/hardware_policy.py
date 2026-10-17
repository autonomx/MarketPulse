"""Hardware-gated policies for the local low-latency live-voice path.

The RTX 4090 validation path uses a Faster Qwen provider that explicitly reports
that generations cannot overlap. The live execution lane now serializes that
provider with accepted-first priority and bounded speculative chunk sizes, so
serial speculative TTS is safe by default and can be preempted when accepted
speech arrives. ``OMNIX_LIVE_TTS_ALLOW_SERIAL_SPECULATION=false`` remains an
explicit fail-closed kill switch if a provider proves unable to stop cleanly at
scheduler chunk boundaries.

This module is installed by the gateway entry point before the FastAPI app is
created. It also makes LM Studio Responses state reuse default-on for accepted
live-voice turns when the environment variable is absent, while preserving an
explicit false opt-out and logging the final eligibility decision. The loaded
model discovery cache is widened for the live hardware profile so a stable LM
Studio model does not require a management-API round trip on every accepted
turn; users can still override the cache TTL explicitly.
"""
from __future__ import annotations

from app.config.env import environment

from typing import Any

from app.conversation.performance_contract import resolve_tts_provider_capabilities

_LMSTUDIO_DISCOVERY_CACHE_ENV = "OMNIX_LMSTUDIO_MODEL_DISCOVERY_CACHE_SECONDS"
_LMSTUDIO_DISCOVERY_CACHE_DEFAULT = "15"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def _bool_setting(raw: str | None, *, default: bool) -> bool:
    if raw is None:
        return default
    normalized = raw.strip().casefold()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    return default


def apply_live_voice_process_defaults() -> None:
    """Apply process defaults at the production composition boundary.

    ``setdefault`` is intentional: an explicit operator value remains authoritative.
    """

    environment().setdefault(
        _LMSTUDIO_DISCOVERY_CACHE_ENV,
        _LMSTUDIO_DISCOVERY_CACHE_DEFAULT,
    )


def should_defer_speculative_tts(provider: Any, allow_serial: str | None = None) -> bool:
    """Defer serial speculation only when the explicit scheduler kill switch is off."""
    if _bool_setting(allow_serial, default=True):
        return False
    capabilities = resolve_tts_provider_capabilities(provider)
    return not capabilities.supports_concurrent_generation


__all__ = [
    "apply_live_voice_process_defaults",
    "should_defer_speculative_tts",
]
