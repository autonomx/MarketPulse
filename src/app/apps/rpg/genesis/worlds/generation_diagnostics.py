"""Compact local diagnostics for reusable-world generation.

The diagnostics intentionally exclude prompts, completions, generated topic content,
and provider payloads. They are safe to paste into a bug report after a quick review.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from app.apps.rpg.foundation.debug_logging import rpg_debug_log_dir, rpg_debug_logging_enabled

_LOG_PREFIX = "world-generation"
_MAX_ERROR_CHARS = 1_200
_MAX_STRING_CHARS = 400
_MAX_LIST_ITEMS = 25
_RETENTION_DAYS = 14
_OMITTED_KEY_PARTS = (
    "prompt",
    "completion",
    "content",
    "document",
    "response",
    "output",
    "input_payload",
    "generated",
    "messages",
)
_REDACTED_KEY_PARTS = (
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
)
_lock = threading.RLock()
_last_cleanup_date: str | None = None


def world_generation_log_path(*, at: datetime | None = None) -> Path:
    moment = at or datetime.now(timezone.utc)
    return rpg_debug_log_dir() / f"{_LOG_PREFIX}-{moment.strftime('%Y-%m-%d')}.jsonl"


def world_generation_log_hint() -> str:
    """Return a stable Windows-friendly path for UI and API error messages."""

    try:
        relative = world_generation_log_path().relative_to(Path.cwd())
        return str(relative).replace("/", "\\")
    except ValueError:
        return str(world_generation_log_path())


def log_world_generation_event(
    event: str,
    *,
    level: str = "info",
    diagnostic_id: str | None = None,
    world_id: str | None = None,
    run_id: str | None = None,
    topic_id: str | None = None,
    job_id: str | None = None,
    fields: Mapping[str, Any] | None = None,
    error: BaseException | str | None = None,
) -> dict[str, Any]:
    if not rpg_debug_logging_enabled():
        return {}
    payload: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": str(event or "world_generation.event"),
        "level": str(level or "info").lower(),
        "pid": os.getpid(),
        "thread": threading.current_thread().name,
    }
    for key, value in (
        ("diagnostic_id", diagnostic_id),
        ("world_id", world_id),
        ("run_id", run_id),
        ("topic_id", topic_id),
        ("job_id", job_id),
    ):
        if value:
            payload[key] = str(value)
    if fields:
        payload["fields"] = _compact(fields)
    if error is not None:
        payload["error"] = {
            "type": type(error).__name__ if isinstance(error, BaseException) else "Error",
            "message": _truncate(str(error), _MAX_ERROR_CHARS),
        }
    _append(payload)
    return payload


def _compact(value: Any, *, key: str = "", depth: int = 0) -> Any:
    normalized_key = key.casefold()
    if any(part in normalized_key for part in _REDACTED_KEY_PARTS):
        return "[redacted]"
    if any(part in normalized_key for part in _OMITTED_KEY_PARTS):
        return "[omitted]"
    if depth >= 5:
        return "[depth-limited]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _truncate(value.replace("\r", " ").replace("\n", " "), _MAX_STRING_CHARS)
    if isinstance(value, Mapping):
        return {
            str(child_key): _compact(child_value, key=str(child_key), depth=depth + 1)
            for child_key, child_value in list(value.items())[:_MAX_LIST_ITEMS]
        }
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        compacted = [_compact(item, key=key, depth=depth + 1) for item in items[:_MAX_LIST_ITEMS]]
        if len(items) > _MAX_LIST_ITEMS:
            compacted.append(f"[{len(items) - _MAX_LIST_ITEMS} more]")
        return compacted
    return _truncate(repr(value), _MAX_STRING_CHARS)


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 18)] + f"…[{len(value)} chars]"


def _append(payload: Mapping[str, Any]) -> None:
    path = world_generation_log_path()
    line = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    encoded = line.encode("utf-8", errors="replace")
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        _cleanup(path.parent)
        fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
        try:
            os.write(fd, encoded)
        finally:
            os.close(fd)


def _cleanup(directory: Path) -> None:
    global _last_cleanup_date
    today = datetime.now(timezone.utc).date()
    if _last_cleanup_date == today.isoformat():
        return
    cutoff = today - timedelta(days=_RETENTION_DAYS)
    for path in directory.glob(f"{_LOG_PREFIX}-*.jsonl"):
        try:
            if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).date() < cutoff:
                path.unlink(missing_ok=True)
        except OSError:
            continue
    _last_cleanup_date = today.isoformat()
