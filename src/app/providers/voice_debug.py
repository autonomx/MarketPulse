"""Structured local diagnostics for character voice resolution.

The browser already persists live-call controller events to
``resources/logs/live-call-streaming.log``. This module adds small JSON-line
logs for the backend-to-TTS handoff and the standalone TTS process without
recording synthesized text.
"""
from __future__ import annotations

from app.config.env import environment

import hashlib
import hmac
import secrets
import itertools
import json
import logging
import logging.handlers
import os
import re
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.runtime.paths import LOGS_DIR

VOICE_DEBUG_LOG_MAX_BYTES = 10_000_000
VOICE_DEBUG_LOG_BACKUP_COUNT = 3
_CHANNEL_PATTERN = re.compile(r"[^A-Za-z0-9_.-]+")
_SEQUENCE = itertools.count(1)
_LOGGER_LOCK = threading.Lock()
MAX_VOICE_DEBUG_LOGGERS = 64
VOICE_DEBUG_LOGGER_TTL_SECONDS = 60 * 60.0
_LOGGERS: OrderedDict[str, tuple[logging.Logger, float]] = OrderedDict()


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return {"bytes": len(value)}
    if isinstance(value, BaseException):
        return f"{type(value).__name__}: {value}"
    return str(value)


def _safe_channel(channel: str) -> str:
    normalized = _CHANNEL_PATTERN.sub("-", str(channel or "voice")).strip("-._")
    return normalized[:60] or "voice"


def _log_dir() -> Path:
    configured = environment().get("OMNIX_VOICE_DEBUG_LOG_DIR", "").strip()
    return Path(configured).expanduser() if configured else Path(LOGS_DIR)


def voice_debug_log_path(channel: str) -> str:
    """Return the absolute log path for one diagnostic channel in this process.

    One file per process (``voice-debug-<channel>.<process>.log``): gateway
    replicas share the backend channel, and a rotating file shared between
    processes loses lines when one of them rotates it (WP-10.1).
    """
    from app.observability.logging import process_log_name

    name = f"voice-debug-{_safe_channel(channel)}.{process_log_name()}.log"
    return str((_log_dir() / name).resolve())


def _close_logger(logger: logging.Logger) -> None:
    for handler in tuple(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


def _prune_loggers_locked(now: float) -> None:
    expired = [
        channel
        for channel, (_logger_value, expires_at) in _LOGGERS.items()
        if expires_at <= now
    ]
    for channel in expired:
        entry = _LOGGERS.pop(channel, None)
        if entry is not None:
            _close_logger(entry[0])


def clear_voice_debug_loggers() -> None:
    """Close cached per-channel handlers for tests and controlled resets."""

    with _LOGGER_LOCK:
        loggers = [logger for logger, _expires_at in _LOGGERS.values()]
        _LOGGERS.clear()
    for logger in loggers:
        _close_logger(logger)


def _logger(channel: str) -> logging.Logger:
    safe_channel = _safe_channel(channel)
    with _LOGGER_LOCK:
        now = time.monotonic()
        _prune_loggers_locked(now)
        existing = _LOGGERS.get(safe_channel)
        if existing is not None:
            _LOGGERS[safe_channel] = (
                existing[0],
                now + VOICE_DEBUG_LOGGER_TTL_SECONDS,
            )
            _LOGGERS.move_to_end(safe_channel)
            return existing[0]
        if len(_LOGGERS) >= MAX_VOICE_DEBUG_LOGGERS:
            _old_channel, (old_logger, _expires_at) = _LOGGERS.popitem(last=False)
            _close_logger(old_logger)
        path = Path(voice_debug_log_path(safe_channel))
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            path,
            maxBytes=VOICE_DEBUG_LOG_MAX_BYTES,
            backupCount=VOICE_DEBUG_LOG_BACKUP_COUNT,
            encoding="utf-8",
            delay=False,
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger = logging.Logger(f"omnix.voice_debug.{safe_channel}.{os.getpid()}")
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        _LOGGERS[safe_channel] = (logger, now + VOICE_DEBUG_LOGGER_TTL_SECONDS)
        return logger


# A per-process key: an unsalted hash of a short utterance can be reversed by
# hashing guesses, while a keyed one still correlates requests within a run.
_FINGERPRINT_KEY = secrets.token_bytes(32)


def text_fingerprint(text: str) -> str:
    """Return a short keyed fingerprint for correlating TTS requests within this process."""
    return hmac.new(_FINGERPRINT_KEY, str(text or "").encode("utf-8"), hashlib.sha256).hexdigest()[:16]


def voice_debug_log(
    channel: str,
    event: str,
    *,
    trace_id: str = "",
    **details: Any,
) -> None:
    """Append one JSON diagnostic record without persisting speech content."""
    if environment().get("OMNIX_VOICE_DEBUG_LOGGING", "1").strip().lower() in {"0", "false", "off", "no"}:
        return
    record = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "monotonic_ms": round(time.perf_counter_ns() / 1_000_000, 3),
        "sequence": next(_SEQUENCE),
        "channel": _safe_channel(channel),
        "event": str(event or "diagnostic")[:160],
        "trace_id": str(trace_id or "")[:180],
        "process_id": os.getpid(),
        "thread_name": threading.current_thread().name,
        "thread_id": threading.get_ident(),
        **details,
    }
    try:
        serialized = json.dumps(record, ensure_ascii=False, sort_keys=True, default=_json_default)
        _logger(channel).info(serialized)
    except (OSError, TypeError, ValueError):
        # Diagnostics must never interrupt speech generation or playback.
        return
