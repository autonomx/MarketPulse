"""Central compatibility helpers for injected legacy structured gateways."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .errors import StructuredOutputError
from .parsing import decode_json_object


def decode_legacy_json_object(value: Any) -> dict[str, Any]:
    """Decode one legacy response while preserving historical empty fallback behavior."""

    if isinstance(value, Mapping):
        return dict(value)
    try:
        return decode_json_object(str(value or ""))
    except (StructuredOutputError, ValueError, TypeError):
        return {}


__all__ = ["decode_legacy_json_object"]
