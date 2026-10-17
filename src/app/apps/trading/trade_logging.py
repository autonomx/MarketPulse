"""Structured local audit logging for automated trading and backtests."""
from __future__ import annotations

from app.config.env import environment

import json
import logging
import os
import threading
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Literal

from app.runtime.paths import resources_logs_root


TradeLogChannel = Literal["auto_trading", "backtest"]

_HANDLER_LOCK = threading.RLock()
_HANDLER_MARKER = "_omnix_trade_audit_log_path"
_SENSITIVE_KEY_FRAGMENTS = (
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
)
_DEFAULT_MAX_BYTES = 25 * 1024 * 1024
_DEFAULT_BACKUP_COUNT = 20


def trade_log_dir() -> Path:
    """Return the writable directory for local trade audit logs."""

    override = environment().get("OMNIX_TRADE_LOG_DIR", "").strip()
    if override:
        return Path(override)
    return resources_logs_root() / "trade"


def trade_log_path(channel: TradeLogChannel) -> Path:
    """Return the JSONL path for one trade-audit channel."""

    override_name = (
        "OMNIX_TRADE_AUTO_LOG_PATH"
        if channel == "auto_trading"
        else "OMNIX_TRADE_BACKTEST_LOG_PATH"
    )
    override = environment().get(override_name, "").strip()
    if override:
        return Path(override)
    # One file per process: the gateway and worker processes each rotate their
    # own file, so a rotation never renames a file another process writes.
    return trade_log_dir() / f"{channel}.{trade_log_process_name()}.jsonl"


def trade_log_process_name() -> str:
    """``OMNIX_INSTANCE_NAME`` when set (one per replica), else the process role."""
    from app.observability.logging import process_log_name

    return process_log_name()


def trade_audit_logging_enabled() -> bool:
    return environment().get("OMNIX_TRADE_AUDIT_LOGGING", "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _path_key(path: Path) -> str:
    try:
        return os.path.normcase(str(path.resolve()))
    except (OSError, RuntimeError):
        return os.path.normcase(str(path.absolute()))


def _logger(channel: TradeLogChannel) -> logging.Logger:
    path = trade_log_path(channel)
    path.parent.mkdir(parents=True, exist_ok=True)
    target = _path_key(path)
    logger = logging.getLogger(f"omnix.trading.audit.{channel}")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    with _HANDLER_LOCK:
        for handler in list(logger.handlers):
            marker = getattr(handler, _HANDLER_MARKER, None)
            if marker is not None and marker != target:
                logger.removeHandler(handler)
                handler.close()
        existing = next(
            (
                handler
                for handler in logger.handlers
                if getattr(handler, _HANDLER_MARKER, None) == target
            ),
            None,
        )
        if existing is None:
            handler = RotatingFileHandler(
                path,
                maxBytes=_DEFAULT_MAX_BYTES,
                backupCount=_DEFAULT_BACKUP_COUNT,
                encoding="utf-8",
            )
            setattr(handler, _HANDLER_MARKER, target)
            handler.setLevel(logging.INFO)
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(handler)
    return logger


def _sensitive_key(key: object) -> bool:
    normalized = str(key).strip().lower().replace("-", "_")
    return any(fragment in normalized for fragment in _SENSITIVE_KEY_FRAGMENTS)


def _sanitize(value: Any, *, key: object | None = None) -> Any:
    if key is not None and _sensitive_key(key):
        return "<redacted>"
    if isinstance(value, dict):
        return {str(item_key): _sanitize(item_value, key=item_key) for item_key, item_value in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_sanitize(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _sanitize(model_dump(mode="json"))
        except (TypeError, ValueError):
            return _sanitize(model_dump())
    return value


def trade_log(channel: TradeLogChannel, event: str, **fields: Any) -> None:
    """Append one redacted JSON object without affecting trading on log failure."""

    if not trade_audit_logging_enabled():
        return
    payload = {
        "logged_at": datetime.now(timezone.utc).isoformat(),
        "channel": channel,
        "event": event,
        **_sanitize(fields),
    }
    try:
        _logger(channel).info(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        )
    except (OSError, RuntimeError, ValueError):
        # Diagnostics are deliberately non-authoritative and must never interrupt
        # deterministic strategy/backtest behavior.
        return


__all__ = [
    "TradeLogChannel",
    "trade_audit_logging_enabled",
    "trade_log",
    "trade_log_dir",
    "trade_log_path",
    "trade_log_process_name",
]
