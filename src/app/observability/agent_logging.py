"""Structured diagnostics for coding-agent lifecycle and failure analysis.

The agent runtime has two useful timelines:

* the process/RPC timeline, which is local to the worker that launched Pi; and
* the durable event timeline, which is the authority used by supervision and
  recovery.

This module records both timelines as JSONL under ``resources/logs/agent``.
Logging is deliberately observational: failures while writing diagnostics are
swallowed so a full disk or unavailable log directory cannot change agent
authority or lifecycle behavior. Because these traces can contain user prompts,
repository content, and tool output, collection is opt-in rather than enabled by
default and log files are created owner-readable/writable only.
"""
from __future__ import annotations

from app.config.env import env_str

from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import threading
import traceback
from typing import Any

from app.runtime.paths import resources_logs_root


AGENT_DEBUG_ENABLED_ENV = "OMNIX_AGENT_DEBUG_LOGS"
AGENT_DEBUG_LOG_DIR_ENV = "OMNIX_AGENT_LOG_DIR"
AGENT_DEBUG_RETENTION_DAYS_ENV = "OMNIX_AGENT_LOG_RETENTION_DAYS"
AGENT_DEBUG_MAX_FIELD_CHARS_ENV = "OMNIX_AGENT_LOG_MAX_FIELD_CHARS"

_DEFAULT_RETENTION_DAYS = 30
_DEFAULT_MAX_FIELD_CHARS = 12_000
_MAX_COLLECTION_ITEMS = 100
_MAX_DEPTH = 7
_LOGGER_NAMES = (
    "app.platform.agent_runtime",
    "app.composition.gateway.agent_runtime",
    "app.composition.gateway.agent",
)
_STANDARD_LOG_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__)
_REDACTED_KEY_PARTS = (
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "private_key",
)
_PRIVATE_KEY_PARTS = (
    "chain_of_thought",
    "thinking",
    "scratchpad",
    "private_reasoning",
)

_lock = threading.RLock()
_configured = False
_configuring = False
_handler: logging.Handler | None = None
_last_cleanup_date: str | None = None
_LOGGER = logging.getLogger(__name__)


def agent_debug_logging_enabled() -> bool:
    value = (env_str(AGENT_DEBUG_ENABLED_ENV, "0") or "0").strip().lower()
    return value not in {"0", "false", "no", "off", "disabled"}


