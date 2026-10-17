"""Structured diagnostics for live-call text and audio streaming."""
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.conversation.contracts import DeliveryCheckpointRecorder
from app.runtime.paths import LOGS_DIR

from app.observability.resilient_rotating_file_handler import ResilientRotatingFileHandler

logger = logging.getLogger(__name__)

LIVE_VOICE_STREAM_LOG_PATH = Path(LOGS_DIR) / "live-call-streaming.log"
LIVE_VOICE_STREAM_LOG_MAX_BYTES = 25_000_000
LIVE_VOICE_STREAM_LOG_BACKUP_COUNT = 4
_ID_PATTERN = re.compile(r"[^A-Za-z0-9_.:-]+")
_SEQUENCE = itertools.count(1)
_DELIVERY_CHECKPOINT_RECORDER: DeliveryCheckpointRecorder | None = None
_DELIVERY_CHECKPOINT_DISPATCHER: DeliveryCheckpointRecorder | None = None


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
    LIVE_VOICE_STREAM_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    file_handler = ResilientRotatingFileHandler(
        LIVE_VOICE_STREAM_LOG_PATH,
        maxBytes=LIVE_VOICE_STREAM_LOG_MAX_BYTES,
        backupCount=LIVE_VOICE_STREAM_LOG_BACKUP_COUNT,
        encoding="utf-8",
        delay=False,
    )
    file_handler.setFormatter(logging.Formatter("%(message)s"))
    record_queue: queue.SimpleQueue[logging.LogRecord] = queue.SimpleQueue()
    queue_handler = logging.handlers.QueueHandler(record_queue)
    logger = logging.getLogger("omnix.live_voice.streaming")
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
    return str(LIVE_VOICE_STREAM_LOG_PATH.resolve())


def normalize_trace_id(value: Any = None) -> str:
    """Return a bounded log-safe turn or controller correlation identifier."""
    candidate = _ID_PATTERN.sub("-", str(value or "")).strip("-._:")[:120]
    return candidate or "live-call-unscoped"


def configure_delivery_checkpoint_recorder(
    recorder: DeliveryCheckpointRecorder,
) -> None:
    """Connect the voice transport to the chat-owned durable turn service."""
    global _DELIVERY_CHECKPOINT_RECORDER
    _DELIVERY_CHECKPOINT_RECORDER = recorder


def configure_delivery_checkpoint_dispatcher(
    dispatcher: DeliveryCheckpointRecorder | None,
) -> None:
    """Select the bounded queue used to dispatch durable delivery checkpoints."""
    global _DELIVERY_CHECKPOINT_DISPATCHER
    _DELIVERY_CHECKPOINT_DISPATCHER = dispatcher


def live_voice_log(trace_id: str, source: str, event: str, **details: Any) -> None:
    """Queue one JSON-line live-call diagnostics record without blocking audio work."""
    if event == "delivery_checkpoint":
        dispatcher = _DELIVERY_CHECKPOINT_DISPATCHER
        if dispatcher is None:
            _persist_delivery(details)
        else:
            dispatcher(details)
    record = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "monotonic_ms": round(time.perf_counter_ns() / 1_000_000, 3),
        "sequence": next(_SEQUENCE),
        "trace_id": normalize_trace_id(trace_id),
        "source": str(source or "unknown")[:80],
        "event": str(event or "diagnostic")[:160],
        "process_id": os.getpid(),
        "thread_name": threading.current_thread().name,
        "thread_id": threading.get_ident(),
        **details,
    }
    try:
        _LOGGER.info(json.dumps(record, ensure_ascii=False, sort_keys=True, default=_json_default))
    except Exception:
        _LOGGER.exception(
            json.dumps(
                {
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                    "trace_id": normalize_trace_id(trace_id),
                    "source": "diagnostics",
                    "event": "serialization_failed",
                },
                sort_keys=True,
            )
        )


def persist_delivery_checkpoint(details: dict[str, Any]) -> None:
    recorder = _DELIVERY_CHECKPOINT_RECORDER
    if recorder is None:
        return
    try:
        recorder(details)
    except Exception:
        logger.debug("suppressed error in %s", "persist_delivery_checkpoint", exc_info=True)
        return


def _persist_delivery(details: dict[str, Any]) -> None:
    persist_delivery_checkpoint(details)


live_voice_log(
    "live-call-diagnostics",
    "diagnostics",
    "logger_ready",
    log_path=diagnostics_log_path(),
    max_bytes=LIVE_VOICE_STREAM_LOG_MAX_BYTES,
    backup_count=LIVE_VOICE_STREAM_LOG_BACKUP_COUNT,
)
