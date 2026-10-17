"""Cursor pagination for collection endpoints (WP-5.5).

A page is ``items``, ``next_cursor`` and ``has_more``. The cursor is an
opaque URL-safe base64 of the ordering key of the last item, so a client
resumes after it in a stable order even while rows are inserted.
"""
from __future__ import annotations

import base64
import json
from typing import Any

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


class InvalidCursor(ValueError):
    """The cursor was not produced by this collection."""


def page_limit(limit: int | None, *, default: int = DEFAULT_PAGE_SIZE, maximum: int = MAX_PAGE_SIZE) -> int:
    """A page size between 1 and ``maximum``."""
    if limit is None:
        return default
    return max(1, min(int(limit), maximum))


def bounded_count(limit: int, *, maximum: int = MAX_PAGE_SIZE) -> int:
    """A row count between 0 and ``maximum`` (``limit=0`` asks for none)."""
    return max(0, min(int(limit), maximum))


def encode_cursor(*key: str | int) -> str:
    payload = json.dumps(list(key), separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_cursor(cursor: str | None, *, arity: int) -> tuple[Any, ...] | None:
    """The ordering key in ``cursor``, or ``None`` for the first page."""
    if not cursor:
        return None
    try:
        encoded = cursor.encode("ascii")
        key = json.loads(base64.urlsafe_b64decode(encoded + b"=" * (-len(encoded) % 4)))
    except (UnicodeEncodeError, ValueError, TypeError) as error:
        raise InvalidCursor("cursor is invalid") from error
    if not isinstance(key, list) or len(key) != arity or not all(isinstance(part, (str, int)) for part in key):
        raise InvalidCursor("cursor is invalid")
    return tuple(key)


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "InvalidCursor",
    "bounded_count",
    "decode_cursor",
    "encode_cursor",
    "page_limit",
]