def agent_debug_log_dir() -> Path:
    override = (env_str(AGENT_DEBUG_LOG_DIR_ENV, "") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return resources_logs_root() / "agent"


def agent_debug_log_status() -> dict[str, Any]:
    directory = agent_debug_log_dir()
    files: list[dict[str, Any]] = []
    if directory.is_dir():
        for path in sorted(directory.glob("*.jsonl")):
            try:
                stat = path.stat()
            except OSError:
                continue
            files.append(
                {
                    "name": path.name,
                    "size_bytes": stat.st_size,
                    "updated_at": datetime.fromtimestamp(
                        stat.st_mtime, timezone.utc
                    ).isoformat(),
                }
            )
    return {
        "enabled": agent_debug_logging_enabled(),
        "directory": str(directory),
        "retention_days": _retention_days(),
        "max_field_chars": _max_field_chars(),
        "files": files,
    }


def configure_agent_debug_logging(*, force: bool = False) -> Path:
    """Create the agent log directory and capture agent-runtime logger records."""

    global _configured, _configuring, _handler
    directory = agent_debug_log_dir()
    if not agent_debug_logging_enabled():
        return directory

    with _lock:
        if _configured and not force:
            return directory
        _configuring = True
        try:
            directory.mkdir(parents=True, exist_ok=True)
            _cleanup_expired_logs(directory)
            if _handler is None:
                _handler = _AgentJsonLogHandler()
            for logger_name in _LOGGER_NAMES:
                logger = logging.getLogger(logger_name)
                logger.setLevel(logging.DEBUG)
                if _handler not in logger.handlers:
                    logger.addHandler(_handler)
            _configured = True
            _write_event(
                {
                    "timestamp": _utc_now(),
                    "event": "runtime.logging_configured",
                    "category": "lifecycle",
                    "level": "info",
                    "pid": os.getpid(),
                    "thread": threading.current_thread().name,
                    "fields": {
                        "directory": str(directory),
                        "retention_days": _retention_days(),
                        "max_field_chars": _max_field_chars(),
                    },
                }
            )
        except Exception:
            _LOGGER.warning(
                "Agent debug log setup failed; diagnostics remain disabled",
                exc_info=True,
            )
            pass
        finally:
            _configuring = False
    return directory


def log_agent_activity(
    event: str,
    *,
    category: str = "activity",
    level: str = "info",
    run_id: str | None = None,
    duration_ms: float | int | None = None,
    fields: dict[str, Any] | None = None,
    error: BaseException | str | None = None,
    include_traceback: bool = False,
) -> dict[str, Any]:
    """Write one sanitized activity record and return the in-memory record."""

    if not agent_debug_logging_enabled():
        return {}
    if not _configured and not _configuring:
        configure_agent_debug_logging()

    payload: dict[str, Any] = {
        "timestamp": _utc_now(),
        "event": str(event or "agent.event").strip() or "agent.event",
        "category": str(category or "activity").strip().lower() or "activity",
        "level": str(level or "info").strip().lower() or "info",
        "pid": os.getpid(),
        "thread": threading.current_thread().name,
    }
    if run_id:
        payload["run_id"] = str(run_id)
    if duration_ms is not None:
        try:
            payload["duration_ms"] = round(float(duration_ms), 3)
        except (TypeError, ValueError):
            payload["duration_ms"] = _sanitize(duration_ms)
    if fields:
        payload["fields"] = _sanitize(fields)
    if error is not None:
        payload["error"] = _error_payload(
            error,
            include_traceback=include_traceback,
        )
    _write_event(payload)
    return payload


def _write_event(payload: dict[str, Any]) -> None:
    if not payload or not agent_debug_logging_enabled():
        return
    try:
        directory = agent_debug_log_dir()
        with _lock:
            directory.mkdir(parents=True, exist_ok=True)
            _cleanup_expired_logs(directory)
            safe_payload = _sanitize(payload)
            line = (
                json.dumps(
                    safe_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                )
                + "\n"
            )
            targets = [_dated_log_path(directory, "activity")]
            category = str(payload.get("category") or "")
            level = str(payload.get("level") or "").lower()
            if payload.get("duration_ms") is not None or category == "performance":
                targets.append(_dated_log_path(directory, "performance"))
            if level in {"error", "critical", "exception"} or payload.get("error") is not None:
                targets.append(_dated_log_path(directory, "errors"))
            run_id = str(payload.get("run_id") or "").strip()
            if run_id:
                targets.append(str(directory / f"run-{_safe_filename(run_id)}.jsonl"))
            encoded = line.encode("utf-8", errors="replace")
            for path in dict.fromkeys(str(item) for item in targets):
                fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
                try:
                    os.write(fd, encoded)
                finally:
                    os.close(fd)
    except Exception:
        _LOGGER.warning(
            "Agent debug event could not be written; runtime behavior is unchanged",
            exc_info=True,
        )
        return


def _dated_log_path(directory: Path, kind: str) -> str:
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return str(directory / f"{kind}-{date}.jsonl")


def _cleanup_expired_logs(directory: Path) -> None:
    global _last_cleanup_date
    today = datetime.now(timezone.utc).date()
    today_key = today.isoformat()
    if _last_cleanup_date == today_key:
        return
    cutoff = today - timedelta(days=_retention_days())
    for path in directory.glob("*.jsonl"):
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).date()
            if modified < cutoff:
                path.unlink(missing_ok=True)
        except OSError:
            continue
    _last_cleanup_date = today_key


