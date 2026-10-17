"""Durable, bounded JSONL diagnostics for audiobook classification runs."""
from __future__ import annotations

from app.config.env import env_str, environment

import json
import logging
import logging.handlers
import os
import threading
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.runtime.paths import resources_root

logger = logging.getLogger(__name__)


AUDIOBOOK_LOG_DIR = resources_root() / "logs" / "audiobook"
AUDIOBOOK_CLASSIFICATION_LOG_PATH = AUDIOBOOK_LOG_DIR / "classifications.jsonl"
AUDIOBOOK_CLASSIFICATION_LOG_MAX_BYTES = 25_000_000
AUDIOBOOK_CLASSIFICATION_LOG_BACKUP_COUNT = 5
AUDIOBOOK_LOG_DIR_ENV = "OMNIX_AUDIOBOOK_LOG_DIR"
AUDIOBOOK_CLASSIFICATION_LOG_PATH_ENV = "OMNIX_AUDIOBOOK_CLASSIFICATION_LOG_PATH"
_LOGGER_NAME = "omnix.audiobook.classification"
_HANDLER_MARKER = "_omnix_audiobook_classification_log_path"
_LOGGER_LOCK = threading.RLock()
_MAX_STRING_CHARS = 12_000
_MAX_COLLECTION_ITEMS = 200
_MAX_NESTING = 6


def classification_log_dir() -> Path:
    """Return the writable audiobook log directory, honoring test overrides."""
    override = environment().get(AUDIOBOOK_LOG_DIR_ENV, "").strip()
    if override:
        path = Path(override)
    else:
        path = AUDIOBOOK_LOG_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def classification_log_path() -> Path:
    """Return the JSONL classification audit path."""
    override = environment().get(AUDIOBOOK_CLASSIFICATION_LOG_PATH_ENV, "").strip()
    if override:
        path = Path(override)
    else:
        path = classification_log_dir() / AUDIOBOOK_CLASSIFICATION_LOG_PATH.name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _path_key(path: Path) -> str:
    try:
        return os.path.normcase(str(path.resolve()))
    except (OSError, RuntimeError):
        return os.path.normcase(str(path.absolute()))


def classification_logger() -> logging.Logger:
    """Return the rotating logger used by the classification audit stream."""
    path = classification_log_path()
    target = _path_key(path)
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    with _LOGGER_LOCK:
        for handler in list(logger.handlers):
            marker = getattr(handler, _HANDLER_MARKER, None)
            if marker is not None and marker != target:
                logger.removeHandler(handler)
                handler.close()
        existing = next(
            (
                handler for handler in logger.handlers
                if getattr(handler, _HANDLER_MARKER, None) == target
            ),
            None,
        )
        if existing is None:
            handler = logging.handlers.RotatingFileHandler(
                path,
                maxBytes=AUDIOBOOK_CLASSIFICATION_LOG_MAX_BYTES,
                backupCount=AUDIOBOOK_CLASSIFICATION_LOG_BACKUP_COUNT,
                encoding="utf-8",
            )
            setattr(handler, _HANDLER_MARKER, target)
            handler.setLevel(logging.INFO)
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(handler)
    return logger


def _bounded(value: Any, *, depth: int = 0) -> Any:
    """Keep diagnostic records useful without allowing unbounded model payloads."""
    if depth >= _MAX_NESTING:
        return "<max-depth>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, bytes):
        return {"bytes": len(value)}
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        try:
            return _bounded(asdict(value), depth=depth + 1)
        except Exception:
            return str(value)
    if isinstance(value, str):
        if len(value) <= _MAX_STRING_CHARS:
            return value
        return value[:_MAX_STRING_CHARS] + "…<truncated>"
    if isinstance(value, dict):
        items = list(value.items())[:_MAX_COLLECTION_ITEMS]
        result = {
            str(key): _bounded(item, depth=depth + 1)
            for key, item in items
        }
        if len(value) > len(items):
            result["_truncated_items"] = len(value) - len(items)
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)[:_MAX_COLLECTION_ITEMS]
        result = [_bounded(item, depth=depth + 1) for item in items]
        if len(value) > len(items):
            result.append(f"<truncated {len(value) - len(items)} items>")
        return result
    if hasattr(value, "model_dump"):
        try:
            return _bounded(value.model_dump(mode="json"), depth=depth + 1)
        except Exception:
            logger.debug("suppressed error in %s", "_bounded", exc_info=True)
    return str(value)


def classification_log(event: str, **details: Any) -> None:
    """Write one durable JSONL audit record without affecting classification."""
    record = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "monotonic_ms": round(time.perf_counter_ns() / 1_000_000, 3),
        "process_id": os.getpid(),
        "thread_name": threading.current_thread().name,
        "event": str(event or "classification_event")[:160],
        **{str(key): _bounded(value) for key, value in details.items()},
    }
    try:
        payload = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
        classification_logger().info(payload)
    except Exception:
        # Diagnostics must never turn a recoverable classification into a failed job.
        logger.debug("suppressed error in %s", "classification_log", exc_info=True)
        return


__all__ = [
    "AUDIOBOOK_CLASSIFICATION_LOG_MAX_BYTES",
    "AUDIOBOOK_CLASSIFICATION_LOG_BACKUP_COUNT",
    "AUDIOBOOK_CLASSIFICATION_LOG_PATH",
    "classification_log",
    "classification_log_dir",
    "classification_log_path",
    "classification_logger",
]
