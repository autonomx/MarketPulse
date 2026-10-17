from __future__ import annotations

from app.config.env import env_str

import json
import logging
import os
import threading
import traceback
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Iterator

from app.caching.bounded_cache import bounded_lru_cache
from app.runtime.paths import resources_root

logger = logging.getLogger(__name__)

RPG_DEBUG_ENABLED_ENV = "OMNIX_RPG_DEBUG_LOGS"
RPG_DEBUG_LOG_DIR_ENV = "OMNIX_RPG_LOG_DIR"
RPG_DEBUG_RETENTION_DAYS_ENV = "OMNIX_RPG_LOG_RETENTION_DAYS"
RPG_DEBUG_MAX_FIELD_CHARS_ENV = "OMNIX_RPG_LOG_MAX_FIELD_CHARS"

_DEFAULT_RETENTION_DAYS = 14
_DEFAULT_MAX_FIELD_CHARS = 12_000
_MAX_COLLECTION_ITEMS = 100
_MAX_DEPTH = 7
_LOGGER_NAMES = (
    "app.apps.rpg",
    "app.composition.gateway.rpg",
    "app.apps.rpg.edge.api.feature_routes.rpg_session_routes",
    "app.composition.gateway.rpg_direct_turn_routes",
    "app.apps.rpg.session.jobs.turn_job_mirror",
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
)

_lock = threading.RLock()
_configured = False
_configuring = False
_handler: logging.Handler | None = None
_last_cleanup_date: str | None = None



def rpg_debug_logging_enabled() -> bool:
    value = env_str(RPG_DEBUG_ENABLED_ENV, "1").strip().lower()
    return value not in {"0", "false", "no", "off", "disabled"}


def rpg_debug_log_dir() -> Path:
    override = env_str(RPG_DEBUG_LOG_DIR_ENV, "").strip()
    return _resolved_log_dir(override)