def _retention_days() -> int:
    return _positive_int(
        env_str(AGENT_DEBUG_RETENTION_DAYS_ENV),
        _DEFAULT_RETENTION_DAYS,
        minimum=1,
        maximum=365,
    )


def _max_field_chars() -> int:
    return _positive_int(
        env_str(AGENT_DEBUG_MAX_FIELD_CHARS_ENV),
        _DEFAULT_MAX_FIELD_CHARS,
        minimum=256,
        maximum=250_000,
    )


def _positive_int(value: str | None, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value or "").strip())
    except ValueError:
        return default
    return min(max(parsed, minimum), maximum)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _safe_filename(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value)
    return safe[:180] or "unknown"


def _sanitize(value: Any, *, key: str = "", depth: int = 0) -> Any:
    key_lower = key.lower()
    if any(part in key_lower for part in _REDACTED_KEY_PARTS):
        return "[redacted]"
    if any(part in key_lower for part in _PRIVATE_KEY_PARTS):
        return "[omitted-private-reasoning]"
    if depth >= _MAX_DEPTH:
        return f"<max-depth:{type(value).__name__}>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        limit = _max_field_chars()
        return value if len(value) <= limit else f"{value[:limit]}...<truncated:{len(value) - limit}>"
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    if isinstance(value, dict):
        items = list(value.items())
        output = {
            str(item_key): _sanitize(item_value, key=str(item_key), depth=depth + 1)
            for item_key, item_value in items[:_MAX_COLLECTION_ITEMS]
        }
        if len(items) > _MAX_COLLECTION_ITEMS:
            output["_truncated_items"] = len(items) - _MAX_COLLECTION_ITEMS
        return output
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        cleaned_items = [_sanitize(item, key=key, depth=depth + 1) for item in items[:_MAX_COLLECTION_ITEMS]]
        if len(items) > _MAX_COLLECTION_ITEMS:
            cleaned_items.append(f"<truncated-items:{len(items) - _MAX_COLLECTION_ITEMS}>")
        return cleaned_items
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _sanitize(model_dump(mode="json"), key=key, depth=depth + 1)
        except Exception:
            _LOGGER.debug("Agent debug value serialization failed type=%s", type(value).__name__)
    return _sanitize(str(value), key=key, depth=depth + 1)


def _error_payload(error: BaseException | str, *, include_traceback: bool) -> dict[str, Any]:
    if isinstance(error, BaseException):
        payload: dict[str, Any] = {
            "type": type(error).__name__,
            "message": str(error),
        }
        if include_traceback:
            payload["traceback"] = "".join(
                traceback.format_exception(type(error), error, error.__traceback__)
            )
        return _sanitize(payload)
    return {"type": "Error", "message": _sanitize(str(error))}


class _AgentJsonLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            extras = {
                key: value
                for key, value in record.__dict__.items()
                if key not in _STANDARD_LOG_RECORD_FIELDS and not key.startswith("_")
            }
            error: BaseException | str | None = None
            include_traceback = False
            if record.exc_info and record.exc_info[1]:
                error = record.exc_info[1]
                include_traceback = True
            log_agent_activity(
                "python.log",
                category="python",
                level=record.levelname.lower(),
                run_id=str(extras.pop("run_id", "") or "") or None,
                fields={
                    "logger": record.name,
                    "message": record.getMessage(),
                    "module": record.module,
                    "function": record.funcName,
                    "line": record.lineno,
                    "extras": extras,
                },
                error=error,
                include_traceback=include_traceback,
            )
        except Exception:
            self.handleError(record)


def _reset_agent_debug_logging_for_tests() -> None:
    global _configured, _configuring, _handler, _last_cleanup_date
    with _lock:
        if _handler is not None:
            for logger_name in _LOGGER_NAMES:
                logger = logging.getLogger(logger_name)
                if _handler in logger.handlers:
                    logger.removeHandler(_handler)
        _configured = False
        _configuring = False
        _handler = None
        _last_cleanup_date = None
