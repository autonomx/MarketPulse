"""Shared content-free diagnostics sanitization.

The Live runtime may pass rich transient details between local components, but
persistent diagnostics must never depend on caller discipline or field naming
alone.  This module provides the final server-side boundary used before a
record is written to disk.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

_CONTENT_FIELDS = {
    "body",
    "content",
    "memory",
    "phrase",
    "prompt",
    "sanitized_text",
    "text",
    "transcript",
}
_CONTENT_SUFFIXES = (
    "_body",
    "_content",
    "_memory",
    "_phrase",
    "_prompt",
    "_text",
    "_transcript",
)
_CONTENT_DERIVED_FIELDS = {
    "body_hash",
    "content_hash",
    "memory_hash",
    "phrase_hash",
    "prompt_hash",
    "sanitized_text_hash",
    "sanitized_text_sha256",
    "text_hash",
    "text_sha256",
    "transcript_hash",
    "transcript_sha256",
}


def _is_content_field(key: str) -> bool:
    normalized = key.strip().lower()
    if normalized in _CONTENT_FIELDS:
        return True
    if normalized.endswith(("_length", "_chars", "_bytes", "_count", "_index", "_id")):
        return False
    return normalized.endswith(_CONTENT_SUFFIXES)


def _is_content_derived_field(key: str) -> bool:
    normalized = key.strip().lower()
    if normalized in _CONTENT_DERIVED_FIELDS:
        return True
    return any(
        normalized.startswith(prefix)
        and normalized.endswith(("_hash", "_sha", "_sha256", "_fingerprint"))
        for prefix in (
            "body_",
            "content_",
            "memory_",
            "phrase_",
            "prompt_",
            "text_",
            "transcript_",
        )
    )


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return sanitize_content_free_details(dict(value))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (bytes, bytearray)):
        return {"bytes": len(value)}
    return str(value)


def sanitize_content_free_details(details: Mapping[str, Any]) -> dict[str, Any]:
    """Return a persistable diagnostics payload with content removed.

    Raw content fields are replaced by character counts when the value is a
    string.  Content-derived hashes are removed because deterministic hashes of
    short sensitive phrases can be dictionary-guessed.  Nested mappings and
    sequences are sanitized recursively.
    """

    sanitized: dict[str, Any] = {}
    for raw_key, value in details.items():
        key = str(raw_key)
        if _is_content_derived_field(key):
            continue
        if _is_content_field(key):
            if isinstance(value, str):
                sanitized[f"{key}_chars"] = len(value)
            elif isinstance(value, (bytes, bytearray)):
                sanitized[f"{key}_bytes"] = len(value)
            elif value is not None:
                sanitized[f"{key}_present"] = True
            continue
        sanitized[key] = _sanitize_value(value)
    return sanitized
