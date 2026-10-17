"""Logging helpers for recoverable agent-runtime failures."""

from __future__ import annotations

import logging


_LOGGER = logging.getLogger("app.platform.agent_runtime")


def log_recovered_exception(
    operation: str,
    error: BaseException,
    *,
    level: int | str = logging.WARNING,
) -> None:
    """Keep fallback behavior visible without logging user or model payloads."""

    severity = getattr(logging, level.upper()) if isinstance(level, str) else level
    _LOGGER.log(
        severity,
        "Agent runtime recovered after %s failed (%s)",
        operation,
        type(error).__name__,
        exc_info=(type(error), error, error.__traceback__),
    )
