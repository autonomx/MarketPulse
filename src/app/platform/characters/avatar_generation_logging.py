"""Persistent diagnostics for Character avatar generation failures."""
from __future__ import annotations

from app.config.env import env_str, environment

import json
import logging
import os
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from app.runtime.paths import resources_root

_LOGGER_NAME = "omnix.character-avatar-generation"
_HANDLER_LOCK = threading.RLock()
_HANDLER_MARKER = "_omnix_avatar_generation_log_path"


def avatar_generation_log_path() -> Path:
    """Return the writable avatar-generation diagnostic log path."""

    override = environment().get("OMNIX_AVATAR_GENERATION_LOG_PATH", "").strip()
    if override:
        return Path(override)
    return resources_root() / "logs" / "avatar_generation.log"


def avatar_generation_logger() -> logging.Logger:
    """Return a process-safe rotating logger rooted under ``resources/logs``."""

    path = avatar_generation_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    target = _path_key(path)
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    with _HANDLER_LOCK:
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
                maxBytes=5 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
            setattr(handler, _HANDLER_MARKER, target)
            handler.setLevel(logging.INFO)
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s %(levelname)s %(name)s %(message)s",
                )
            )
            logger.addHandler(handler)
    return logger


def avatar_generation_payload(**fields: Any) -> str:
    """Serialize stable request/result context for one diagnostic line."""

    return json.dumps(fields, ensure_ascii=False, sort_keys=True, default=str)


def _path_key(path: Path) -> str:
    try:
        return os.path.normcase(str(path.resolve()))
    except (OSError, RuntimeError):
        return os.path.normcase(str(path.absolute()))


__all__ = [
    "avatar_generation_log_path",
    "avatar_generation_logger",
    "avatar_generation_payload",
]
