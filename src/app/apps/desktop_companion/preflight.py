"""Vision capability preflight for explicit Desktop Companion Watch enablement."""
from __future__ import annotations

import time
from collections.abc import Callable
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

from app.providers.desktop_vision import (
    CodexDesktopVisionClient,
    DesktopVisionClient,
    default_desktop_vision_client,
)

_TINY_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAAAXNSR0IArs4c6QAAAARnQU1BAACxjwv8YQUA"
    "AAAJcEhZcwAADsMAAA7DAcdvqGQAAAAtSURBVFhH7c6hAQAACMOw/f80+B0AJpVVyTyXHtcBAAAAAAAAAAAAAAAA"
    "AAAsl/rw4k5bXakAAAAASUVORK5CYII="
)


class DesktopCompanionPreflightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vision_model_id: str | None = Field(default=None, max_length=240)
    remote_vision_allowed: bool = False


class DesktopCompanionPreflightResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ready: bool
    model_id: str | None = None
    endpoint: str | None = None
    remote: bool = False
    latency_ms: float | None = Field(default=None, ge=0)
    reason: str


class DesktopCompanionPreflightService:
    def __init__(
        self,
        *,
        client_factory: Callable[[], DesktopVisionClient | CodexDesktopVisionClient] = (
            default_desktop_vision_client
        ),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client_factory = client_factory
        self._clock = clock

    def check(self, request: DesktopCompanionPreflightRequest) -> DesktopCompanionPreflightResult:
        client = self._client_factory()
        endpoint = _safe_endpoint(client.base_url)
        remote = is_remote_vision_endpoint(client.base_url)
        if remote and not request.remote_vision_allowed:
            return DesktopCompanionPreflightResult(
                ready=False,
                model_id=request.vision_model_id or client.default_model,
                endpoint=endpoint,
                remote=True,
                reason="remote_vision_not_allowed",
            )
        started = self._clock()
        try:
            item = client.describe(
                _TINY_PNG,
                (
                    "This is a Desktop Companion capability test using a harmless one-pixel image. "
                    "Reply with a brief visible-description response and do not infer private content."
                ),
                request.vision_model_id,
            )
            model = str(item.metadata.get("model") or request.vision_model_id or client.default_model or "").strip()
            return DesktopCompanionPreflightResult(
                ready=True,
                model_id=model or None,
                endpoint=endpoint,
                remote=remote,
                latency_ms=round(max(0.0, self._clock() - started) * 1000, 3),
                reason="vision_capability_verified",
            )
        except Exception as exc:
            return DesktopCompanionPreflightResult(
                ready=False,
                model_id=request.vision_model_id or client.default_model,
                endpoint=endpoint,
                remote=remote,
                latency_ms=round(max(0.0, self._clock() - started) * 1000, 3),
                reason=f"{type(exc).__name__}: {exc}"[:300],
            )


def is_remote_vision_endpoint(value: str) -> bool:
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").casefold()
    return hostname not in {"", "localhost", "127.0.0.1", "::1"} and not hostname.startswith("127.")


def _safe_endpoint(value: str) -> str | None:
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"[:300]


_default_preflight_service: DesktopCompanionPreflightService | None = None


def default_desktop_companion_preflight_service() -> DesktopCompanionPreflightService:
    global _default_preflight_service
    if _default_preflight_service is None:
        _default_preflight_service = DesktopCompanionPreflightService()
    return _default_preflight_service


__all__ = [
    "DesktopCompanionPreflightRequest",
    "DesktopCompanionPreflightResult",
    "DesktopCompanionPreflightService",
    "default_desktop_companion_preflight_service",
    "is_remote_vision_endpoint",
]
