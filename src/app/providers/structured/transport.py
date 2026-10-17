"""Shared transport helpers for structured provider adapters."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, MutableMapping

from .errors import UnsupportedStructuredMode

_STRUCTURED_OPTION_KEYS = ("response_format", "tools", "tool_choice")
_STRUCTURED_REJECTION_MARKERS = (
    "response_format",
    "json_schema",
    "json object",
    "json_object",
    "structured output",
    "tool_choice",
    "tool call",
    "tools are not supported",
    "function calling",
)


@dataclass(frozen=True)
class StructuredTransportOptions:
    request_timeout_seconds: float | None = None
    payload_options: dict[str, Any] = field(default_factory=dict)


def pop_structured_transport_options(
    kwargs: MutableMapping[str, Any],
) -> StructuredTransportOptions:
    """Remove Omnix transport hints and structured API fields from provider kwargs."""

    timeout_value = kwargs.pop("request_timeout_seconds", None)
    timeout: float | None = None
    if timeout_value is not None:
        try:
            timeout = max(0.001, float(timeout_value))
        except (TypeError, ValueError):
            timeout = None
    payload: dict[str, Any] = {}
    for key in _STRUCTURED_OPTION_KEYS:
        if key in kwargs:
            payload[key] = kwargs.pop(key)
    return StructuredTransportOptions(timeout, payload)


def raise_if_structured_mode_rejected(
    *,
    status_code: int | None,
    response_body: str,
    error: Exception,
) -> None:
    """Raise a typed capability rejection for provider-supported downgrade logic."""

    if status_code not in {400, 404, 405, 415, 422, 501}:
        return
    text = str(response_body or "").casefold()
    if any(marker in text for marker in _STRUCTURED_REJECTION_MARKERS):
        raise UnsupportedStructuredMode(
            f"provider rejected requested structured mode: {response_body[:1000]}"
        ) from error