@bounded_lru_cache(max_entries=8, ttl_seconds=3600.0)
def _resolved_log_dir(override: str) -> Path:
    """The log directory, resolved and created once (WP-5.6: a turn logs dozens of events)."""
    directory = Path(override).expanduser().resolve() if override else resources_root() / "logs" / "rpg"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def rpg_debug_log_status() -> dict[str, Any]:
    directory = rpg_debug_log_dir()
    files: list[dict[str, Any]] = []
    if directory.is_dir():
        for path in sorted(directory.glob("*")):
            if not path.is_file():
                continue
            try:
                stat = path.stat()
                files.append(
                    {
                        "name": path.name,
                        "size_bytes": stat.st_size,
                        "updated_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                    }
                )
            except OSError:
                continue
    return {
        "enabled": rpg_debug_logging_enabled(),
        "directory": str(directory),
        "retention_days": _retention_days(),
        "max_field_chars": _max_field_chars(),
        "files": files,
    }


def configure_rpg_debug_logging(*, force: bool = False) -> Path:
    global _configured, _configuring, _handler
    directory = rpg_debug_log_dir()
    if not rpg_debug_logging_enabled():
        return directory

    with _lock:
        if _configured and not force:
            return directory
        _configuring = True
        try:
            directory.mkdir(parents=True, exist_ok=True)
            _cleanup_expired_logs(directory)
            if _handler is None:
                _handler = _RpgJsonLogHandler()
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
        finally:
            _configuring = False
    return directory


def new_rpg_trace_id(prefix: str = "rpg") -> str:
    safe_prefix = "".join(ch for ch in str(prefix).lower() if ch.isalnum() or ch in {"-", "_"}) or "rpg"
    return f"{safe_prefix}-{uuid.uuid4().hex}"


def log_rpg_event(
    event: str,
    *,
    category: str = "activity",
    level: str = "info",
    session_id: str | None = None,
    turn_id: str | None = None,
    trace_id: str | None = None,
    duration_ms: float | int | None = None,
    fields: dict[str, Any] | None = None,
    error: BaseException | str | None = None,
    include_traceback: bool = False,
) -> dict[str, Any]:
    if not rpg_debug_logging_enabled():
        return {}
    if not _configured and not _configuring:
        configure_rpg_debug_logging()

    normalized_level = str(level or "info").strip().lower()
    payload: dict[str, Any] = {
        "timestamp": _utc_now(),
        "event": str(event or "rpg.event").strip() or "rpg.event",
        "category": str(category or "activity").strip().lower() or "activity",
        "level": normalized_level,
        "pid": os.getpid(),
        "thread": threading.current_thread().name,
    }
    if session_id:
        payload["session_id"] = str(session_id)
    if turn_id:
        payload["turn_id"] = str(turn_id)
    if trace_id:
        payload["trace_id"] = str(trace_id)
    if duration_ms is not None:
        payload["duration_ms"] = round(float(duration_ms), 3)
    if fields:
        payload["fields"] = _sanitize(fields)
    if error is not None:
        payload["error"] = _error_payload(error, include_traceback=include_traceback)

    _write_event(payload)
    return payload


@contextmanager
def rpg_debug_span(
    event: str,
    *,
    category: str = "performance",
    session_id: str | None = None,
    turn_id: str | None = None,
    trace_id: str | None = None,
    fields: dict[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    span_trace_id = trace_id or new_rpg_trace_id(event)
    started_at = perf_counter()
    mutable_fields = dict(fields or {})
    log_rpg_event(
        f"{event}.started",
        category=category,
        session_id=session_id,
        turn_id=turn_id,
        trace_id=span_trace_id,
        fields=mutable_fields,
    )
    try:
        yield mutable_fields
    except Exception as exc:
        log_rpg_event(
            f"{event}.failed",
            category=category,
            level="error",
            session_id=session_id,
            turn_id=turn_id,
            trace_id=span_trace_id,
            duration_ms=(perf_counter() - started_at) * 1000.0,
            fields=mutable_fields,
            error=exc,
            include_traceback=True,
        )
        raise
    else:
        log_rpg_event(
            f"{event}.completed",
            category=category,
            session_id=session_id,
            turn_id=turn_id,
            trace_id=span_trace_id,
            duration_ms=(perf_counter() - started_at) * 1000.0,
            fields=mutable_fields,
        )


def summarize_session(session: Any) -> dict[str, Any]:
    root = _dict(session)
    manifest = _dict(root.get("manifest"))
    state = _dict(root.get("state"))
    simulation = _dict(root.get("simulation_state")) or state
    runtime = _dict(root.get("runtime_state"))
    scene = _dict(simulation.get("scene")) or _dict(state.get("scene"))
    world = _dict(simulation.get("world")) or _dict(state.get("world"))
    player = _dict(simulation.get("player")) or _dict(state.get("player"))
    combat = _dict(runtime.get("combat_state")) or _dict(simulation.get("combat"))
    inventory = player.get("inventory")
    if not isinstance(inventory, list):
        inventory = simulation.get("inventory")
    narration_jobs = runtime.get("narration_jobs")
    if not isinstance(narration_jobs, list):
        narration_jobs = []
    location = (
        scene.get("location")
        or scene.get("location_name")
        or simulation.get("location")
        or state.get("location")
        or manifest.get("location")
    )
    return _drop_empty(
        {
            "session_id": manifest.get("session_id") or manifest.get("id") or root.get("session_id"),
            "title": manifest.get("title") or root.get("title"),
            "tick": runtime.get("tick") or simulation.get("tick") or state.get("tick"),
            "turn_count": manifest.get("turn_count") or root.get("turn_count"),
            "location": location,
            "world_time": world.get("time") or world.get("world_time"),
            "player_level": player.get("level"),
            "player_hp": player.get("hp") or player.get("health"),
            "inventory_count": len(inventory) if isinstance(inventory, list) else None,
            "combat_active": combat.get("active") if combat else None,
            "narration_jobs": len(narration_jobs),
        }
    )


def summarize_turn_result(result: Any) -> dict[str, Any]:
    root = _dict(result)
    nested = _dict(root.get("result"))
    authoritative = _dict(root.get("authoritative"))
    sources = (root, nested, authoritative)
    timing = _first_dict(sources, "manual_turn_stage_timing", "stage_timing", "timing")
    performance = _first_dict(sources, "performance", "turn_performance", "metrics")
    visible_text = _first_text(
        sources,
        "final_narration",
        "narration",
        "summary",
        "response",
        "content",
    )
    turn_id = _first_value(sources, "turn_id")
    tick = _first_value(sources, "tick")
    session = root.get("session")
    return _drop_empty(
        {
            "ok": root.get("ok"),
            "error": root.get("error"),
            "turn_id": turn_id,
            "tick": tick,
            "action_type": _first_value(sources, "action_type"),
            "semantic_action_type": _first_value(sources, "semantic_action_type"),
            "semantic_family": _first_value(sources, "semantic_family"),
            "outcome": _first_value(sources, "outcome"),
            "narration_status": _first_value(sources, "narration_status"),
            "llm_called": _first_value(sources, "llm_called"),
            "llm_purpose": _first_value(sources, "llm_purpose"),
            "source": _first_value(sources, "source"),
            "visible_text": visible_text,
            "visible_text_chars": len(visible_text) if visible_text else 0,
            "stage_timing": timing,
            "performance": performance,
            "session": summarize_session(session) if isinstance(session, dict) else {},
            "top_level_keys": sorted(root.keys()),
        }
    )


def _write_event(payload: dict[str, Any]) -> None:
    if not payload or not rpg_debug_logging_enabled():
        return
    directory = rpg_debug_log_dir()
    with _lock:
        _cleanup_expired_logs(directory)
        safe_payload = _sanitize(payload)
        line = json.dumps(safe_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        targets = [_dated_log_path(directory, "activity")]
        category = str(payload.get("category") or "")
        if payload.get("duration_ms") is not None or category == "performance":
            targets.append(_dated_log_path(directory, "performance"))
        level = str(payload.get("level") or "").lower()
        if level in {"error", "critical", "exception"} or payload.get("error") is not None:
            targets.append(_dated_log_path(directory, "errors"))
        encoded = line.encode("utf-8", errors="replace")
        for path in dict.fromkeys(targets):
            try:
                fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
            except FileNotFoundError:
                # The directory was removed while running: create it again.
                directory.mkdir(parents=True, exist_ok=True)
                fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
            try:
                os.write(fd, encoded)
            finally:
                os.close(fd)


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
    return _positive_int(env_str(RPG_DEBUG_RETENTION_DAYS_ENV), _DEFAULT_RETENTION_DAYS, minimum=1, maximum=365)


def _max_field_chars() -> int:
    return _positive_int(env_str(RPG_DEBUG_MAX_FIELD_CHARS_ENV), _DEFAULT_MAX_FIELD_CHARS, minimum=256, maximum=250_000)


def _positive_int(value: str | None, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value or "").strip())
    except ValueError:
        return default
    return min(max(parsed, minimum), maximum)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _sanitize(value: Any, *, key: str = "", depth: int = 0) -> Any:
    if any(part in key.lower() for part in _REDACTED_KEY_PARTS):
        return "[redacted]"
    if depth >= _MAX_DEPTH:
        return f"<max-depth:{type(value).__name__}>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        limit = _max_field_chars()
        return value if len(value) <= limit else f"{value[:limit]}…<truncated:{len(value) - limit}>"
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
        listed = [_sanitize(item, key=key, depth=depth + 1) for item in items[:_MAX_COLLECTION_ITEMS]]
        if len(items) > _MAX_COLLECTION_ITEMS:
            listed.append(f"<truncated-items:{len(items) - _MAX_COLLECTION_ITEMS}>")
        return listed
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _sanitize(model_dump(mode="json"), key=key, depth=depth + 1)
        except Exception:
            logger.debug("suppressed error in %s", "_sanitize", exc_info=True)
    return _sanitize(str(value), key=key, depth=depth + 1)


def _error_payload(error: BaseException | str, *, include_traceback: bool) -> dict[str, Any]:
    if isinstance(error, BaseException):
        payload: dict[str, Any] = {
            "type": type(error).__name__,
            "message": str(error),
        }
        if include_traceback:
            payload["traceback"] = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        return _sanitize(payload)
    return {"type": "Error", "message": _sanitize(str(error))}


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _drop_empty(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, "", {}, [])}


def _first_value(sources: tuple[dict[str, Any], ...], *keys: str) -> Any:
    for source in sources:
        for key in keys:
            value = source.get(key)
            if value not in (None, "", {}, []):
                return value
    return None


def _first_dict(sources: tuple[dict[str, Any], ...], *keys: str) -> dict[str, Any]:
    value = _first_value(sources, *keys)
    return _dict(value)


def _first_text(sources: tuple[dict[str, Any], ...], *keys: str) -> str:
    value = _first_value(sources, *keys)
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n\n".join(str(item).strip() for item in value if str(item).strip())
    return ""


class _RpgJsonLogHandler(logging.Handler):
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
            log_rpg_event(
                "python.log",
                category="python",
                level=record.levelname.lower(),
                session_id=str(extras.pop("session_id", "") or "") or None,
                turn_id=str(extras.pop("turn_id", "") or "") or None,
                trace_id=str(extras.pop("trace_id", "") or "") or None,
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


