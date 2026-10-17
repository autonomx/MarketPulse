"""Structured diagnostics for chat TTS streaming."""
from __future__ import annotations

import atexit
import itertools
import json
import logging
import logging.handlers
import os
import queue
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.runtime.paths import LOGS_DIR

from app.observability.content_free_diagnostics import sanitize_content_free_details
from app.observability.resilient_rotating_file_handler import ResilientRotatingFileHandler

logger = logging.getLogger(__name__)

TTS_STREAM_LOG_PATH = Path(LOGS_DIR) / "tts-streaming.log"
TTS_STREAM_LOG_MAX_BYTES = 25_000_000
TTS_STREAM_LOG_BACKUP_COUNT = 4
_MAX_ACTIVE_STREAMS = 4096
_ACTIVE_STREAM_TTL_SECONDS = 4 * 60 * 60
_STREAM_ID_PATTERN = re.compile(r"[^A-Za-z0-9_.-]+")
_SEQUENCE = itertools.count(1)
_ACTIVE_LOCK = threading.Lock()
_ACTIVE_STREAMS: dict[str, float] = {}
_LAST_PCM_SUCCESS_AT: float | None = None
_COMPLETED_PCM_STREAMS = 0


def _json_default(value: Any) -> Any:
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            logger.debug("suppressed error in %s", "_json_default", exc_info=True)
    if isinstance(value, bytes):
        return {"bytes": len(value)}
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _create_logger() -> tuple[logging.Logger, logging.handlers.QueueListener]:
    TTS_STREAM_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    file_handler = ResilientRotatingFileHandler(
        TTS_STREAM_LOG_PATH,
        maxBytes=TTS_STREAM_LOG_MAX_BYTES,
        backupCount=TTS_STREAM_LOG_BACKUP_COUNT,
        encoding="utf-8",
        delay=False,
    )
    file_handler.setFormatter(logging.Formatter("%(message)s"))
    record_queue: queue.SimpleQueue[logging.LogRecord] = queue.SimpleQueue()
    queue_handler = logging.handlers.QueueHandler(record_queue)
    logger = logging.getLogger("omnix.tts.streaming")
    logger.handlers.clear()
    logger.addHandler(queue_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    listener = logging.handlers.QueueListener(record_queue, file_handler, respect_handler_level=True)
    listener.start()
    return logger, listener


_LOGGER, _LISTENER = _create_logger()
atexit.register(_LISTENER.stop)


def diagnostics_log_path() -> str:
    """Return the absolute persistent diagnostics path."""
    return str(TTS_STREAM_LOG_PATH.resolve())


def normalize_stream_id(value: Any = None) -> str:
    """Return a bounded log-safe stream correlation identifier."""
    candidate = _STREAM_ID_PATTERN.sub("-", str(value or "")).strip("-._")[:80]
    return candidate or f"tts-{uuid.uuid4().hex}"


def stream_log(stream_id: str, source: str, event: str, **details: Any) -> None:
    """Queue one content-free JSON-line record without blocking audio work."""
    global _LAST_PCM_SUCCESS_AT, _COMPLETED_PCM_STREAMS
    if source == 'server' and event == 'done_control_sent' and not details.get('partial') and details.get('sent_frames', 0) > 0:
        with _ACTIVE_LOCK:
            _LAST_PCM_SUCCESS_AT = time.perf_counter()
            _COMPLETED_PCM_STREAMS += 1
    record = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "monotonic_ms": round(time.perf_counter_ns() / 1_000_000, 3),
        "sequence": next(_SEQUENCE),
        "stream_id": stream_id,
        "source": source,
        "event": event,
        "process_id": os.getpid(),
        "thread_name": threading.current_thread().name,
        "thread_id": threading.get_ident(),
        **sanitize_content_free_details(details),
    }
    try:
        _LOGGER.info(json.dumps(record, ensure_ascii=False, sort_keys=True, default=_json_default))
    except Exception:
        _LOGGER.exception(
            json.dumps(
                {
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                    "stream_id": record.get("stream_id"),
                    "source": "diagnostics",
                    "event": "serialization_failed",
                },
                sort_keys=True,
            )
        )


def _stream_now() -> float:
    return time.perf_counter()


def active_streams_snapshot() -> dict[str, float]:
    """Return current stream IDs and ages without exposing request text."""
    now = _stream_now()
    with _ACTIVE_LOCK:
        _prune_active_streams_locked(now)
        return {
            active_id: round((now - started_at) * 1000, 3)
            for active_id, started_at in sorted(_ACTIVE_STREAMS.items())
        }


def runtime_stream_snapshot() -> dict[str, Any]:
    """Bounded process counters, without stream IDs or speech content."""
    now = _stream_now()
    with _ACTIVE_LOCK:
        _prune_active_streams_locked(now)
        return {'active_streams': len(_ACTIVE_STREAMS), 'completed_pcm_streams': _COMPLETED_PCM_STREAMS,
                'last_successful_pcm_request_age_seconds': time.perf_counter() - _LAST_PCM_SUCCESS_AT if _LAST_PCM_SUCCESS_AT is not None else None}


def begin_stream(stream_id: str, **details: Any) -> int:
    """Register a live stream and log overlap information."""
    now = _stream_now()
    with _ACTIVE_LOCK:
        _prune_active_streams_locked(now)
        if stream_id not in _ACTIVE_STREAMS and len(_ACTIVE_STREAMS) >= _MAX_ACTIVE_STREAMS:
            oldest_id = min(_ACTIVE_STREAMS, key=_ACTIVE_STREAMS.__getitem__)
            _ACTIVE_STREAMS.pop(oldest_id, None)
        previous = {
            active_id: round((now - started_at) * 1000, 3)
            for active_id, started_at in _ACTIVE_STREAMS.items()
        }
        _ACTIVE_STREAMS[stream_id] = now
        active_count = len(_ACTIVE_STREAMS)
    stream_log(
        stream_id,
        "server",
        "stream_registered",
        active_stream_count=active_count,
        preexisting_active_streams=previous,
        **details,
    )
    return active_count


def clear_active_streams() -> None:
    """Clear live stream diagnostics during shutdown and tests."""
    with _ACTIVE_LOCK:
        _ACTIVE_STREAMS.clear()


def _prune_active_streams_locked(now: float) -> None:
    expired = [
        stream_id
        for stream_id, started_at in _ACTIVE_STREAMS.items()
        if now - started_at > _ACTIVE_STREAM_TTL_SECONDS
    ]
    for stream_id in expired:
        _ACTIVE_STREAMS.pop(stream_id, None)


def end_stream(stream_id: str, **details: Any) -> int:
    """Unregister a stream and record its lifetime and remaining overlap."""
    now = _stream_now()
    with _ACTIVE_LOCK:
        started_at = _ACTIVE_STREAMS.pop(stream_id, None)
        remaining = {
            active_id: round((now - active_started_at) * 1000, 3)
            for active_id, active_started_at in _ACTIVE_STREAMS.items()
        }
        active_count = len(_ACTIVE_STREAMS)
    stream_log(
        stream_id,
        "server",
        "stream_unregistered",
        active_stream_count=active_count,
        stream_lifetime_ms=round((now - started_at) * 1000, 3) if started_at is not None else None,
        remaining_active_streams=remaining,
        **details,
    )
    return active_count


stream_log(
    "diagnostics",
    "diagnostics",
    "logger_ready",
    log_path=diagnostics_log_path(),
    max_bytes=TTS_STREAM_LOG_MAX_BYTES,
    backup_count=TTS_STREAM_LOG_BACKUP_COUNT,
)
